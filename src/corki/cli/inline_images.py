"""Tracked atomic image markers; plain text that resembles a marker is not an attachment."""

from dataclasses import dataclass

from corki.protocol.input_images import image_label, validate_image_positions
from corki.protocol.input_mentions import InputMention
from corki.protocol.tools import ImageAttachment


@dataclass
class Paste:
    label: str
    text: str


@dataclass(frozen=True)
class Mention:
    label: str
    selector: InputMention


@dataclass
class Element:
    start: int
    end: int
    image: ImageAttachment | Paste | Mention


class ImageDraft:
    def __init__(self):
        self.text = ""
        self.elements = []
        self.history = []

    @property
    def images(self):
        return tuple(e.image for e in self.elements if isinstance(e.image, ImageAttachment))

    @property
    def positions(self):
        return tuple(e.start for e in self.elements if isinstance(e.image, ImageAttachment))

    @property
    def pastes(self):
        return tuple(
            (e.start, e.image.label, e.image.text)
            for e in self.elements
            if isinstance(e.image, Paste)
        )

    @property
    def bindings(self):
        return tuple(
            (e.start, e.image.label, e.image.selector)
            for e in sorted(self.elements, key=lambda e: e.start)
            if isinstance(e.image, Mention)
        )

    @property
    def mentions(self):
        return tuple(dict.fromkeys(selector for _, _, selector in self.bindings))

    def bind(self, start, label, selector):
        """Own a selected mention, never infer a binding from a typed display name."""
        end = start + len(label)
        if not label or self.text[start:end] != label or start < 0:
            raise ValueError("mention does not match saved position")
        if any(e.start < end and start < e.end for e in self.elements):
            raise ValueError("mention overlaps another composer element")
        self.elements.append(Element(start, end, Mention(label, selector)))

    def expanded(self):
        text = self.text
        positions = list(self.positions)
        for start, label, payload in sorted(self.pastes, reverse=True):
            end = start + len(label)
            text = text[:start] + payload + text[end:]
            positions = [p + len(payload) - len(label) if p >= end else p for p in positions]
        return text, self.images, tuple(positions)

    def paste(self, text, position, cursor):
        self._remember(cursor)
        base = f"[Pasted Content {len(text)} chars]"
        labels = {label for _, label, _ in self.pastes}
        numbers = [
            1 if label == base else int(label[len(base) + 2 :])
            for label in labels
            if label == base or (label.startswith(base + " #") and label[len(base) + 2 :].isdigit())
        ]
        label = f"{base} #{max(numbers) + 1}" if numbers else base
        self._edit(position, position, label)
        self.elements.append(Element(position, position + len(label), Paste(label, text)))
        self._trim_history()
        return cursor + len(label) if cursor >= position else cursor

    def clear(self, text=""):
        self.text = text
        self.elements.clear()
        self.history.clear()

    def _remember(self, cursor):
        self.history.append(
            (self.text, tuple((e.start, e.end, e.image) for e in self.elements), cursor)
        )
        self._trim_history()

    def _trim_history(self):
        while self.history:
            images = {id(e.image): e.image for e in self.elements}
            for _, entries, _ in self.history:
                images.update((id(image), image) for _, _, image in entries)
            if (
                len(self.history) <= 64
                and sum(len(text) for text, _, _ in self.history) <= 1_000_000
                and sum(
                    len(i.data_url)
                    if isinstance(i, ImageAttachment)
                    else len(i.text)
                    if isinstance(i, Paste)
                    else len(i.label) + len(i.selector.path)
                    for i in images.values()
                )
                <= 44_000_000
            ):
                break
            self.history.pop(0)

    def undo(self):
        if not self.history:
            return None
        self.text, elements, cursor = self.history.pop()
        self.elements = [Element(*element) for element in elements]
        for element in self.elements:
            if element.start < cursor < element.end:
                cursor = element.end
        return min(cursor, len(self.text))

    def _edit(self, start, end, inserted):
        delta = len(inserted) - (end - start)
        survivors = []
        for element in self.elements:
            if element.end <= start:
                survivors.append(element)
            elif element.start >= end:
                element.start += delta
                element.end += delta
                survivors.append(element)
        self.elements = survivors
        self.text = self.text[:start] + inserted + self.text[end:]

    def _relabel(self, cursor):
        # Paste order owns numbering, even when the user inserts image 2 before image 1.
        numbered = {
            id(element): n
            for n, element in enumerate(
                (e for e in self.elements if isinstance(e.image, ImageAttachment)), 1
            )
        }
        for element in sorted(self.elements, key=lambda e: e.start, reverse=True):
            if not isinstance(element.image, ImageAttachment):
                continue
            label = image_label(numbered[id(element)])
            start, end = element.start, element.end
            delta = len(label) - (end - start)
            self.text = self.text[:start] + label + self.text[end:]
            element.end = start + len(label)
            for other in self.elements:
                if other is not element and other.start >= end:
                    other.start += delta
                    other.end += delta
            if cursor >= end:
                cursor += delta
            elif cursor > start:
                cursor = element.end
        return cursor

    def sync(self, text, cursor):
        """Map a single buffer edit; deleting/replacing part of a marker removes it atomically."""
        if text == self.text:
            return cursor
        old = self.text
        start = 0
        # Identical adjacent labels can share a prefix after deleting the first one.
        # The resulting caret disambiguates which owned element was removed.
        prefix_limit = min(len(old), len(text))
        if len(text) < len(old):
            prefix_limit = min(prefix_limit, cursor)
        while start < prefix_limit and old[start] == text[start]:
            start += 1
        suffix = 0
        while suffix < min(len(old), len(text)) - start and old[-1 - suffix] == text[-1 - suffix]:
            suffix += 1
        end, new_end = len(old) - suffix, len(text) - suffix
        self._remember(start)
        inserted = text[start:new_end]
        left, right = start, end
        for element in self.elements:
            if (element.start < end and element.end > start) or (
                start == end and element.start < start < element.end
            ):
                left, right = min(left, element.start), max(right, element.end)
        # Cursor relative to the original new buffer, adjusted for removed marker fragments.
        if cursor >= new_end:
            cursor -= (start - left) + min(cursor - new_end, right - end)
        elif cursor >= start:
            cursor -= start - left
        self._edit(left, right, inserted)
        return max(0, min(len(self.text), self._relabel(cursor)))

    def attach(self, image, position, cursor):
        self._remember(cursor)
        label = image_label(len(self.images) + 1)
        self._edit(position, position, label)
        self.elements.append(Element(position, position + len(label), image))
        self._trim_history()
        return cursor + len(label) if cursor >= position else cursor

    def restore(self, text, images, positions=(), pastes=(), bindings=()):
        positions = validate_image_positions(text, positions, len(images))
        self.clear(text)
        if positions:
            for n, (image, start) in enumerate(zip(images, positions, strict=True), 1):
                label = image_label(n)
                if text[start : start + len(label)] != label:
                    raise ValueError("image marker does not match its saved position")
                self.elements.append(Element(start, start + len(label), image))
        else:
            for image in images:
                self.attach(image, len(self.text), len(self.text))
        for start, label, payload in pastes:
            if self.text[start : start + len(label)] != label:
                raise ValueError("paste marker does not match saved position")
            self.elements.append(Element(start, start + len(label), Paste(label, payload)))
        for start, label, selector in bindings:
            self.bind(start, label, selector)
        self.history.clear()

    def append(self, text, images=(), positions=(), pastes=(), bindings=()):
        other = ImageDraft()
        other.restore(text, images, positions, pastes, bindings)
        used = {label for _, label, _ in self.pastes}
        for element in sorted(other.elements, key=lambda e: e.start):
            if not isinstance(element.image, Paste):
                continue
            label = element.image.label
            if label in used:
                base = f"[Pasted Content {len(element.image.text)} chars]"
                number = 2
                label = f"{base} #{number}"
                while label in used:
                    number += 1
                    label = f"{base} #{number}"
                start, end = element.start, element.end
                delta = len(label) - (end - start)
                other.text = other.text[:start] + label + other.text[end:]
                element.end = start + len(label)
                element.image = Paste(label, element.image.text)
                for following in other.elements:
                    if following is not element and following.start >= end:
                        following.start += delta
                        following.end += delta
            used.add(label)
        offset = len(self.text)
        self.text += other.text
        self.elements.extend(
            Element(e.start + offset, e.end + offset, e.image) for e in other.elements
        )
        self._relabel(len(self.text))
