import subprocess
import sys
from pathlib import Path


def test_desktop_does_not_import_cli_presentation():
    root = Path(__file__).parents[2]
    result = subprocess.run([
        sys.executable, "-c",
        "import sys;sys.path.insert(0,'app/bridge');import service;"
        "assert not any(m=='corki.cli' or m.startswith('corki.cli.') for m in sys.modules)",
    ], cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_cli_and_desktop_use_identical_field_validation():
    from corki.cli.elicitation import validate_field as cli_validate
    from corki.protocol.form_validation import validate_field

    assert cli_validate is validate_field
