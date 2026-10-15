"""Build the pinned policy compiler without modifying the reference checkout."""

import argparse
import json
import runpy
import shutil
import subprocess
import tempfile
from pathlib import Path

COMMIT = "ddf04ad26789d040f9ef6a96736f76602e35a6cc"


def apply_workspace_metadata(reference: Path, patch: Path) -> None:
    """Apply only in the exported build tree; drift or a missing patch is fatal."""
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=reference, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=reference, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--update-lock",
        action="store_true",
        help="regenerate this bridge's lockfile before building",
    )
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    provenance = args.output.with_name(args.output.name + ".json")
    if any(path.exists() or path.is_symlink() for path in (args.output, provenance)):
        raise FileExistsError("binary or receipt already exists; choose a new output path")
    with tempfile.TemporaryDirectory(prefix="corki-sandbox-build-") as directory:
        root = Path(directory)
        reference = root / "reference"
        reference.mkdir()
        archive = subprocess.Popen(
            ["git", "-C", str(args.checkout.resolve()), "archive", COMMIT],
            stdout=subprocess.PIPE,
        )
        try:
            subprocess.run(["tar", "-x", "-C", str(reference)], stdin=archive.stdout, check=True)
        finally:
            archive.stdout.close()
            status = archive.wait()
        if status:
            raise RuntimeError("reference source export failed")
        for name in ("Cargo.toml", "Cargo.lock"):
            shutil.copyfile(source / name, root / name)
        shutil.copytree(source / "src", root / "src")
        shutil.copytree(source / "patches", root / "patches")
        # Product metadata is an audited additive patch to the pinned native
        # policy engine, never a rewrite of sandbox output or the user's checkout.
        apply_workspace_metadata(reference, root / "patches/workspace-metadata.patch")
        if args.update_lock:
            lock_command = [
                args.cargo,
                "+1.95.0",
                "update",
                "--package",
                "corki-sandbox",
                "--manifest-path",
                str(root / "Cargo.toml"),
            ]
            if args.offline:
                lock_command.append("--offline")
            subprocess.run(lock_command, check=True)
            shutil.copyfile(root / "Cargo.lock", source / "Cargo.lock")
        helpers = runpy.run_path(str(source / "receipt.py"))
        compiled_digest = helpers["source_digest"](root)
        command = [
            args.cargo,
            "+1.95.0",
            "build",
            "--release",
            "--locked",
            "--message-format=json-render-diagnostics",
            "--manifest-path",
            str(root / "Cargo.toml"),
            "-j",
            "4",
        ]
        if args.offline:
            command.append("--offline")
        # Use Cargo's emitted artifact identity, not a guessed target/release
        # path that can select stale output under a host/cross-target override.
        with tempfile.TemporaryFile(mode="w+t") as messages:
            subprocess.run(command, check=True, stdout=messages)
            messages.seek(0)
            binaries = set()
            for line in messages:
                event = json.loads(line)
                if (
                    event.get("reason") == "compiler-artifact"
                    and event.get("target", {}).get("name") == "corki-sandbox"
                    and event.get("executable")
                ):
                    binaries.add(Path(event["executable"]))
        if len(binaries) != 1:
            raise ValueError("Cargo did not identify exactly one compiler executable")
        binary = binaries.pop()
        if helpers["source_digest"](root) != compiled_digest:
            raise ValueError("bridge source changed during compilation")
        manifest = helpers["receipt"](source, binary, compiled_digest=compiled_digest)
        if any(path.exists() or path.is_symlink() for path in (args.output, provenance)):
            raise FileExistsError("output already exists; choose a new artifact path")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with binary.open("rb") as origin, args.output.open("xb") as destination:
            shutil.copyfileobj(origin, destination)
        shutil.copystat(binary, args.output)
        api = runpy.run_path(str(source.parents[1] / "src/corki/execution/bundled.py"))
        if api["file_sha256"](args.output) != manifest["binary_sha256"]:
            raise ValueError("compiler artifact changed while being copied")
        with provenance.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
