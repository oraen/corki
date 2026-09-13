"""Include a provenance-checked native compiler in a platform-specific wheel."""

import os
import runpy
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        configured = os.environ.get("CORKI_BUILD_SANDBOX_COMPILER")
        if self.target_name != "wheel":
            return
        root = Path(self.root)
        api = runpy.run_path(str(root / "src/corki/execution/bundled.py"))
        if configured is None:
            if version != "standard":
                return  # Editable imports use the source installation, not wheel assets.
            binary = api["bundled_compiler"]()
            if binary is None:
                return
            provenance = binary.parent / "manifest.json"
        else:
            binary = Path(configured)
            if not binary.is_absolute():
                raise ValueError("CORKI_BUILD_SANDBOX_COMPILER must be absolute")
            provenance = binary.with_name(binary.name + ".json")
        if version != "standard":
            raise ValueError("native compiler bundling requires a standard wheel build")
        manifest = api["verify_compiler"](binary, provenance)
        helpers = runpy.run_path(str(root / "native/sandbox/receipt.py"))
        if manifest["source_sha256"] != helpers["source_digest"](root / "native/sandbox"):
            raise ValueError("native compiler was not built from this bridge source")
        name = "corki-sandbox.exe" if manifest["system"] == "windows" else "corki-sandbox"
        build_data["force_include"].update(
            {
                str(binary): f"corki/_native/sandbox/{name}",
                str(provenance): "corki/_native/sandbox/manifest.json",
            }
        )
        build_data["pure_python"] = False
        build_data["tag"] = "py3-none-" + manifest["wheel_platform"]
