"""Bind full consolidation policy to the worker's actual file-backed workspace."""

from pathlib import Path

from corki.prompting import PromptStore


def build_consolidation_prompt(store: PromptStore, root: Path, shared: bool = False) -> str:
    """Render policy and optional source instructions without injecting source bodies."""
    extensions = root / "extensions"
    blocks = {
        "memory_extensions_" + name: (
            store.render("memory/extensions_" + name, memory_extensions_root=str(extensions))
            if extensions.is_dir()
            else ""
        )
        for name in ("folder_structure", "primary_inputs")
    }
    return (
        store.render(
            "memory/consolidation",
            memory_root=str(root),
            phase2_workspace_diff_file="phase2_workspace_diff.md",
            **blocks,
        )
        + "\n"
        + store.render(
            "memory/consolidation_runtime_shared" if shared else "memory/consolidation_runtime"
        )
    )


def seed_extension_instructions(root: Path) -> None:
    """Install native ad-hoc interpretation guidance without replacing user edits."""
    directory = root / "extensions" / "ad_hoc"
    if any(path.is_symlink() for path in (root, root / "extensions", directory)):
        raise ValueError("memory extension directory cannot be a symbolic link")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "instructions.md"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(PromptStore().render("memory/ad_hoc_instructions"))
    except FileExistsError:
        if path.is_symlink() or not path.is_file():
            raise ValueError("memory extension instructions must be a regular file") from None
