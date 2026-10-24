"""Build the pinned policy compiler without modifying the reference checkout."""

import argparse
import json
import runpy
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

COMMIT = "ddf04ad26789d040f9ef6a96736f76602e35a6cc"


def export_reference(checkout: Path, destination: Path) -> None:
    """Export the pinned tree without requiring a platform tar executable."""
    # A disk-backed archive bounds memory and avoids leaving a blocked git child
    # when extraction fails. Close the archive before Windows removes its file.
    with tempfile.TemporaryFile() as archive:
        subprocess.run(
            ["git", "-C", str(checkout.resolve()), "archive", "--format=tar", COMMIT],
            stdout=archive, check=True,
        )
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r:") as source:
            source.extractall(destination, filter="data")


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
    parser.add_argument("--jobs", type=int, default=4, help="maximum concurrent compiler jobs")
    parser.add_argument(
        "--update-lock",
        action="store_true",
        help="regenerate this bridge's lockfile before building",
    )
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be a positive integer")
    source = Path(__file__).resolve().parent
    api = runpy.run_path(str(source.parents[1] / "src/corki/execution/bundled.py"))
    windows = api["host_identity"]()[0] == "windows"
    auxiliary_names = api["WINDOWS_HELPERS"] if windows else frozenset()
    provenance = args.output.with_name(args.output.name + ".json")
    outputs = (args.output, provenance, *(args.output.parent / name for name in auxiliary_names))
    if len(set(outputs)) != len(outputs):
        raise ValueError("compiler output conflicts with a helper artifact name")
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise FileExistsError("binary or receipt already exists; choose a new output path")
    with tempfile.TemporaryDirectory(prefix="corki-sandbox-build-") as directory:
        root = Path(directory)
        reference = root / "reference"
        reference.mkdir()
        export_reference(args.checkout, reference)
        for name in ("Cargo.toml", "Cargo.lock"):
            shutil.copyfile(source / name, root / name)
        shutil.copytree(source / "src", root / "src")
        shutil.copytree(source / "patches", root / "patches")
        shutil.copytree(source / ".cargo", root / ".cargo")
        # Product metadata is an audited additive patch to the pinned native
        # policy engine, never a rewrite of sandbox output or the user's checkout.
        apply_workspace_metadata(reference, root / "patches/workspace-metadata.patch")
        # Native Windows setup owns machine-wide accounts, firewall filters and
        # named objects. Its identifiers must not collide with installed Codex.
        apply_workspace_metadata(reference, root / "patches/windows-product-isolation.patch")
        # Backport Codex's Windows 10 drive alias handling while keeping strict
        # no-follow checks on filesystem components below the volume root.
        apply_workspace_metadata(reference, root / "patches/windows-drive-root.patch")
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
            subprocess.run(lock_command, cwd=root, check=True)
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
            str(args.jobs),
        ]
        if args.offline:
            command.append("--offline")
        targets = {"corki-sandbox"}
        if windows:
            targets.update(Path(name).stem for name in auxiliary_names)
            command.extend(["-p", "corki-sandbox", "-p", "codex-windows-sandbox"])
            for target in sorted(targets):
                command.extend(["--bin", target])
        # Use Cargo's emitted artifact identity, not a guessed target/release
        # path that can select stale output under a host/cross-target override.
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as messages:
            # Cargo resolves local config from cwd, not --manifest-path.
            subprocess.run(command, cwd=root, check=True, stdout=messages)
            messages.seek(0)
            binaries = {target: set() for target in targets}
            for line in messages:
                event = json.loads(line)
                if (
                    event.get("reason") == "compiler-artifact"
                    and event.get("target", {}).get("name") in targets
                    and event.get("executable")
                ):
                    binaries[event["target"]["name"]].add(Path(event["executable"]))
        if any(len(paths) != 1 for paths in binaries.values()):
            raise ValueError(
                "Cargo did not identify exactly one executable for each sandbox target"
            )
        binary = binaries["corki-sandbox"].pop()
        auxiliary = {name: binaries[Path(name).stem].pop() for name in auxiliary_names}
        if helpers["source_digest"](root) != compiled_digest:
            raise ValueError("bridge source changed during compilation")
        manifest = helpers["receipt"](
            source, binary, compiled_digest=compiled_digest, auxiliary=auxiliary
        )
        if any(path.exists() or path.is_symlink() for path in outputs):
            raise FileExistsError("output already exists; choose a new artifact path")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with binary.open("rb") as origin, args.output.open("xb") as destination:
            shutil.copyfileobj(origin, destination)
        shutil.copystat(binary, args.output)
        if api["file_sha256"](args.output) != manifest["binary_sha256"]:
            raise ValueError("compiler artifact changed while being copied")
        for name, helper in auxiliary.items():
            destination_path = args.output.parent / name
            with helper.open("rb") as origin, destination_path.open("xb") as destination:
                shutil.copyfileobj(origin, destination)
            shutil.copystat(helper, destination_path)
            if api["file_sha256"](destination_path) != manifest["auxiliary_sha256"][name]:
                raise ValueError("sandbox helper artifact changed while being copied")
        with provenance.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
