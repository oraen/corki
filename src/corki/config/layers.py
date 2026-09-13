"""Local config discovery and admission, separate from active-project selection.

The layer state travels with settings so context and execution use the same
admitted sources. Managed requirements remain a separate host authority.
"""

import os
import stat
import tomllib
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path

from corki.config.project_trust import lookup_project, project_configurations, trust_git_root
from corki.config.shell_environment import parse_shell_environment_policy

# Native project-local denylist, with Corki's provider registry spellings.
# An execution compiler is an embedding-host executable, not repository policy.
_PROJECT_DENIED = (
    "openai_base_url",
    "chatgpt_base_url",
    "apps_mcp_product_sku",
    "responses_api_metadata",
    "model_provider",
    "model_providers",
    "notify",
    "profile",
    "profiles",
    "experimental_realtime_webrtc_call_base_url",
    "experimental_realtime_ws_base_url",
    "otel",
    "provider",
    "providers",
)


@dataclass(frozen=True, slots=True)
class ConfigLayer:
    file: Path
    kind: str
    disabled_reason: str | None = None
    contents: str = field(default="", repr=False)
    hooks_json: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.file, Path) or not self.file.is_absolute():
            raise ValueError("config layer file must be an absolute host path")
        if self.kind not in {"user", "project"}:
            raise ValueError("invalid local config layer kind")
        if self.disabled_reason is not None and not isinstance(self.disabled_reason, str):
            raise ValueError("invalid config layer disabled reason")
        if not isinstance(self.contents, str):
            raise ValueError("config layer contents must be an immutable TOML snapshot")
        if self.hooks_json is not None and not isinstance(self.hooks_json, str):
            raise ValueError("hook file contents must be an immutable JSON snapshot")


@dataclass(frozen=True, slots=True)
class LocalConfigState:
    layers: tuple[ConfigLayer, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "layers", tuple(self.layers))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        if any(not isinstance(layer, ConfigLayer) for layer in self.layers):
            raise ValueError("config layers must be host-owned")
        if any(not isinstance(warning, str) for warning in self.warnings):
            raise ValueError("config warnings must be text")

    def rule_folders(self, fallback_home: Path) -> tuple[Path, ...]:
        folders = tuple(layer.file.parent for layer in self.layers if layer.disabled_reason is None)
        return (
            folders
            if any(layer.kind == "user" for layer in self.layers)
            else (fallback_home, *folders)
        )


def _merge(base: dict, overlay: dict, path: tuple[str, ...] = ()) -> None:
    if path == ("shell_environment_policy",):
        displaced = (
            ("exclude", "include_only")
            if "filters" in overlay
            else ("filters",)
            if "exclude" in overlay or "include_only" in overlay
            else ()
        )
        for key in displaced:
            base.pop(key, None)
    if path == ("shell_environment_policy", "filters"):
        normalized = {key.lower(): value for key, value in base.items()}
        base.clear()
        base.update(normalized)
        overlay = {key.lower(): value for key, value in overlay.items()}
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value, (*path, key))
        else:
            base[key] = deepcopy(value)


def _validate_filters(document: dict) -> None:
    policy = document.get("shell_environment_policy")
    if isinstance(policy, dict):
        parse_shell_environment_policy(
            {key: policy[key] for key in ("filters", "exclude", "include_only") if key in policy}
        )


def _normalize_layer(document: dict, base: Path) -> None:
    """Normalize aliases and source-owned paths before applying precedence."""
    # Normalize aliases in each source before precedence is applied. A higher
    # legacy spelling must override a lower canonical spelling, not coexist.
    for table, legacy, canonical in (
        ("memories", "no_memories_if_mcp_or_web_search", "disable_on_external_context"),
        ("agents", "max_threads", "max_concurrent_threads_per_session"),
    ):
        values = document.get(table)
        if isinstance(values, dict) and legacy in values:
            value = values.pop(legacy)
            values.setdefault(canonical, value)

    def absolute(value):
        return str(Path(os.path.abspath(base / Path(value).expanduser())))

    instructions_file = document.get("model_instructions_file")
    if isinstance(instructions_file, str) and instructions_file:
        document["model_instructions_file"] = absolute(instructions_file)

    plugins = document.get("plugins")
    if isinstance(plugins, dict) and isinstance(plugins.get("directories"), list):
        plugins["directories"] = [
            absolute(v) if isinstance(v, str) else v for v in plugins["directories"]
        ]
    skills = document.get("skills")
    if isinstance(skills, dict) and isinstance(skills.get("config"), list):
        for entry in skills["config"]:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                entry["path"] = absolute(entry["path"])
    mcp = document.get("mcp")
    if isinstance(mcp, dict) and isinstance(mcp.get("servers"), dict):
        for server in mcp["servers"].values():
            if (
                isinstance(server, dict)
                and server.get("environment_id", "local") == "local"
                and isinstance(server.get("cwd"), str)
                and server["cwd"]
            ):
                server["cwd"] = absolute(server["cwd"])
    # Permission workspace-root patterns deliberately remain policy-cwd relative,
    # like upstream compile_workspace_roots; they are not config-file paths.


def _project_root(cwd: Path, markers: tuple[str, ...]) -> Path:
    for directory in (cwd, *cwd.parents):
        for marker in markers:
            path = directory / marker
            try:
                metadata = path.stat()
                if marker == ".git" and stat.S_ISDIR(metadata.st_mode):
                    (path / "HEAD").stat()
            except (OSError, ValueError):
                continue
            return directory
    return cwd


def _same_path(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.resolve(strict=True) == right.resolve(strict=True)
    except (OSError, RuntimeError):
        return False


def load_local_config(cwd: Path, user_file: Path | None) -> tuple[dict, LocalConfigState]:
    """Capture user + trusted project layers before model or extension startup."""
    cwd = Path(os.path.abspath(cwd))
    document, layers, warnings = {}, [], []
    if user_file is not None:
        user_file = Path(os.path.abspath(user_file.expanduser()))
        contents = ""
        with suppress(FileNotFoundError):
            contents = user_file.read_text(encoding="utf-8")
        document = tomllib.loads(contents)
        layers.append(ConfigLayer(user_file, "user", contents=contents))
        _normalize_layer(document, user_file.parent)
        _validate_filters(document)
    # Freeze this map before project merges: a project cannot admit itself or
    # later descendants by adding trust declarations to its own config file.
    projects = {
        key: deepcopy(value)
        for key, value in project_configurations(document).items()
        if value.get("trust_level") is not None
    }
    markers = document.get("project_root_markers", (".git",))
    if not isinstance(markers, (list, tuple)) or any(not isinstance(v, str) for v in markers):
        raise ValueError("project_root_markers must be an array of strings")
    root, git_root = _project_root(cwd, markers), trust_git_root(cwd)
    directories = []
    for directory in (cwd, *cwd.parents):
        directories.append(directory)
        if directory == root:
            break
    for directory in reversed(directories):
        folder = directory / ".corki"
        try:
            if not folder.is_dir():
                continue
        except OSError:
            continue
        if user_file is not None and _same_path(folder, user_file.parent):
            continue
        selected = None
        for candidate in (directory, root, git_root):
            if candidate is not None:
                # The loader uses dunce::canonicalize, not the active-project
                # consumer's WSL drive-mount folding.
                selected = lookup_project(projects, candidate, normalize_wsl=False)
                if selected is not None:
                    break
        trust = selected.get("trust_level") if selected is not None else None
        disabled = (
            None
            if trust == "trusted"
            else (
                f"Project-local config and rules disabled for {directory}: "
                f"project trust is {trust or 'unknown'}."
            )
        )
        file = folder / "config.toml"
        try:
            contents = file.read_text(encoding="utf-8")
        except FileNotFoundError:
            contents = ""
        try:
            overlay = tomllib.loads(contents)
        except tomllib.TOMLDecodeError as error:
            if disabled is None:
                raise ValueError(f"Error parsing project config file {file}: {error}") from error
            overlay = {}
        layers.append(ConfigLayer(file, "project", disabled, contents))
        if disabled is not None:
            warnings.append(disabled)
            continue
        ignored = [key for key in _PROJECT_DENIED if key in overlay]
        for key in ignored:
            del overlay[key]
        execution = overlay.get("execution")
        if isinstance(execution, dict) and "compiler" in execution:
            del execution["compiler"]
            ignored.append("execution.compiler")
            if not execution:
                del overlay["execution"]
        if ignored:
            warnings.append(
                f"Ignored unsupported project-local config keys in {file}: {', '.join(ignored)}"
            )
        _normalize_layer(overlay, folder)
        _validate_filters(overlay)
        _merge(document, overlay)
    visited_hook_folders = set()
    for index, layer in enumerate(layers):
        folder = layer.file.parent
        if layer.disabled_reason is not None or folder in visited_hook_folders:
            continue
        visited_hook_folders.add(folder)
        source = folder / "hooks.json"
        try:
            if source.is_file():
                layers[index] = replace(layer, hooks_json=source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            warnings.append(f"Failed to read hooks JSON in {source}: {error}")
    return document, LocalConfigState(tuple(layers), tuple(warnings))
