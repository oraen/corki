"""Disposable browser fixture. No real models, user home or external tools."""

import argparse
import asyncio
import importlib.util
import json
import os
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "application_fixture_server", Path(__file__).parents[1] / "server.py"
)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
from corki.models.types import (  # noqa: E402 - bootstraps the standalone Manager module
    ModelCompleted,
    ModelTextDelta,
    ModelUsage,
)
from corki.protocol.items import (  # noqa: E402 - bootstraps the standalone Manager module
    AssistantMessageItem,
    new_step_id,
)


class Model:
    async def stream(self, request):
        content = (
            "应用已接通。\n\n## 测试回答\n\n"
            "这是隔离环境中的 **流式回复**。\n\n| 项目 | 状态 |\n| --- | --- |\n| "
            '提示词 | 已生效 |\n\n```json\n{"ready": true, "count": 3}\n```'
        )
        for i in range(0, len(content), 8):
            yield ModelTextDelta(content[i : i + 8])
            await asyncio.sleep(0.08)
        yield ModelCompleted(
            (AssistantMessageItem(content, request.items[-1].turn_id, new_step_id()),),
            ModelUsage(input_tokens=300, output_tokens=80),
        )

    async def aclose(self):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--info", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="corki-applications-qa-") as folder:
        root = Path(folder)
        home = root / "home"
        home.mkdir()
        os.environ["CORKI_HOME"] = str(home)
        (home / "settings.json").write_text(
            json.dumps(
                {
                    "default": {"provider": "local", "model": "test"},
                    "providers": {
                        "local": {
                            "base_url": "http://127.0.0.1:1",
                            "api_mode": "responses",
                            "models": {"test": {"context_window": 131072}},
                        }
                    },
                }
            )
        )
        host = ThreadingHTTPServer(("127.0.0.1", args.port), server.Handler)
        host.daemon_threads = True
        host.store = server.Store(home, root / "work", root / "compat")
        host.store.applications.execution.model_factory = lambda _: Model()
        from corki.core.runtime import LangGraphRuntime

        async def runtime(**kw):
            return await LangGraphRuntime.acreate(**kw, model=Model())

        host.store.applications.execution.runtime_factory = runtime
        for name, kind, description in [
            ("写作助手", "llm", "整理思路、润色表达，让文字更清晰。"),
            ("代码助手", "agent", "结合工作目录与工具，协助定位和解决问题。"),
            ("数据解读", "llm", "梳理数据中的变化，提炼关键发现。"),
        ]:
            host.store.applications.store.save(
                dict(
                    name=name,
                    type=kind,
                    description=description,
                    prompt="请简洁回答，明确事实与推测。",
                )
            )
        args.info.write_text(
            json.dumps(dict(url=f"http://127.0.0.1:{host.server_port}", home=str(home)))
        )
        print(f"Fixture: http://127.0.0.1:{host.server_port}", flush=True)
        try:
            host.serve_forever()
        finally:
            host.store.applications.close()
            host.server_close()


if __name__ == "__main__":
    main()
