"""Install a verified native compiler into this source checkout (no runtime download)."""

import argparse
import runpy
import shutil
import tempfile
from pathlib import Path


def install(binary: Path, source: Path) -> Path:
    if not binary.is_absolute():
        raise ValueError("compiler path must be absolute")
    api = runpy.run_path(str(source / "src/corki/execution/bundled.py"))
    receipt = binary.with_name(binary.name + ".json")
    manifest = api["verify_compiler"](binary, receipt)
    helpers = runpy.run_path(str(source / "native/sandbox/receipt.py"))
    if manifest["source_sha256"] != helpers["source_digest"](source / "native/sandbox"):
        raise ValueError("compiler was not built from this bridge source")
    destination = source / "src/corki/_native/sandbox"
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("invalid native asset parent directory")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("source compiler directory already exists; it was not replaced")
    with tempfile.TemporaryDirectory(prefix=".sandbox-install-", dir=destination.parent) as name:
        staging = Path(name)
        shutil.copy2(binary, staging / "corki-sandbox")
        shutil.copy2(receipt, staging / "manifest.json")
        api["verify_compiler"](staging / "corki-sandbox", staging / "manifest.json")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("source compiler directory appeared; it was not replaced")
        staging.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compiler", type=Path)
    args = parser.parse_args()
    print(install(args.compiler, Path(__file__).resolve().parents[2]))


if __name__ == "__main__":
    main()
