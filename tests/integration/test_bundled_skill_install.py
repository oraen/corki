"""Concurrent installers use real independent processes and kernel locks."""

import subprocess
import sys


def test_concurrent_install_preserves_complete_tree_and_refreshes(tmp_path):
    source, home = tmp_path / "source", tmp_path / "home"
    (source / "example/scripts").mkdir(parents=True)
    (source / "example/SKILL.md").write_text("---\nname: example\n---\n你好", encoding="utf-8")
    for index in range(40):
        (source / f"example/scripts/{index}.py").write_text("# Fixture\n" * 100)
    code = (
        "import sys; from pathlib import Path; "
        "from corki.skills.installer import install_bundled_skills; "
        "install_bundled_skills(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    for expected in ("first", "updated"):
        (source / "example/revision").write_text(expected)
        children = [subprocess.Popen(
            [sys.executable, "-c", code, str(home), str(source)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) for _ in range(3)]
        try:
            for child in children:
                _, error = child.communicate(timeout=30)
                assert child.returncode == 0, error.decode(errors="replace")
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait()
        destination = home / "skills/.system"
        assert (destination / "example/revision").read_text() == expected
        assert len(list((destination / "example/scripts").iterdir())) == 40
        assert sorted(p.name for p in (home / "skills").iterdir()) == [".system", ".system.lock"]
