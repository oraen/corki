"""Install a verified native compiler into this source checkout (no runtime download)."""

import argparse
import os
import runpy
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def staging_directory(parent: Path):
    if os.name != "nt":
        with tempfile.TemporaryDirectory(prefix=".sandbox-install-", dir=parent) as name:
            yield Path(name)
        return
    # Windows mkdtemp uses a protected owner-only ACL. Renaming it would retain
    # that ACL and make an elevated installation unreadable to ordinary users.
    # These verified application assets inherit the destination parent's ACL;
    # no user credentials or configuration are staged here.
    staging = parent / (".sandbox-install-" + uuid.uuid4().hex)
    staging.mkdir()
    try:
        yield staging
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def install(binary: Path, source: Path) -> Path:
    if not binary.is_absolute():
        raise ValueError("compiler path must be absolute")
    api = runpy.run_path(str(source / "src/corki/execution/bundled.py"))
    receipt = binary.with_name(binary.name + ".json")
    manifest = api["verify_compiler"](binary, receipt)
    binary_name = "corki-sandbox.exe" if manifest["system"] == "windows" else "corki-sandbox"
    helpers = runpy.run_path(str(source / "native/sandbox/receipt.py"))
    if manifest["source_sha256"] != helpers["source_digest"](source / "native/sandbox"):
        raise ValueError("compiler was not built from this bridge source")
    destination = source / "src/corki/_native/sandbox"
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("invalid native asset parent directory")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("source compiler directory already exists; it was not replaced")
    with staging_directory(destination.parent) as staging:
        shutil.copy2(binary, staging / binary_name)
        for name in manifest.get("auxiliary_sha256", {}):
            shutil.copy2(binary.parent / name, staging / name)
        shutil.copy2(receipt, staging / "manifest.json")
        api["verify_compiler"](staging / binary_name, staging / "manifest.json")
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
