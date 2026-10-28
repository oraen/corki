"""Build the public, credential-free connector sample distributed by manager."""

import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
destination = root / "manager/static/connector-example.zip"
files = {
    "connector.json": root / "gateway/examples/local-webhook/connector.json",
    "main.py": root / "gateway/examples/local-webhook/main.py",
    "README.md": root / "gateway/examples/README.md",
}
with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for name, source in files.items():
        info = zipfile.ZipInfo("local-webhook/" + name, (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, source.read_bytes())
print(destination.name)
