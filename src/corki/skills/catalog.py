"""Host skill metadata allocation following Codex's bounded catalog renderer.

This budget counts metadata lines, not the whole request. It deliberately uses
UTF-8 bytes/4 for token mode and Unicode characters for the unknown-window fallback.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from corki.prompting import PromptStore
from corki.skills.models import SkillMetadata, SkillScope

_PROMPTS = PromptStore()


@dataclass(frozen=True, slots=True)
class MetadataBudget:
    limit: int
    tokens: bool = True

    @classmethod
    def resolve(cls, window: int | None = None, configured: int | None = None):
        if configured is not None:
            if not isinstance(configured, int) or isinstance(configured, bool) or configured <= 0:
                raise ValueError("skills.max_context_tokens must be a positive integer")
            return cls(min(configured, 10_000))
        return cls(max(1, window * 2 // 100)) if window and window > 0 else cls(8000, False)

    def cost(self, text: str) -> int:
        return (len(text.encode("utf-8")) + 3) // 4 if self.tokens else len(text)


@dataclass(frozen=True, slots=True)
class CatalogReport:
    total_count: int = 0
    included_count: int = 0
    omitted_count: int = 0
    truncated_description_chars: int = 0
    truncated_description_count: int = 0

    @property
    def warning(self) -> str | None:
        if self.omitted_count:
            noun, verb = ("skill", "was") if self.omitted_count == 1 else ("skills", "were")
            return (
                "Exceeded skills context budget. All skill descriptions were removed and "
                f"{self.omitted_count} additional {noun} {verb} not included in the "
                "model-visible skills list."
            )
        if self.total_count and (
            (self.truncated_description_chars + self.total_count - 1) // self.total_count > 100
        ):
            return (
                "Skill descriptions were shortened to fit the skills context budget. "
                "Corki can still see every skill, but some descriptions are shorter. "
                "Disable unused skills or plugins to leave more room for the rest."
            )
        return None


@dataclass(frozen=True, slots=True)
class CatalogRender:
    body: str
    lines: tuple[str, ...]
    roots: tuple[tuple[str, str], ...]
    report: CatalogReport
    metadata_cost: int


def _body(lines: tuple[str, ...], roots: tuple[tuple[str, str], ...] = ()) -> str:
    return "\n" + _PROMPTS.render(
        "extensions/skills/catalog_aliased" if roots else "extensions/skills/catalog_absolute",
        **({"roots": "\n".join(f"- `{name}` = `{path}`" for name, path in roots)} if roots else {}),
        skills="\n".join(lines),
    )


def _line(skill: SkillMetadata, description: str, locator: str) -> str:
    body = f"{description} " if description else ""
    return f"- {skill.qualified_name}: {body}(file: {locator})"


def _allocate(skills, locators, budget):
    descriptions = [
        s.description if len(s.description) <= 1024 else s.description[:1021] + "..."
        for s in skills
    ]
    minimums = [
        budget.cost(_line(s, "", path) + "\n") for s, path in zip(skills, locators, strict=True)
    ]
    full = [
        budget.cost(_line(s, d, path) + "\n")
        for s, d, path in zip(skills, descriptions, locators, strict=True)
    ]
    if sum(full) <= budget.limit:
        return descriptions, [len(d) for d in descriptions]
    if sum(minimums) > budget.limit:
        remaining = budget.limit
        allocations = []
        for cost in minimums:
            included = cost <= remaining
            allocations.append(0 if included else None)
            if included:
                remaining -= cost
        return descriptions, allocations

    # Prefix costs let round-robin growth remain linear in the description size.
    extras = []
    for skill, description, path, minimum in zip(
        skills, descriptions, locators, minimums, strict=True
    ):
        base = _line(skill, "", path) + "\n"
        size = len(base.encode("utf-8")) if budget.tokens else len(base)
        costs = [0]
        prefix = 0
        for char in description:
            prefix += len(char.encode("utf-8")) if budget.tokens else 1
            count = size + prefix + 1  # space between nonempty description and locator
            costs.append(((count + 3) // 4 if budget.tokens else count) - minimum)
        extras.append(costs)
    allocations = [0] * len(skills)
    remaining = budget.limit - sum(minimums)
    while True:
        changed = False
        for i, costs in enumerate(extras):
            current = allocations[i]
            if current + 1 == len(costs):
                continue
            delta = costs[current + 1] - costs[current]
            if delta <= remaining:
                remaining -= delta
                allocations[i] += 1
                changed = True
        if not changed:
            return descriptions, allocations


def _render(skills, locators, budget, roots=(), overhead=0):
    descriptions, allocations = _allocate(skills, locators, budget)
    lines, truncated = [], []
    for skill, path, description, count in zip(
        skills, locators, descriptions, allocations, strict=True
    ):
        truncated.append(len(description) - (count or 0))
        if count is not None:
            lines.append(_line(skill, description[:count], path))
    lines = tuple(lines)
    return CatalogRender(
        _body(lines, roots),
        lines,
        roots,
        CatalogReport(
            len(skills),
            len(lines),
            len(skills) - len(lines),
            sum(truncated),
            sum(count > 0 for count in truncated),
        ),
        overhead + sum(budget.cost(line + "\n") for line in lines),
    )


def _plugin_bases(root: Path) -> tuple[Path, Path] | None:
    for marketplace in (root, *root.parents):
        parent = marketplace.parent
        if parent.name == "cache" and parent.parent.name == "plugins":
            parts = root.relative_to(marketplace).parts
            return (marketplace, marketplace.joinpath(*parts[:2])) if len(parts) >= 2 else None
    return None


def _roots(skills):
    bases = [_plugin_bases(skill.root) for skill in skills]
    counts = Counter(base[1] for base in bases if base is not None)
    values = dict.fromkeys(
        str(base[0] if base and counts[base[1]] <= 1 else skill.root).replace("\\", "/")
        for skill, base in zip(skills, bases, strict=True)
    )
    return tuple((f"r{i}", value) for i, value in enumerate(values))


def shorten(locator: str, roots: tuple[tuple[str, str], ...]) -> str:
    matches = [(name, root) for name, root in roots if locator.startswith(root.rstrip("/") + "/")]
    if not matches:
        return locator
    name, root = max(matches, key=lambda entry: len(entry[1]))
    return name + "/" + locator[len(root.rstrip("/")) + 1 :]


def render_catalog(skills: tuple[SkillMetadata, ...], budget: MetadataBudget) -> CatalogRender:
    if not skills:
        return CatalogRender("", (), (), CatalogReport(), 0)
    roots = _roots(skills)  # discovery order, distinct from display order
    ranks = {SkillScope.SYSTEM: 0, SkillScope.PROJECT: 2, SkillScope.USER: 3}
    skills = tuple(
        sorted(skills, key=lambda s: (ranks.get(s.scope, 4), s.qualified_name, str(s.path)))
    )
    locators = tuple(str(skill.discovery_path or skill.path).replace("\\", "/") for skill in skills)
    absolute = _render(skills, locators, budget)
    overhead = max(0, budget.cost(_body((), roots)) - budget.cost(_body(())))
    if overhead >= budget.limit:
        return absolute
    aliased = _render(
        skills,
        tuple(shorten(path, roots) for path in locators),
        MetadataBudget(budget.limit - overhead, budget.tokens),
        roots,
        overhead,
    )

    def score(rendered):
        return (
            rendered.report.included_count,
            -rendered.report.truncated_description_chars,
            -rendered.metadata_cost,
        )

    return aliased if score(aliased) > score(absolute) else absolute
