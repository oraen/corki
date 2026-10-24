import runpy
from pathlib import Path

import pytest


@pytest.fixture
def attachment_name():
    source = Path(__file__).resolve().parents[2] / "skills/pdf/scripts/pdf_meta.py"
    return runpy.run_path(str(source))["attachment_name"]


@pytest.mark.parametrize("name", [
    "report.txt:stream", "CON", "aux.pdf", "LPT¹.txt", "COM1.log",
    "NUL .txt", "..", "note.", "note ", "bad\0name", 'bad"name',
])
def test_attachment_rejects_windows_special_names(attachment_name, name):
    with pytest.raises(ValueError, match="portable filename"):
        attachment_name(name)


@pytest.mark.parametrize("name", [r"folder\中文 note.txt", "folder/中文 note.txt"])
def test_attachment_flattens_both_path_grammars(attachment_name, name):
    assert attachment_name(name) == "中文 note.txt"
