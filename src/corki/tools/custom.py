"""One JSON file per user-owned tool; explicit HTTP/one-shot process adapters."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import re
import signal
import sys
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from corki.config.shell_environment import ShellEnvironmentPolicy
from corki.execution.backend import sandbox_command
from corki.mcp.input_schema import normalized_input_schema
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools.builtin.shell_environment import unified_exec_environment

LOG = logging.getLogger(__name__)
MAX_FILE_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}\Z")


def validate_custom_tool(value):
    if not isinstance(value, dict):
        raise ValueError("工具配置必须是 JSON 对象")
    data = copy.deepcopy(value)
    if not isinstance(data.get("name"), str) or not NAME.fullmatch(data["name"]):
        raise ValueError("工具名称需以字母开头，限 48 位字母、数字、横线或下划线")
    if not isinstance(data.get("description"), str) or not data["description"].strip():
        raise ValueError("请填写工具描述")
    if len(data["description"]) > 4000:
        raise ValueError("工具描述过长")
    schema = data.get("parameters")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("参数定义必须是 type 为 object 的 JSON Schema")
    normalized_input_schema(schema, compact=False)
    if not isinstance(data.get("enabled", True), bool):
        raise ValueError("启用状态必须是布尔值")
    timeout = data.get("timeout_seconds", 30)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0.1 <= timeout <= 300
    ):
        raise ValueError("超时时间需在 0.1–300 秒之间")
    transport = data.get("transport")
    if transport not in ("http", "process"):
        raise ValueError("请选择 HTTP 或本地进程")
    for field in ("env", "headers", "env_headers"):
        pairs = data.get(field, {})
        if not isinstance(pairs, dict) or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or "\x00" in k + v
            for k, v in pairs.items()
        ):
            raise ValueError(f"{field} 必须是字符串键值对象")
        if field != "env" and any("\r" in k + v or "\n" in k + v for k, v in pairs.items()):
            raise ValueError("请求头不能包含换行符")
        if field == "env" and any("=" in k for k in pairs):
            raise ValueError("环境变量名称不能包含等号")
    if transport == "http":
        if data.get("method", "POST") not in ("GET", "POST"):
            raise ValueError("HTTP 请求方法仅支持 GET 或 POST")
        url = data.get("url", "")
        if not isinstance(url, str):
            raise ValueError("请填写 HTTP 地址")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("请填写有效的 HTTP/HTTPS 地址，认证请使用请求头")
        _ = parsed.port
    else:
        if (
            not isinstance(data.get("command"), str)
            or not data["command"].strip()
            or "\x00" in data["command"]
        ):
            raise ValueError("请填写启动命令")
        args = data.get("args", [])
        if not isinstance(args, list) or any(not isinstance(a, str) or "\x00" in a for a in args):
            raise ValueError("启动参数必须是字符串数组")
        if data.get("cwd") is not None and (
            not isinstance(data["cwd"], str) or "\x00" in data["cwd"]
        ):
            raise ValueError("工作目录无效")
    if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode()) > MAX_FILE_BYTES:
        raise ValueError("工具配置过大")
    return data


def custom_tool_files(home):
    root = Path(home) / "tools" / "custom"
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.json") if p.is_file() and not p.is_symlink())


def read_custom_tool(path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("工具配置过大")
    data = validate_custom_tool(json.loads(path.read_text(encoding="utf-8")))
    if path.stem != data["name"]:
        raise ValueError("工具名称必须与 JSON 文件名一致")
    return data


def register_custom_tools(registry, home, *, approvals=None, environment_policy=None):
    for path in custom_tool_files(home):
        try:
            data = read_custom_tool(path)
            if data.get("enabled", True):
                registry.register(
                    CustomTool(data, approvals=approvals, environment_policy=environment_policy)
                )
        except (ValueError, OSError) as error:
            LOG.warning("Skipping custom tool %s (%s)", path.name, type(error).__name__)


class CustomTool:
    def __init__(self, config, *, approvals=None, environment_policy=None):
        self.config = validate_custom_tool(config)
        self.approvals = approvals
        self.environment_policy = environment_policy or ShellEnvironmentPolicy()

    @property
    def spec(self):
        return ToolSpec(
            name="custom__" + self.config["name"],
            description=self.config["description"],
            parameters=self.config["parameters"],
            source="custom",
        )

    async def execute(self, call, context):
        data = self.config
        arguments = dict(call.arguments or {})
        cwd = (context.cwd / Path(data.get("cwd") or ".").expanduser()).resolve()
        if not cwd.is_dir():
            raise ValueError("工具工作目录不存在")
        env = unified_exec_environment(dict(os.environ), self.environment_policy)
        secrets = []
        if data["transport"] == "process":
            env.update(data.get("env", {}))
            secrets.extend(data.get("env", {}).values())
            argv = [data["command"], *data.get("args", [])]
            payload = arguments
        else:
            headers = dict(data.get("headers", {}))
            for name, variable in data.get("env_headers", {}).items():
                if variable not in env:
                    raise ValueError(f"请求头环境变量未设置：{variable}")
                headers[name] = env[variable]
            secrets.extend(headers.values())
            argv = [sys.executable, str(Path(__file__).with_name("custom_http.py")), data["url"]]
            payload = {
                "arguments": arguments,
                "headers": headers,
                "method": data.get("method", "POST"),
                "timeout": data.get("timeout_seconds", 30),
            }
        if context.execution_permissions is not None:
            argv = await sandbox_command(
                context.execution_permissions,
                argv,
                cwd,
                approvals=self.approvals,
                call_id=str(call.id),
                honor_allow_prefix_rules=context.honor_exec_policy_allow_rules,
                on_warning=context.on_warning,
            )
        if context.on_external_context is not None:
            await context.on_external_context()
        output, error, code = await run_process(
            argv, payload, cwd, env, data.get("timeout_seconds", 30)
        )
        content = output if code == 0 else (error or output or f"进程退出码：{code}")
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                content = content.replace(secret, "[已隐藏]")
        return ToolResult(
            call_id=call.id,
            tool_name=call.name,
            content=content,
            is_error=code != 0,
            contains_external_context=True,
        )


async def run_process(argv, payload, cwd, env, timeout):
    """Bound output and own process cleanup, including cancellation and descendants."""
    process = None
    tasks = []
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    )
    try:
        process = await asyncio.shield(spawn)

        async def read(stream):
            chunks, size = [], 0
            while block := await stream.read(65536):
                size += len(block)
                if size > MAX_OUTPUT_BYTES:
                    raise ValueError("工具输出超过 1 MB 限制")
                chunks.append(block)
            return b"".join(chunks).decode("utf-8", errors="replace")

        async def write():
            try:
                process.stdin.write(
                    (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode()
                )
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        async with asyncio.timeout(timeout):
            tasks = [
                asyncio.create_task(read(process.stdout)),
                asyncio.create_task(read(process.stderr)),
                asyncio.create_task(write()),
                asyncio.create_task(process.wait()),
            ]
            output, error, _, code = await asyncio.gather(*tasks)
            return output, error, code
    finally:
        if process is None:
            process = await spawn
        if os.name == "posix":
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
        await process.wait()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
