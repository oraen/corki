"""Ordered shell environment derivation for local unified execution."""

import sys
from collections.abc import Mapping

from corki import __version__
from corki.config.shell_environment import ShellEnvironmentPolicy
from corki.protocol.execution_identity import ExecutionIdentity

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_NON_INHERITABLE = frozenset(
    name.lower()
    for name in (
        "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
        "NODE_REPL_AUTH_TOKEN",
        "OPENAI_FEDERATION_RULE_ID",
        "OPENAI_IDENTITY_TOKEN_FILE",
        "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    )
)
_UNIX_CORE = [
    "PATH",
    "SHELL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "USER",
]
_WINDOWS_CORE = [
    "PATH",
    "PATHEXT",
    "SHELL",
    "COMSPEC",
    "SYSTEMROOT",
    "WINDIR",
    "SYSTEMDRIVE",
    "USERNAME",
    "USERDOMAIN",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PROGRAMDATA",
    "LOCALAPPDATA",
    "APPDATA",
    "TEMP",
    "TMP",
    "TMPDIR",
    "POWERSHELL",
    "PWSH",
]
_UNIFIED_EXEC_ENV = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "LANG": "C.UTF-8",
    "LC_CTYPE": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "COLORTERM": "",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "GH_PAGER": "cat",
    "CODEX_CI": "1",
}


def matches_environment_pattern(pattern: str, name: str) -> bool:
    """Whole-name * / ? matching with per-character Unicode lowercase equality.

    No regex, bracket classes, path separators or escape rules. Comparing each
    character separately preserves wildcard width when lowercase expands (İ).
    """
    pattern_index = name_index = 0
    star = -1
    retry = 0
    while name_index < len(name):
        if pattern_index < len(pattern) and pattern[pattern_index] == "*":
            star, retry = pattern_index, name_index
            pattern_index += 1
        elif pattern_index < len(pattern) and (
            pattern[pattern_index] == "?"
            or pattern[pattern_index].lower() == name[name_index].lower()
        ):
            pattern_index += 1
            name_index += 1
        elif star >= 0:
            retry += 1
            name_index, pattern_index = retry, star + 1
        else:
            return False
    return all(char == "*" for char in pattern[pattern_index:])


def create_shell_environment(
    inherited: Mapping[str, str], policy: ShellEnvironmentPolicy, *, platform: str | None = None
) -> dict[str, str]:
    """Apply policy in source order; explicit set cannot restore launch credentials."""
    windows = (platform or sys.platform) == "win32"
    core = {name.lower() for name in (_WINDOWS_CORE if windows else _UNIX_CORE)}
    env = {
        key: value
        for key, value in inherited.items()
        if policy.inherit == "all"
        or (policy.inherit == "core" and key.translate(_ASCII_LOWER) in core)
    }
    excluded = policy.exclude
    if not policy.ignore_default_excludes:
        excluded = ("*KEY*", "*SECRET*", "*TOKEN*", *excluded)
    env = {
        k: v for k, v in env.items() if not any(matches_environment_pattern(p, k) for p in excluded)
    }
    for key, value in policy.set.items():
        if windows:
            env = {
                k: v
                for k, v in env.items()
                if k.translate(_ASCII_LOWER) != key.translate(_ASCII_LOWER)
            }
        env[key] = value
    if policy.include_only:
        env = {
            k: v
            for k, v in env.items()
            if any(matches_environment_pattern(p, k) for p in policy.include_only)
        }
    env = {k: v for k, v in env.items() if k.translate(_ASCII_LOWER) not in _NON_INHERITABLE}
    if windows and not any(k.translate(_ASCII_LOWER) == "pathext" for k in env):
        env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    return env


def unified_exec_environment(
    inherited: Mapping[str, str],
    policy: ShellEnvironmentPolicy,
    *,
    platform: str | None = None,
    identity: ExecutionIdentity | None = None,
) -> dict[str, str]:
    """Apply the noninteractive exec overlay after user filtering/overrides."""
    env = create_shell_environment(inherited, policy, platform=platform)
    windows = (platform or sys.platform) == "win32"
    # No named permission profile or patch rollout is active in Corki yet.
    # Inherited/configured values cannot serve as claims about this host.
    env = {
        k: v
        for k, v in env.items()
        if k.translate(_ASCII_LOWER) != "codex_apply_patch_preserve_line_endings"
        and (k.translate(_ASCII_LOWER) if windows else k)
        not in (
            ("codex_permission_profile", "codex_version")
            if windows
            else ("CODEX_PERMISSION_PROFILE", "CODEX_VERSION")
        )
    }
    if identity is not None:
        env["CODEX_THREAD_ID"] = str(identity.thread_id)
        env["CODEX_SESSION_ID"] = str(identity.session_id)
    else:
        # Standalone managers have no owning Runtime: do not impersonate an
        # outer Codex process merely because its launch environment is present.
        env = {
            k: v
            for k, v in env.items()
            if k.translate(_ASCII_LOWER)
            not in (
                "codex_thread_id",
                "codex_session_id",
            )
        }
    env["CODEX_VERSION"] = __version__
    env.update(_UNIFIED_EXEC_ENV)
    return {
        k: v
        for k, v in env.items()
        if (k.translate(_ASCII_LOWER) if windows else k)
        != ("codex_plugin_metrics_output" if windows else "CODEX_PLUGIN_METRICS_OUTPUT")
    }
