"""Image-reference positions in user-authored text (Python Unicode character offsets)."""

from corki.protocol.tools import TextContent


def image_label(number):
    return f"[Image #{number}]"


def validate_image_positions(text, positions, count=None):
    positions = tuple(positions)
    if not positions:
        return positions
    if count is not None and len(positions) != count:
        raise ValueError("image positions must match attachments")
    ranges = []
    for number, start in enumerate(positions, 1):
        label = image_label(number)
        if type(start) is not int or start < 0 or text[start : start + len(label)] != label:
            raise ValueError("image marker does not match its saved position")
        ranges.append((start, start + len(label)))
    ranges.sort()
    if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:], strict=False)):
        raise ValueError("image markers overlap")
    return positions


def labelled_image_content(text, images):
    # Codex sends numbered image blocks before the literal, marker-bearing text.
    # No fabricated filesystem path: Corki owns the bytes directly.
    parts = []
    for number, image in enumerate(images, 1):
        parts.extend(
            (TextContent(f"<image name={image_label(number)}>"), image, TextContent("</image>"))
        )
    parts.append(TextContent(text))
    return tuple(parts)
