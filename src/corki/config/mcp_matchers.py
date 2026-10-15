"""Immutable modern MCP executable/argument and URL matchers."""

from dataclasses import dataclass

from corki.config import mcp_regex
from corki.config.features import MCPServerSettings
from corki.config.mcp_shapes import command_matcher_fields, value_matcher_fields


@dataclass(frozen=True, slots=True)
class MCPValueMatcher:
    """The native exact/prefix/regex language, without path or URL normalization."""

    kind: str
    value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or self.kind not in {"exact", "prefix", "regex"}
            or not isinstance(self.value, str)
        ):
            raise ValueError("invalid MCP value matcher")
        if self.kind == "regex":
            mcp_regex.validate(self.value)

    @classmethod
    def from_mapping(cls, value: object) -> "MCPValueMatcher":
        """Typed matcher objects reject unknown fields instead of broadening policy."""
        return cls(*value_matcher_fields(value))

    def matches(self, candidate: str) -> bool:
        """Require full regex matching, or literal exact/prefix comparison."""
        if self.kind == "exact":
            return candidate == self.value
        if self.kind == "prefix":
            return candidate.startswith(self.value)
        return mcp_regex.matches(self.value, candidate)


@dataclass(frozen=True, slots=True)
class MCPCommandMatcher:
    """Exact executable and complete positional argument list."""

    executable: str
    args: tuple[MCPValueMatcher, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.executable, str):
            raise ValueError("MCP executable must be a string")
        object.__setattr__(self, "args", tuple(self.args))
        if not all(isinstance(arg, MCPValueMatcher) for arg in self.args):
            raise ValueError("MCP argument matchers must be typed")

    @classmethod
    def from_mapping(cls, value: object) -> "MCPCommandMatcher":
        """Preserve required args, including an explicitly empty list."""
        executable, args = command_matcher_fields(value)
        return cls(executable, tuple(MCPValueMatcher.from_mapping(v) for v in args))

    def matches(self, settings: MCPServerSettings) -> bool:
        """Extra arguments and executable aliases cannot inherit authorization."""
        return (
            settings.transport == "stdio"
            and settings.command == self.executable
            and len(settings.args) == len(self.args)
            and all(
                matcher.matches(arg) for matcher, arg in zip(self.args, settings.args, strict=True)
            )
        )


@dataclass(frozen=True, slots=True)
class MCPUrlMatcher:
    """A matching URL authorizes the complete HTTP server configuration."""

    matcher: MCPValueMatcher

    def __post_init__(self) -> None:
        if not isinstance(self.matcher, MCPValueMatcher):
            raise ValueError("MCP URL matcher must be typed")

    def matches(self, settings: MCPServerSettings) -> bool:
        """Do not apply URL rules to a stdio executable with a similar string."""
        return settings.transport == "http" and self.matcher.matches(settings.url or "")
