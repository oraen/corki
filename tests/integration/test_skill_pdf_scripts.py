"""Real standalone PDF helpers with Unicode paths and removable output files."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_pdf_helpers_roundtrip_files(tmp_path):
    for module in ("pypdf", "pdfplumber", "pypdfium2", "reportlab"):
        pytest.importorskip(module)
    from reportlab.pdfgen.canvas import Canvas

    scripts = Path(__file__).resolve().parents[2] / "skills/pdf/scripts"
    work = tmp_path / "中文 PDF (fixture)"
    work.mkdir()
    source = work / "source document.pdf"
    canvas = Canvas(str(source))
    canvas.drawString(72, 720, "Corki portable PDF fixture")
    canvas.save()
    environment = dict(os.environ, PYTHONUTF8="1", CORKI_HOME=str(work / "home"))

    def run(script, *arguments):
        result = subprocess.run(
            [sys.executable, str(scripts / script), *map(str, arguments)],
            cwd=work, env=environment, capture_output=True,
            text=True, encoding="utf-8", timeout=120,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    assert "Corki portable PDF fixture" in run("pdf_read.py", source, "--text")["pages"][0]
    combined = work / "combined.pdf"
    assert run("pdf_merge.py", source, source, "-o", combined)["page_count"] == 2
    part = work / "part.pdf"
    run("pdf_split.py", combined, "--pages", "2", "--rotate", "90", "-o", part)
    metadata = run("pdf_read.py", part, "--meta")
    assert metadata["page_count"] == 1 and metadata["pages"][0]["rotation"] == 90
    attachment = work / "附件 note.txt"
    attachment.write_text("portable fixture\n", encoding="utf-8")
    attached = work / "attached.pdf"
    run("pdf_meta.py", source, "--attach", attachment, "-o", attached)
    extracted = run("pdf_meta.py", attached, "--extract-attachments", work / "extracted")
    assert Path(extracted["extracted"][0]).read_bytes() == attachment.read_bytes()
    images = run("pdf_page_image.py", source, "--pages", "1", "--out-dir", work / "images")
    assert images["rendered"] is True and len(images["files"]) == 1
    assert Path(images["files"][0]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    # Particularly important on Windows: no helper keeps an open source/output handle.
    for path in (source, combined, part, attachment, attached):
        path.unlink()


def test_pdf_form_helpers_with_unicode_paths(tmp_path):
    for module in ("pypdf", "reportlab", "PIL"):
        pytest.importorskip(module)
    from pypdf import PdfReader

    scripts = Path(__file__).resolve().parents[2] / "skills/pdf/scripts"
    work = tmp_path / "表单 files"
    work.mkdir()
    spec = work / "布局.json"
    spec.write_text(json.dumps({"fields": [
        {"name": "name", "type": "text", "entry_box": [100, 600, 350, 625]},
        {"name": "agree", "type": "checkbox", "entry_box": [100, 550, 120, 570]},
    ]}), encoding="utf-8")

    def run(script, *arguments):
        result = subprocess.run(
            [sys.executable, "-X", "utf8=0", str(scripts / script), *map(str, arguments)],
            cwd=work, env={**os.environ, "PYTHONIOENCODING": "ascii",
                          "CORKI_HOME": str(work / "home")},
            capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        return json.loads(result.stdout.decode("utf-8"))

    overlay = work / "预览.png"
    assert run("pdf_form_layout.py", spec, "--render-overlay", overlay)["ok"]
    assert overlay.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    source = work / "空白.pdf"
    assert len(run("pdf_make_form.py", spec, "-o", source)["fields"]) == 2
    values = work / "填充值.json"
    values.write_text(json.dumps({"name": "Example", "agree": True}), encoding="utf-8")
    output = work / "填写.pdf"
    filled = run("pdf_fill_form.py", source, "--fields-json", values, "-o", output)
    assert filled["filled"] == ["agree", "name"]
    with output.open("rb") as stream:
        fields = PdfReader(stream).get_fields()
        assert fields["name"]["/V"] == "Example"
        assert fields["agree"]["/V"] == "/Yes"
    for path in (spec, overlay, source, values, output):
        path.unlink()
