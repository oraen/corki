"""Tests for prompt resource loading, validation, rendering, and caching."""

from pathlib import Path

import pytest

from corki.prompting import (
    InvalidPromptNameError,
    InvalidPromptTemplateError,
    MissingPromptVariablesError,
    PromptNotFoundError,
    PromptStore,
)


def test_loads_repository_template_with_discovered_variables() -> None:
    store = PromptStore()

    template = store.load("context/environment")

    assert template.name == "context/environment.md"
    assert template.variables == frozenset({"cwd", "shell", "current_date", "timezone"})


def test_renders_variables_without_treating_json_braces_as_placeholders(tmp_path: Path) -> None:
    template_path = tmp_path / "sample.md"
    template_path.write_text('Directory: #{cwd}\nSchema: {"command": "pytest"}\n', encoding="utf-8")
    store = PromptStore(root=tmp_path)

    rendered = store.render("sample", cwd="/workspace/corki")

    assert rendered == 'Directory: /workspace/corki\nSchema: {"command": "pytest"}\n'


def test_keeps_escaped_placeholder_literal(tmp_path: Path) -> None:
    (tmp_path / "sample.md").write_text(r"Literal: \#{name}; rendered: #{name}", encoding="utf-8")
    store = PromptStore(root=tmp_path)

    assert store.render("sample", name="Corki") == "Literal: #{name}; rendered: Corki"


def test_reports_every_missing_variable(tmp_path: Path) -> None:
    (tmp_path / "sample.md").write_text("#{first} #{second}", encoding="utf-8")
    store = PromptStore(root=tmp_path)

    with pytest.raises(MissingPromptVariablesError) as error:
        store.render("sample", first="available")

    assert error.value.missing == frozenset({"second"})


@pytest.mark.parametrize("source", ["#{bad-name}", "#{unfinished"])
def test_rejects_invalid_placeholder_syntax(tmp_path: Path, source: str) -> None:
    (tmp_path / "sample.md").write_text(source, encoding="utf-8")
    store = PromptStore(root=tmp_path)

    with pytest.raises(InvalidPromptTemplateError):
        store.load("sample")


def test_caches_parsed_source_until_cleared(tmp_path: Path) -> None:
    template_path = tmp_path / "sample.md"
    template_path.write_text("first", encoding="utf-8")
    store = PromptStore(root=tmp_path)

    first = store.load("sample")
    template_path.write_text("second", encoding="utf-8")
    cached = store.load("sample")
    store.clear_cache()
    reloaded = store.load("sample")

    assert cached is first
    assert cached.source == "first"
    assert reloaded.source == "second"


@pytest.mark.parametrize("name", ["", "../secret", "/absolute", "bad\\name", "dir//name"])
def test_rejects_unsafe_prompt_names(name: str, tmp_path: Path) -> None:
    store = PromptStore(root=tmp_path)

    with pytest.raises(InvalidPromptNameError):
        store.load(name)


def test_reports_unknown_template(tmp_path: Path) -> None:
    store = PromptStore(root=tmp_path)

    with pytest.raises(PromptNotFoundError):
        store.load("agent/missing")
