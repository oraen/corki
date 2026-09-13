import itertools

import pytest

from corki import __version__
from corki.config import CorkiSettings
from corki.config.shell_environment import ShellEnvironmentPolicy, parse_shell_environment_policy
from corki.tools.builtin.shell_environment import (
    create_shell_environment,
    matches_environment_pattern,
    unified_exec_environment,
)


@pytest.mark.parametrize(
    "pattern,name,expected",
    [
        ("", "", True),
        ("", "x", False),
        ("***", "", True),
        ("?", "", False),
        ("*?*", "cat", True),
        ("*12", "122", False),
        ("*121", "12121", True),
        ("cat", "wildcat", False),
        ("*o?a*r", "foobar", True),
        ("*32", "3232332", True),
        ("*???a", "bbbbba", True),
        ("* ", "\n ", True),
        ("[AB]", "A", False),
        ("[AB]", "[ab]", True),
        (r"\*", "anything", False),
        (r"\*", r"\anything", True),
        ("КО?", "Кот", True),
        ("İ?", "İx", True),
        ("i?", "İx", False),
        ("İ", "i\u0307", False),
        ("ß", "SS", False),
        ("Σ", "ς", False),
        ("Σ", "σ", True),
        ("ΟΣ", "οσ", True),
        ("ΟΣ", "ος", False),
        ("?", "😀", True),
    ],
)
def test_whole_name_unicode_wildmatch(pattern, name, expected):
    assert matches_environment_pattern(pattern, name) is expected


def test_wildmatch_exhaustive_short_patterns_against_independent_dp():
    def oracle(pattern, name):
        previous = [True] + [False] * len(name)
        for char in pattern:
            current = [previous[0] and char == "*"]
            for index, candidate in enumerate(name, 1):
                current.append(
                    previous[index] or current[-1]
                    if char == "*"
                    else previous[index - 1] and (char == "?" or char.lower() == candidate.lower())
                )
            previous = current
        return previous[-1]

    patterns = ["".join(p) for n in range(5) for p in itertools.product("a*?", repeat=n)]
    names = ["".join(p) for n in range(5) for p in itertools.product("aB", repeat=n)]
    for pattern, name in itertools.product(patterns, names):
        assert matches_environment_pattern(pattern, name) == oracle(pattern, name), (pattern, name)


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_default_keeps_ordinary_keys_but_scrubs_all_launch_names(platform):
    restricted = [
        "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
        "NODE_REPL_AUTH_TOKEN",
        "OPENAI_FEDERATION_RULE_ID",
        "OPENAI_IDENTITY_TOKEN_FILE",
        "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    ]
    inherited = {name.swapcase(): "fake-inherited" for name in restricted}
    inherited.update(SAFE="value", OPENAI_API_KEY="fake-key", PATHEXT="custom")
    policy = ShellEnvironmentPolicy(set={name.title(): "fake-configured" for name in restricted})
    assert create_shell_environment(inherited, policy, platform=platform) == {
        "SAFE": "value",
        "OPENAI_API_KEY": "fake-key",
        "PATHEXT": "custom",
    }


@pytest.mark.parametrize(
    "inherit,expected",
    [
        ("none", {}),
        ("all", {"path": "p", "HOME": "h", "APPDATA": "a", "OTHER": "o"}),
        ("core", {"path": "p", "HOME": "h"}),
    ],
)
def test_unix_inheritance(inherit, expected):
    assert (
        create_shell_environment(
            {"path": "p", "HOME": "h", "APPDATA": "a", "OTHER": "o"},
            ShellEnvironmentPolicy(inherit=inherit),
            platform="linux",
        )
        == expected
    )


def test_windows_core_and_override_case_preservation():
    assert create_shell_environment(
        {"Path": "old", "PATH": "also-old", "Home": "not-core", "AppData": "a", "SystemRoot": "s"},
        ShellEnvironmentPolicy(inherit="core", set={"path": "new"}),
        platform="win32",
    ) == {"path": "new", "AppData": "a", "SystemRoot": "s", "PATHEXT": ".COM;.EXE;.BAT;.CMD"}
    assert create_shell_environment(
        {"Path": "old"},
        ShellEnvironmentPolicy(set={"PATH": "new"}),
        platform="linux",
    ) == {"Path": "old", "PATH": "new"}


@pytest.mark.parametrize(
    "set_values,expected",
    [
        ({}, {"PATHEXT": ".COM;.EXE;.BAT;.CMD"}),
        ({"pathext": ""}, {"pathext": ""}),
    ],
)
def test_windows_pathext_added_after_empty_inheritance(set_values, expected):
    assert (
        create_shell_environment(
            {}, ShellEnvironmentPolicy(inherit="none", set=set_values), platform="win32"
        )
        == expected
    )


def test_filter_order_and_empty_include_list():
    inherited = {"API_KEY": "old", "SECRET": "old", "DROP": "old", "KEEP": "old"}
    policy = ShellEnvironmentPolicy(
        ignore_default_excludes=False,
        exclude=("DROP",),
        set={"DROP": "new", "API_KEY": "new", "OUTSIDE": "not-kept"},
        include_only=("DROP", "API_KEY", "KEEP"),
    )
    assert create_shell_environment(inherited, policy, platform="linux") == {
        "DROP": "new",
        "API_KEY": "new",
        "KEEP": "old",
    }
    assert create_shell_environment(
        {"KEEP": "yes"}, ShellEnvironmentPolicy(include_only=()), platform="linux"
    ) == {"KEEP": "yes"}


def test_canonical_filters_and_legacy_have_same_behavior(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[shell_environment_policy.filters]\n"SECRET*"="exclude"\n"S*"="include"\n')
    policy = CorkiSettings.for_directory(tmp_path, config_file=config).shell_environment_policy
    legacy = parse_shell_environment_policy({"exclude": ["SECRET*"], "include_only": ["S*"]})
    assert policy == legacy
    assert create_shell_environment(
        {"SECRET": "no", "SAFE": "yes", "OTHER": "no"}, policy, platform="linux"
    ) == {"SAFE": "yes"}


@pytest.mark.parametrize(
    "value",
    [
        [],
        "all",
        {"inherit": "ALL"},
        {"inherit": False},
        {"ignore_default_excludes": 1},
        {"experimental_use_profile": "false"},
        {"exclude": "*"},
        {"include_only": [1]},
        {"set": []},
        {"set": {"A": 1}},
        {"filters": []},
        {"filters": {"*": True}},
        {"filters": {}, "exclude": []},
        {"filters": {}, "include_only": []},
        {"filters": {"A*": "include", "a*": "exclude"}},
        {"filters": {"СЕКРЕТ*": "include", "секрет*": "exclude"}},
    ],
)
def test_bad_policy_configuration(value):
    with pytest.raises(ValueError):
        parse_shell_environment_policy(value)


def test_policy_copies_mutable_config_and_hides_values():
    supplied = {"SAFE": "fake-secret-value"}
    patterns = ["DROP"]
    policy = ShellEnvironmentPolicy(set=supplied, exclude=patterns)
    supplied["SAFE"] = "changed"
    patterns.append("SAFE")
    assert create_shell_environment({}, policy, platform="linux") == {"SAFE": "fake-secret-value"}
    assert "fake-secret-value" not in repr(policy)
    with pytest.raises(TypeError):
        policy.set["SAFE"] = "changed"


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_exec_overlay_wins_and_plugin_output_is_removed(platform):
    result = unified_exec_environment(
        {},
        ShellEnvironmentPolicy(
            inherit="none",
            include_only=("TERM", "*METRICS*"),
            set={
                "TERM": "wrong",
                "CODEX_PLUGIN_METRICS_OUTPUT": "fake",
                "codex_plugin_metrics_output": "lower",
            },
        ),
        platform=platform,
    )
    expected = {
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
        "CODEX_VERSION": __version__,
    }
    if platform == "win32":
        expected["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    else:
        expected["codex_plugin_metrics_output"] = "lower"
    assert result == expected
