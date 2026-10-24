"""Exercise the standalone creator with UTF-8 files and legacy Windows pipes."""

import os
import subprocess
import sys
from pathlib import Path

import yaml


def test_skill_creator_unicode_metadata_and_bom_validation(tmp_path):
    scripts = Path(__file__).resolve().parents[2] / "skills/skill-creator/scripts"
    destination = tmp_path / "技能 files"
    environment = {**os.environ, "CORKI_HOME": str(tmp_path / "home"),
                   "PYTHONIOENCODING": "ascii"}

    def run(script, *args):
        result = subprocess.run(
            [sys.executable, "-X", "utf8=0", str(scripts / script), *map(str, args)],
            env=environment, capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        return result.stdout.decode("utf-8")

    run("init_skill.py", "example", "--path", destination,
        "--resources", "scripts,references,assets", "--examples",
        "--interface", "display_name=示例技能")
    skill = destination / "example"
    interface = yaml.safe_load((skill / "agents/openai.yaml").read_text(encoding="utf-8"))
    assert interface["interface"]["display_name"] == "示例技能"
    for file in ("scripts/example.py", "references/api_reference.md", "assets/example_asset.txt"):
        assert (skill / file).read_text(encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: 中文描述\n---\n\n按步骤处理示例任务。\n",
        encoding="utf-8-sig",
    )
    assert "Skill is valid" in run("quick_validate.py", skill)
    run("generate_openai_yaml.py", skill, "--interface", "display_name=更新后的示例")
    interface = yaml.safe_load((skill / "agents/openai.yaml").read_text(encoding="utf-8"))
    assert interface["interface"]["display_name"] == "更新后的示例"
