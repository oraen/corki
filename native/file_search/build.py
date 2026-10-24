"""Build and install a new, locked local-search bundle without overwriting one."""

import argparse
import json
import os
import platform
import runpy
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("choose a new bundle directory; existing artifacts are preserved")
    source = Path(__file__).resolve().parent
    root = source.parents[1]
    api = runpy.run_path(str(source / "artifact.py"))
    initial_digest = api["source_digest"](source)
    common = ["--locked", "--manifest-path", str(source / "Cargo.toml")]
    if args.offline:
        common.append("--offline")
    with tempfile.TemporaryDirectory(prefix="corki-file-search-build-") as temp:
        target = Path(temp) / "target"
        result = subprocess.run(
            [
                args.cargo,
                "build",
                *common,
                "--release",
                "-j",
                "4",
                "--target-dir",
                str(target),
                "--message-format=json-render-diagnostics",
            ],
            check=True,
            cwd=source,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        binaries = set()
        for line in result.stdout.splitlines():
            event = json.loads(line)
            if (
                event.get("reason") == "compiler-artifact"
                and event.get("target", {}).get("name") == "corki-file-search"
                and event.get("executable")
            ):
                binaries.add(Path(event["executable"]))
        if len(binaries) != 1 or initial_digest != api["source_digest"](source):
            raise ValueError("ambiguous artifact or source changed during build")
        metadata = json.loads(
            subprocess.check_output(
                [args.cargo, "metadata", *common, "--format-version", "1"],
                cwd=source,
                text=True,
                encoding="utf-8",
            )
        )
        licenses = []
        for package in sorted(metadata["packages"], key=lambda p: (p["name"], p["version"])):
            if package["name"] == "corki-file-search":
                continue
            directory = Path(package["manifest_path"]).parent
            files = sorted(
                {
                    p
                    for pattern in (
                        "LICENSE*",
                        "COPYING*",
                        "NOTICE*",
                        "license*",
                        "copying*",
                        "notice*",
                    )
                    for p in directory.glob(pattern)
                    if p.is_file()
                }
            )
            if not files and package.get("source", "").startswith("git+"):
                files = sorted(directory.parent.glob("LICENSE*"))
            if not files:
                raise ValueError(f"missing license texts for {package['name']}")
            licenses.append(f"{package['name']} {package['version']} ({package.get('license')})\n")
            for path in files:
                licenses.append(path.name + "\n" + path.read_text(encoding="utf-8") + "\n")
        system, machine = platform.system().lower(), platform.machine().lower()
        sysroot = Path(
            subprocess.check_output(
                [os.environ.get("RUSTC", "rustc"), "--print", "sysroot"],
                text=True,
                encoding="utf-8",
            ).strip()
        )
        rust_notice = sysroot / "share/doc/rust/COPYRIGHT-library.html"
        if not rust_notice.is_file():
            raise ValueError("Rust standard library copyright notices are required")
        if system == "darwin":
            major = platform.mac_ver()[0].split(".")[0]
            tag = f"macosx_{major}_0_{machine}"
        elif system == "linux":
            tag = f"linux_{machine}"
        elif system == "windows" and machine in ("amd64", "x86_64", "arm64", "aarch64"):
            tag = "win_amd64" if machine in ("amd64", "x86_64") else "win_arm64"
        else:
            raise ValueError("unsupported native search build platform")
        executable_name = "corki-file-search.exe" if system == "windows" else "corki-file-search"
        output.parent.mkdir(parents=True, exist_ok=True)
        # Reuse native asset staging: Windows mkdtemp's protected ACL must not
        # become the published bundle's ACL after an elevated build.
        installer = runpy.run_path(str(root / "native/sandbox/install.py"))
        with installer["staging_directory"](output.parent) as staging:
            bundle = staging / "bundle"
            bundle.mkdir()
            shutil.copy2(binaries.pop(), bundle / executable_name)
            shutil.copyfile(rust_notice, bundle / "RUST_COPYRIGHT.html")
            (bundle / "THIRD_PARTY.txt").write_text("\n".join(licenses), encoding="utf-8")
            manifest = {
                "version": 1,
                "source_sha256": initial_digest,
                "wheel_platform": tag,
                "files": {
                    name: api["digest"](bundle / name)
                    for name in (executable_name, "THIRD_PARTY.txt", "RUST_COPYRIGHT.html")
                },
            }
            (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
            api["verify"](root, bundle)
            # rename cannot replace an existing nonempty bundle; never update an
            # executable in place while an existing reader could still own it.
            if output.exists() or output.is_symlink():
                raise FileExistsError("bundle destination appeared during build")
            os.rename(bundle, output)
    print(output)


if __name__ == "__main__":
    main()
