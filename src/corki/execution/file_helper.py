"""Legacy Python helper; current configured file tools use the native entrypoint.

Retained for compatibility inspection, not used as a native-helper fallback.
"""

import base64
import json
import sys
from pathlib import Path


def main():
    # -I excludes model cwd and PYTHONPATH. Import the exact host-installed code.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        request = json.loads(sys.stdin.buffer.read(8_000_001))
        arguments = request["arguments"]
        if request["operation"] == "patch":
            from corki.tools.builtin.patch import _apply_operations, _parse_patch

            value = _apply_operations(Path.cwd(), _parse_patch(arguments["patch"]))
        elif request["operation"] == "image":
            from corki.tools.builtin.image import ViewImageTool

            value = base64.b64encode(
                ViewImageTool(max_bytes=arguments["max_bytes"])._read(Path(arguments["path"]))
            ).decode("ascii")
        elif request["operation"] == "project_instructions":
            from corki.config.instructions import ProjectInstructionsConfig
            from corki.context.project_instructions import load_project_entries

            entries = load_project_entries(
                Path(arguments["cwd"]), ProjectInstructionsConfig(**arguments["config"])
            )
            value = json.dumps(
                [{"text": entry.text, "source": str(entry.source)} for entry in entries]
            )
        else:
            raise ValueError("unknown filesystem operation")
        response = {"ok": value}
    except Exception as exc:
        response = {"error": str(exc)[:2000]}
    sys.stdout.write(json.dumps(response))


if __name__ == "__main__":
    main()
