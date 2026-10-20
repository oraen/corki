"""Local management API; explicit MCP probes may start configured stdio services."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import copy
import hashlib
import http.client
import io
import ipaddress
import json
import mimetypes
import os
import re
import shlex
import shutil
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, urljoin, urlparse

from corki.config import document
from corki.config.features import MCPServerSettings
from corki.config.settings import CorkiSettings
from corki.config.skills import disabled_skill_paths, parse_skill_rules
from corki.core.runtime import _create_model, _model_capabilities
from corki.cron.diagnostics import safe_log
from corki.cron.service import CronService
from corki.cron.store import CronStore
from corki.knowledge.extraction import extract as extract_knowledge
from corki.knowledge.store import KnowledgeStore
from corki.mcp.client import create_client
from corki.mcp.input_schema import mcp_input_schema
from corki.mcp.json_rpc import plain_json
from corki.models.types import ModelRequest, ModelTextDelta
from corki.protocol.ids import new_model_step_id, new_tool_call_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem
from corki.protocol.tools import ToolCall
from corki.skills.frontmatter import parse as parse_skill
from corki.skills.walk import walk_skill_files
from corki.storage.admin import DatabaseAdmin
from corki.storage.query_workspace import QueryWorkspace
from corki.tools.base import ToolContext
from corki.tools.custom import (
    NAME as CUSTOM_TOOL_NAME,
)
from corki.tools.custom import (
    CustomTool,
    custom_tool_files,
    read_custom_tool,
    validate_custom_tool,
)
from corki.tools.executor import ToolExecutor
from corki.tools.registry import ToolRegistry

STATIC = Path(__file__).parent / "static"
LOCK = threading.RLock()
MCP_TEST_SLOTS = threading.BoundedSemaphore(4)
MODEL_TEST_SLOTS = threading.BoundedSemaphore(3)
CUSTOM_TEST_SLOTS = threading.BoundedSemaphore(4)
KNOWLEDGE_SLOTS = threading.BoundedSemaphore(2)
CONFIG_HISTORY_LIMIT = 20
LIMIT = 256 * 1024
IMPORT_LIMIT = 8 * 1024 * 1024


class KnowledgeHTML(HTMLParser):
    """Extract readable HTML without executing scripts or loading subresources."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = []
        self.hidden = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"}:
            self.hidden.append(tag)
        if tag == "title":
            self.in_title = True
        if not self.hidden and tag in {
            "p",
            "div",
            "br",
            "li",
            "section",
            "article",
            "h1",
            "h2",
            "h3",
            "tr",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if tag == "title":
            self.in_title = False
        if not self.hidden and tag in {
            "p",
            "div",
            "li",
            "section",
            "article",
            "h1",
            "h2",
            "h3",
            "tr",
        }:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.hidden:
            self.parts.append(data)

    def result(self):
        text = "\n\n".join(
            line.strip() for line in "".join(self.parts).splitlines() if line.strip()
        )
        return "".join(self.title).strip(), text


def knowledge_result(title, content):
    if not content.strip():
        raise ValueError("未提取到正文，请上传含文字的文档或直接输入文本")
    if len(content.encode("utf-8")) > LIMIT:
        raise ValueError("文档内容过长，请分成多个文档导入")
    name = re.sub(r"[^\w-]+", "-", title).strip("-")[:80] or "新文档"
    return {"name": name, "content": content.strip()}


def parse_knowledge_document(data):
    name = data.get("filename", "")
    if not isinstance(name, str):
        raise ValueError("请选择文档")
    try:
        raw = base64.b64decode(data.get("data", ""), validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("文件读取失败，请重新上传") from exc
    if not raw or len(raw) > IMPORT_LIMIT:
        raise ValueError("请选择不超过 8 MB 的文档")
    ext = Path(name).suffix.lower()
    try:
        if ext in {".md", ".txt"}:
            try:
                content = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                content = raw.decode("gb18030")
        elif ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                raise ValueError("请先解除 PDF 密码后再上传")
            if len(reader.pages) > 300:
                raise ValueError("PDF 页数过多，请拆分后导入")
            chunks = []
            for page in reader.pages:
                chunks.append(page.extract_text() or "")
                if sum(len(chunk.encode()) for chunk in chunks) > LIMIT:
                    raise ValueError("文档内容过长，请分成多个文档导入")
            content = "\n\n".join(chunks)
        elif ext == ".docx":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > IMPORT_LIMIT:
                    raise ValueError("文档内容过长，请拆分后导入")
                xml = archive.read(info)
            if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                raise ValueError("无法解析此文档")
            root = ET.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            content = "\n\n".join("".join(p.itertext()) for p in root.findall(".//w:p", ns))
        else:
            raise ValueError("请选择 Markdown、TXT、PDF 或 DOCX 文档")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("文档解析失败，请检查文件是否完整") from exc
    return knowledge_result(Path(name).stem, content)


def fetch_knowledge_page(url):
    """Fetch public HTTP(S) pages, pinning the validated address per redirect."""
    for _ in range(6):
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("请输入有效的 http 或 https 网页地址")
        try:
            host = parsed.hostname.encode("idna").decode()
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError("请使用公开网页地址")
            connection = http.client.HTTPConnection(host, port, timeout=15)
            connection.sock = socket.create_connection((addresses[0][4][0], port), timeout=15)
            try:
                if parsed.scheme == "https":
                    connection.sock = ssl.create_default_context().wrap_socket(
                        connection.sock, server_hostname=host
                    )
                path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
                if parsed.query:
                    path += "?" + quote(parsed.query, safe="%=&?/:@!$'()*+,;-._~")
                connection.request(
                    "GET",
                    path,
                    headers={
                        "User-Agent": "Corki-Manager/1.0",
                        "Accept": "text/html,text/plain",
                        "Accept-Encoding": "identity",
                    },
                )
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("网页重定向失败")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise ValueError(f"网页暂时无法读取（HTTP {response.status}）")
                mime = response.headers.get_content_type()
                if mime not in {
                    "text/html",
                    "application/xhtml+xml",
                    "text/plain",
                    "text/markdown",
                }:
                    raise ValueError("此链接不是可解析的网页")
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("网页过大，请复制正文后导入")
                charset = response.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
            finally:
                connection.close()
        except (OSError, http.client.HTTPException, LookupError) as exc:
            raise ValueError("网页读取失败，请检查链接后重试") from exc
        title, content = host, text
        if mime in {"text/html", "application/xhtml+xml"}:
            parser = KnowledgeHTML()
            parser.feed(text)
            title, content = parser.result()
        result = knowledge_result(title or host, content)
        result["content"] += "\n\n来源：" + url
        return knowledge_result(result["name"], result["content"])
    raise ValueError("网页跳转次数过多，请使用最终页面地址")


def revision(text):
    return hashlib.sha256(text.encode()).hexdigest()


def atomic(path, text, backup=True):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        dest = path.parent / ".manager-backups"
        dest.mkdir(exist_ok=True, mode=0o700)
        old = path.read_bytes()
        target = dest / (path.name + "." + hashlib.sha256(old).hexdigest()[:16])
        if not target.exists():
            target.write_bytes(old)
            target.chmod(0o600)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".manager-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, home, workspace, compatibility_home=None):
        self.home = home.resolve()
        self.workspace = workspace.resolve()
        self.compatibility_home = (compatibility_home or Path.home()).resolve()
        self.config = self.home / "settings.json"
        self.database = self.home / "sessions/corki.db"
        self.knowledge = KnowledgeStore(self.home)
        from corki.knowledge.builtin import install_builtin_knowledge

        install_builtin_knowledge(self.home)
        self.cron = CronStore(self.home)
        self.cron_service = CronService(self.home)

    @property
    def knowledge_root(self):
        return self.home / "manager/knowledge"

    @property
    def knowledge_state_path(self):
        return self.home / "manager/knowledge-state.json"

    def knowledge_state(self):
        text = (
            self.knowledge_state_path.read_text()
            if self.knowledge_state_path.exists() else '{"disabled":[]}'
        )
        try:
            data = json.loads(text)
        except (ValueError, TypeError) as error:
            raise ValueError("知识库状态文件格式无效") from error
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("disabled"), list)
            or any(not isinstance(key, str) for key in data["disabled"])
        ):
            raise ValueError("知识库状态文件格式无效")
        return text, set(data["disabled"])

    def knowledge_key(self, path):
        return Path(path).resolve().relative_to(self.knowledge_root.resolve()).as_posix()

    def write_knowledge_state(self, disabled):
        atomic(
            self.knowledge_state_path,
            json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2) + "\n",
            backup=False,
        )

    def toggle_knowledge(self, data):
        text, disabled = self.knowledge_state()
        if data.get("knowledge_revision") != revision(text):
            raise ValueError("知识库状态已更新，请刷新后重试")
        item = next(
            (f for f in self.files() if f["id"] == data.get("id") and f["kind"] == "knowledge"),
            None,
        )
        if item is None:
            raise ValueError("知识文档不存在")
        enabled = data.get("values", {}).get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("知识库开关必须是布尔值")
        key = self.knowledge_key(item["path"])
        if enabled and item["index_status"] != "ready":
            # Preparation stays disabled until finish publishes the validated index.
            disabled.add(key)
            self.write_knowledge_state(disabled)
            self.start_knowledge(item)
            return {"message": "技能加载中"}
        if enabled:
            disabled.discard(key)
        else:
            disabled.add(key)
        self.knowledge.set_enabled(item["document_id"], enabled)
        self.write_knowledge_state(disabled)
        return {"message": "已开启" if enabled else "已停用"}

    def start_knowledge(self, item):
        doc = self.knowledge.sync(item["path"], item["name"], Path(item["path"]).read_text())
        if doc["status"] in ("ready", "processing"):
            return
        if not KNOWLEDGE_SLOTS.acquire(blocking=False):
            raise ValueError("已有两篇文档正在处理，请稍后点击重试")
        job = None
        try:
            config = document.loads(self.config_text())
            default = config.get("default", {})
            provider, model = default.get("provider"), default.get("model")
            if not provider or not model:
                raise ValueError("请先配置默认供应商和模型")
            settings = CorkiSettings.for_directory(
                self.home, config_file=self.config, provider=provider, model=model)
            settings = replace(settings, model_request_max_retries=1,
                               model_unbounded_connection_retries=False,
                               execution_permissions=None)
            content = Path(item["path"]).read_text()
            self.knowledge.set_enabled(doc["id"], False)
            job = self.knowledge.claim(
                doc["id"], settings.provider_id + "/" + settings.model, prepare=True)
            if not job:
                KNOWLEDGE_SLOTS.release()
                return
            thread = threading.Thread(target=self._process_knowledge,
                                      args=(settings, doc, job, content), daemon=True)
            thread.start()
        except Exception:
            KNOWLEDGE_SLOTS.release()
            if job:
                self.knowledge.fail(doc["id"], job, "无法启动处理，请检查默认模型配置后重试")
            raise

    def _process_knowledge(self, settings, doc, job, content):
        async def process():
            model = _create_model(settings, _model_capabilities(settings))
            await extract_knowledge(model, settings, self.knowledge, doc, job, content)
        try:
            asyncio.run(process())
            with LOCK:
                with self.knowledge.connect() as db:
                    current = db.execute(
                        "SELECT enabled FROM documents WHERE id=?", (doc["id"],)).fetchone()
                if current and current["enabled"]:
                    _, disabled = self.knowledge_state()
                    disabled.discard(doc["path"])
                    self.write_knowledge_state(disabled)
        except Exception as error:
            # Never persist provider error bodies: they may contain credentials or source text.
            reason = ("模型返回的摘要格式不符合要求，请重试" if isinstance(error, ValueError)
                      else "默认模型请求失败，请检查连接或额度后重试")
            self.knowledge.fail(doc["id"], job, reason)
        finally:
            KNOWLEDGE_SLOTS.release()

    def config_text(self):
        return self.config.read_text() if self.config.exists() else "{}"

    def quick_templates(self, *, today=None):
        template = json.loads((STATIC.parent / "templates/quick-setup.json").read_text())
        today = today or datetime.now(UTC).date().isoformat()
        for provider in template["providers"]:
            provider["models"] = {
                name: model for name, model in provider["models"].items()
                if not model.get("available_before") or today < model["available_before"]
            }
        template["providers"] = [p for p in template["providers"] if p["models"]]
        return template

    def quick_catalog(self):
        # Public picker data deliberately excludes connection settings.
        return {"providers": [{
            "id": p["id"], "label": p["label"], "key_hint": p.get("key_hint", ""),
            "models": {name: {key: value for key, value in model.items()
                               if key in ("label", "generation", "description")}
                       for name, model in p["models"].items()},
        } for p in self.quick_templates()["providers"]], "revision": revision(self.config_text())}

    def quick_setup(self, data):
        template = self.quick_templates()
        catalog = {p["id"]: p for p in template["providers"]}
        identity, model, key = data.get("provider"), data.get("model"), data.get("api_key")
        if not isinstance(identity, str) or identity not in catalog:
            raise ValueError("请选择有效的厂商")
        preset = catalog[identity]
        if not isinstance(model, str) or model not in preset["models"]:
            raise ValueError("请选择该厂商下的有效模型")
        if not isinstance(key, str) or not key.strip() or len(key) > 4096 or any(c in key for c in "\r\n\x00"):
            raise ValueError("请填写有效的 API Key")
        # Connection settings come exclusively from the maintained server template.
        config = copy.deepcopy(template["base"])
        model_settings = {k: copy.deepcopy(v) for k, v in preset["models"][model].items()
                          if k in ("description", "context_window", "reasoning_effort",
                                   "supported_reasoning_levels", "default_reasoning_level")}
        config["providers"] = {identity: {
            "base_url": preset["base_url"], "api_mode": preset["api_mode"],
            "api_key": key.strip(), "models": {model: model_settings},
        }}
        config["default"] = {"provider": identity, "model": model}
        content = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        with LOCK:
            self.validate_config(content)
            self.write_config(content, data.get("revision"), "快速配置 / 模板生成")
        return {"message": "配置已生效，新启动的 Corki 将使用此配置"}

    def quick_add(self, data):
        catalog = {p["id"]: p for p in self.quick_templates()["providers"]}
        identity, selected_models = data.get("provider"), data.get("models")
        if not isinstance(identity, str) or identity not in catalog:
            raise ValueError("请选择有效的厂商")
        preset = catalog[identity]
        if (not isinstance(selected_models, list) or not selected_models
                or len(selected_models) > len(preset["models"])
                or any(not isinstance(model, str) or model not in preset["models"]
                       for model in selected_models)
                or len(set(selected_models)) != len(selected_models)):
            raise ValueError("请选择该厂商下的有效模型")
        key = data.get("api_key", "")
        if not isinstance(key, str) or len(key) > 4096 or any(c in key for c in "\r\n\x00"):
            raise ValueError("请填写有效的 API Key")
        key = key.strip()
        model_settings = {model: {
            k: copy.deepcopy(v) for k, v in preset["models"][model].items()
            if k in ("description", "context_window", "reasoning_effort",
                     "supported_reasoning_levels", "default_reasoning_level")
        } for model in selected_models}
        with LOCK:
            text = self.config_text()
            if data.get("revision") != revision(text):
                raise ValueError("配置已被其他程序更新，请刷新后重试")
            config = document.loads(text)
            providers = config.get("providers", {})
            if not isinstance(providers, dict):
                raise ValueError("供应商配置格式无效")
            existing = providers.get(identity)
            if any(not isinstance(profile, dict) for profile in providers.values()):
                raise ValueError("供应商配置格式无效")
            if existing is None:
                if any(identity in profile.get("aliases", []) for profile in providers.values()):
                    raise ValueError("供应商名称已被别名占用")
                if not key:
                    raise ValueError("请填写 API Key")
                profile = {"base_url": preset["base_url"], "api_mode": preset["api_mode"],
                           "api_key": key, "models": model_settings}
                text = document.set_value(text, ("providers", identity), profile)
            else:
                models = existing.get("models", {})
                if not isinstance(models, dict):
                    raise ValueError("模型配置格式无效")
                if any(model in models for model in selected_models):
                    raise ValueError("所选模型中有已存在的模型，请重新选择")
                if existing.get("api_key"):
                    if key:
                        raise ValueError("该供应商已配置密钥，快速添加不会替换现有密钥")
                else:
                    if not key:
                        raise ValueError("请填写 API Key")
                    text = document.set_value(text, ("providers", identity, "api_key"), key)
                for model in selected_models:
                    text = document.set_value(
                        text, ("providers", identity, "models", model), model_settings[model]
                    )
            if not config.get("default") and not providers:
                text = document.set_value(
                    text, ("default",), {"provider": identity, "model": selected_models[0]}
                )
            self.validate_config(text)
            self.write_config(text, data["revision"], "快速添加模型")
        return {"message": f"已添加 {len(selected_models)} 个模型；现有默认模型保持不变" if providers else f"已添加 {len(selected_models)} 个模型，并将第一个设为默认"}

    @staticmethod
    def desktop_script(script, args=(), timeout=30):
        if sys.platform != "darwin":
            raise ValueError("快速启动与目录选择目前支持 macOS")
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-", *args], input=script,
                text=True, capture_output=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError("系统操作超时，请检查桌面上的授权或目录选择窗口") from error
        if result.returncode:
            if "-128" in result.stderr:
                return None
            if "-1743" in result.stderr or "not authorized" in result.stderr.lower():
                raise ValueError("请在系统设置的隐私与安全性 → 自动化中允许打开终端")
            raise ValueError("无法完成系统操作，请检查终端安装状态和系统自动化权限")
        return result.stdout.rstrip("\r\n")

    def choose_launch_directory(self):
        path = self.desktop_script('return POSIX path of (choose folder with prompt "选择 Corki 工作目录")', timeout=120)
        return {"path": path, "cancelled": path is None}

    def desktop_application(self):
        local = Path(__file__).resolve().parents[1] / "app/out"
        candidates = [*sorted(local.glob("Corki-darwin-*/Corki.app")),
                      Path("/Applications/Corki.app"), Path.home() / "Applications/Corki.app"]
        for bundle in candidates:
            executable = bundle / "Contents/MacOS/Corki"
            if executable.is_file() and os.access(executable, os.X_OK):
                return executable
        raise ValueError("未找到 Corki 桌面应用，请先安装或打包桌面应用")

    def quick_launch(self, data):
        mode = data.get("mode", "cli")
        if mode not in ("cli", "desktop"):
            raise ValueError("请选择有效的启动方式")
        if sys.platform != "darwin":
            raise ValueError("快速启动目前支持 macOS")
        message = "配置无效，建议使用快速配置配置厂商和模型"
        try:
            content = self.config_text()
            self.validate_config(content)
            config = document.loads(content)
            default = config.get("default")
            providers = config.get("providers")
            if not isinstance(default, dict) or not isinstance(providers, dict):
                raise ValueError(message)
            provider, model = default.get("provider"), default.get("model")
            if not isinstance(provider, str) or not provider.strip() or not isinstance(model, str) or not model.strip():
                raise ValueError(message)
            profile = providers.get(provider)
            if profile is None:
                profile = next((item for item in providers.values()
                                if isinstance(item, dict) and provider in item.get("aliases", [])), None)
            if not isinstance(profile, dict) or not isinstance(profile.get("models"), dict) or model not in profile["models"]:
                raise ValueError(message)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise ValueError(message) from error
        value = data.get("directory", "")
        if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in "\x00\r\n"):
            raise ValueError("请输入有效的工作目录")
        path = Path(value.strip()).expanduser() if value.strip() else self.home / "main/default"
        if not path.is_absolute():
            raise ValueError("请输入完整目录路径，或使用 ~/ 开头的路径")
        if not value.strip():
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.is_dir():
            raise ValueError("工作目录不存在，请重新选择")
        if mode == "desktop":
            executable = self.desktop_application()
            environment = os.environ.copy()
            environment.pop("ELECTRON_RUN_AS_NODE", None)
            environment["CORKI_HOME"] = str(self.home)
            environment["CORKI_PYTHON"] = sys.executable
            try:
                subprocess.Popen(
                    [str(executable), "--corki-directory", str(path.resolve())],
                    cwd=str(path.resolve()), env=environment,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as error:
                raise ValueError("无法启动 Corki 桌面应用，请检查安装是否完整") from error
            return {"message": "已请求打开 Corki 桌面应用", "directory": str(path.resolve()), "mode": mode}
        executable = shutil.which("corki")
        if not executable:
            executable = next((str(p) for p in (Path.home()/".local/bin/corki", Path(sys.executable).parent/"corki") if p.is_file() and os.access(p, os.X_OK)), None)
        if not executable:
            raise ValueError("未找到 corki 命令，请先安装 Corki")
        path = path.resolve()
        command = f"cd -- {shlex.quote(str(path))} && env CORKI_HOME={shlex.quote(str(self.home))} {shlex.quote(executable)}"
        # Resolve through Launch Services, including applications outside /Applications.
        located = subprocess.run(["/usr/bin/osascript", "-e", 'POSIX path of (path to application id "com.googlecode.iterm2")'], capture_output=True, text=True, timeout=10, check=False)
        if located.returncode == 0:
            terminal = "iTerm"
            script = '''on run argv
tell application id "com.googlecode.iterm2"
activate
set newWindow to (create window with default profile)
tell current session of newWindow to write text (item 1 of argv)
end tell
end run'''
        else:
            terminal = "Terminal"
            script = '''on run argv
tell application id "com.apple.Terminal"
activate
do script (item 1 of argv)
end tell
end run'''
        self.desktop_script(script, [command])
        return {"message": f"已在 {terminal} 启动 Corki", "directory": str(path), "terminal": terminal}

    def config_versions(self):
        folder = self.home / "manager/config-versions"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder.chmod(0o700)
        return folder

    def archive_config(self, text, reason):
        folder = self.config_versions()
        entries = sorted(folder.glob("*.json"), reverse=True)
        if entries:
            latest = json.loads(entries[0].read_text())
            if latest["revision"] == revision(text):
                return latest["id"]
        identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex
        value = dict(
            id=identity,
            created=datetime.now(UTC).isoformat(),
            revision=revision(text),
            reason=reason,
            content=text,
        )
        atomic(folder / (identity + ".json"), json.dumps(value, ensure_ascii=False), backup=False)
        return identity

    def config_document(self, identity=None):
        with LOCK:
            current = self.config_text()
            self.archive_config(current, "当前配置 / 外部更新")
            versions = []
            selected = None
            for path in sorted(self.config_versions().glob("*.json"), reverse=True):
                value = json.loads(path.read_text())
                content = value.pop("content")
                if value["id"] == identity:
                    selected = content
                versions.append(value)
            if identity and selected is None:
                raise ValueError("配置版本不存在")
            return dict(
                content=current if identity is None else selected,
                revision=revision(current),
                versions=versions[:CONFIG_HISTORY_LIMIT],
                path=str(self.config),
                display_path=(
                    "~/" + str(self.config.relative_to(Path.home()))
                    if self.config.is_relative_to(Path.home())
                    else str(self.config)
                ),
            )

    def validate_config(self, text):
        if not isinstance(text, str) or len(text.encode()) > LIMIT:
            raise ValueError("配置不能超过 256 KB")
        if not text.strip():
            raise ValueError("配置不能为空，请输入 JSON 对象")
        document.loads(text)
        self.home.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.home, suffix=".json", prefix=".manager-validate-")
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(text)
            CorkiSettings.for_directory(self.workspace, config_file=Path(name))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError("Corki 配置校验失败，请检查默认模型、字段类型和参数范围") from error
        finally:
            Path(name).unlink(missing_ok=True)

    def write_config(self, text, expected, reason):
        if revision(self.config_text()) != expected:
            raise ValueError("配置写入冲突，请刷新后重试")
        self.archive_config(self.config_text(), "修改前配置")
        atomic(self.config, text)
        self.archive_config(text, reason)

    def redaction_secrets(self, config=None):
        def secrets(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if (
                        any(
                            k in key.lower()
                            for k in ("key", "token", "password", "secret", "authorization")
                        )
                        and isinstance(child, str)
                        and child
                    ):
                        yield child
                    else:
                        yield from secrets(child)
            elif isinstance(value, list):
                for child in value:
                    yield from secrets(child)

        if config is None:
            config = document.loads(self.config_text())
        # Short local gateway placeholders can be ordinary names or words.
        return sorted(
            (value for value in set(secrets(config)) if len(value) >= 8),
            key=len,
            reverse=True,
        )

    def redact(self, text, redaction_secrets=None):
        # Keep the explicit token-pattern pass for recognizable short tokens.
        if redaction_secrets is None:
            redaction_secrets = self.redaction_secrets()
        for secret in redaction_secrets:
            text = text.replace(secret, "[已隐藏]")
        text = re.sub(r'(?i)(Bearer\s+)[^\s"\\]+|\bsk-[\w-]+|\bgh[pousr]_[\w]+', "[已隐藏]", text)
        return text

    def files(self, redaction_secrets=None):
        result = []
        if redaction_secrets is None:
            redaction_secrets = self.redaction_secrets()
        roots = [
            (self.home / "skills", "skills", True),
            (self.home / "skills/.system", "skills", False),
            (self.compatibility_home / ".agents/skills", "skills", True),
            (self.workspace / ".corki/skills", "skills", False),
            (self.workspace / ".agents/skills", "skills", False),
            (self.home / "manager/knowledge", "knowledge", True),
            (self.home / "memories", "memories", True),
        ]
        seen = set()
        for root, kind, writable in roots:
            if not root.exists():
                continue
            paths = walk_skill_files(root).files if kind == "skills" else root.rglob("*.md")
            for path in sorted(paths):
                if ".manager-backups" in path.parts or path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root.resolve()) or resolved in seen:
                    continue
                seen.add(resolved)
                builtin = (
                    kind == "skills"
                    and resolved.is_relative_to((self.home / "skills/.system").resolve())
                ) or (
                    kind == "knowledge"
                    and resolved.is_relative_to((self.knowledge_root / ".system").resolve())
                )
                if path.stat().st_size > LIMIT:
                    continue
                text = path.read_text(errors="replace")
                name, description = path.stem, ""
                valid = True
                if kind == "skills":
                    try:
                        name, description, _ = parse_skill(text, path.parent.name)
                    except ValueError:
                        valid = False
                        name, description = path.parent.name, "技能文件格式需要检查"
                else:
                    name = next(
                        (
                            line.lstrip("# ").strip()
                            for line in text.splitlines()
                            if line.startswith("# ")
                        ),
                        path.stem,
                    )
                    description = next(
                        (
                            line.strip()
                            for line in text.splitlines()
                            if line.strip() and not line.startswith(("#", "---"))
                        ),
                        "",
                    )
                knowledge = self.knowledge.sync(path, name, text) if kind == "knowledge" else None
                result.append(
                    dict(
                        id=revision(str(path)),
                        kind=kind,
                        name=name,
                        description=self.redact(description[:180], redaction_secrets),
                        path=str(path),
                        writable=writable and not builtin,
                        valid=valid,
                        **(
                            {"enabled": bool(knowledge["enabled"]),
                             "document_id": knowledge["id"],
                             "index_status": knowledge["status"],
                             "index_error": knowledge["error"],
                             "index_summary": knowledge["summary"]}
                            if kind == "knowledge" else {}
                        ),
                        group=(
                            "builtin"
                            if builtin
                            else "user"
                            if kind == "knowledge"
                            else "public"
                            if root == self.compatibility_home / ".agents/skills"
                            else "corki"
                        ),
                        source=(
                            "内置"
                            if builtin
                            else "用户"
                            if kind == "knowledge"
                            else "Corki 专属"
                            if root == self.home / "skills"
                            else "项目"
                            if root.is_relative_to(self.workspace)
                            else "公共"
                        ),
                        bytes=path.stat().st_size,
                        updated=datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                        revision=revision(text),
                    )
                )
                if len(result) >= 1200:
                    return result
        return result

    def query(self, sql, args=()):
        if not self.database.exists():
            return []
        with sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, args)]

    def monthly_usage(self, today=None):
        today = today or datetime.now().astimezone().date()
        first = today - timedelta(days=29)
        rows = self.query(
            "SELECT date(created_at,'localtime') AS day, "
            "coalesce(sum(input_tokens),0) AS input, "
            "coalesce(sum(output_tokens),0) AS output, count(*) AS steps "
            "FROM model_steps WHERE date(created_at,'localtime') BETWEEN ? AND ? "
            "GROUP BY day",
            (first.isoformat(), today.isoformat()),
        )
        by_day = {row["day"]: row for row in rows}
        days = [(first + timedelta(days=index)).isoformat() for index in range(30)]
        return [by_day.get(day, dict(day=day, input=0, output=0, steps=0)) for day in days]

    def hooks_document(self):
        from corki.config.hooks import HOOK_EVENTS
        from corki.core.stop_hooks import command_identity

        text = self.config_text()
        config = document.loads(text)
        path = self.home / "hooks.json"
        raw = path.read_text() if path.exists() else ""
        external = json.loads(raw) if raw.strip() else {}
        if not isinstance(external, dict) or not isinstance(external.get("hooks", {}), dict):
            raise ValueError("hooks.json 格式无效，请先修复配置文件")
        if not isinstance(config.get("hooks", {}), dict):
            raise ValueError("settings.json 中 hooks 必须为对象")
        states = config.get("hooks", {}).get("state", {})
        if not isinstance(states, dict):
            raise ValueError("hooks.state 必须为对象")
        entries = []
        for source, filename, events in [("settings", self.config, config.get("hooks", {})),
                                          ("file", path, external.get("hooks", {}))]:
            for event in HOOK_EVENTS:
                groups = events.get(event, [])
                if not isinstance(groups, list):
                    raise ValueError(f"{event} 必须为数组")
                for gi, group in enumerate(groups):
                    if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                        raise ValueError(f"{event} 配置组无效")
                    for hi, handler in enumerate(group.get("hooks", [])):
                        event_key = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
                        key = f"{filename}:{event_key}:{gi}:{hi}"
                        rule = states.get(key, states.get("file:" + key, {}))
                        if not isinstance(rule, dict):
                            raise ValueError("Hook 信任配置无效")
                        error = None
                        try:
                            fingerprint, _ = command_identity(handler, event_name=event, matcher=group.get("matcher"))
                            trusted = rule.get("trusted_hash") == fingerprint
                        except (ValueError, TypeError, AttributeError) as exc:
                            trusted = False
                            error = str(exc)
                        entries.append(dict(id=f"{source}:{event}:{gi}:{hi}", source=source,
                                            event=event, matcher=group.get("matcher", "*"), handler=handler if isinstance(handler, dict) else {},
                                            enabled=rule.get("enabled", True) is True and trusted,
                                            trusted=trusted, error=error, key=key))
        skill_files = [f for f in self.files() if f["kind"] == "skills" and f.get("valid", True)]
        disabled = disabled_skill_paths(parse_skill_rules(config.get("skills", {}).get("config", []), self.home),
                                        ((f["name"], Path(f["path"]).resolve()) for f in skill_files))
        skills = [{"name": f["name"], "path": f["path"], "group": f.get("group", ""),
                   "enabled": Path(f["path"]).resolve() not in disabled} for f in skill_files]
        return dict(entries=entries, skills=skills, enabled=config.get("features", {}).get("hooks", True),
                    revision=revision(text), file_revision=revision(raw), events=list(HOOK_EVENTS))

    def discard_hook_upload(self, value):
        if not isinstance(value, str):
            return
        root = self.home / "hooks" / "skills"
        path = Path(value)
        if path.name != "SKILL.md" or path.parent.parent != root or not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
            return
        def references(value):
            if isinstance(value, dict):
                return (value.get("type") == "skill" and value.get("path") == str(path)) or any(references(v) for v in value.values())
            if isinstance(value, list):
                return any(references(v) for v in value)
            return False
        config = document.loads(self.config_text())
        hook_file = self.home / "hooks.json"
        external = json.loads(hook_file.read_text()) if hook_file.exists() else {}
        if references(config) or references(external):
            return
        if path.parent.is_symlink() or not path.parent.exists():
            return
        shutil.rmtree(path.parent)

    def mutate_hooks(self, data):
        from corki.config.hooks import HOOK_EVENTS
        from corki.core.stop_hooks import command_identity

        with LOCK:
            if data.get("action") == "discard-upload":
                self.discard_hook_upload(data.get("path"))
                return {"message": "未使用的上传已清理"}
            snapshot = self.hooks_document()
            if data.get("revision") != snapshot["revision"] or data.get("file_revision") != snapshot["file_revision"]:
                raise ValueError("配置已更新，请刷新后重试")
            text = self.config_text()
            config = document.loads(text)
            action = data.get("action")
            if action == "upload-skill":
                upload = data.get("upload")
                if not isinstance(upload, dict):
                    raise ValueError("请选择技能文件夹、ZIP 或 SKILL.md")
                result = self.import_skill({**upload, "name": uuid.uuid4().hex}, private=True)
                return {"path": str(Path(result["path"]) / "SKILL.md"), "name": result["name"]}
            if action == "global":
                if type(data.get("enabled")) is not bool:
                    raise ValueError("无效的开关值")
                updated = document.set_value(text, ("features", "hooks"), data["enabled"])
                self.write_config(updated, snapshot["revision"], "修改 Hooks 总开关")
                return {"message": "Hooks 总开关已更新"}
            if action not in ("save", "toggle", "delete"):
                raise ValueError("不支持的 Hook 操作")
            identity = data.get("id")
            old = next((e for e in snapshot["entries"] if e["id"] == identity), None)
            if identity and old is None:
                raise ValueError("Hook 不存在，请刷新后重试")
            if action != "save" and old is None:
                raise ValueError("请选择 Hook")
            source = old["source"] if old else "settings"
            path = self.home / "hooks.json"
            original = path.read_text() if path.exists() else ""
            external = json.loads(original) if original.strip() else {}
            hooks = config.setdefault("hooks", {})
            events = hooks if source == "settings" else external.setdefault("hooks", {})
            states = hooks.setdefault("state", {})
            filename = self.config if source == "settings" else path
            # Keep each surviving handler's original trust while numeric indices shift.
            saved_states = {}
            for entry in snapshot["entries"]:
                if entry["source"] != source:
                    continue
                _, event, gi, hi = entry["id"].split(":")
                handler = events[event][int(gi)]["hooks"][int(hi)]
                saved_states[id(handler)] = copy.deepcopy(states.get(entry["key"], states.get("file:" + entry["key"], {})))
                states.pop(entry["key"], None)
                states.pop("file:" + entry["key"], None)
            if old:
                _, event, gi, hi = identity.split(":")
                group = events[event][int(gi)]
                handler = group["hooks"][int(hi)]
                if action in ("delete", "save"):
                    group["hooks"].pop(int(hi))
                    if not group["hooks"]:
                        events[event].pop(int(gi))
                else:
                    if type(data.get("enabled")) is not bool:
                        raise ValueError("无效的开关值")
                    rule = saved_states[id(handler)]
                    rule["enabled"] = data["enabled"]
                    if data["enabled"]:
                        rule["trusted_hash"], _ = command_identity(handler, event_name=event, matcher=group.get("matcher"))
            if action == "save":
                event, matcher, handler = data.get("event"), data.get("matcher", "*"), data.get("handler")
                if event not in HOOK_EVENTS or not isinstance(matcher, str) or not isinstance(handler, dict):
                    raise ValueError("请填写有效的事件、匹配条件和处理器")
                if type(data.get("enabled")) is not bool:
                    raise ValueError("请选择启用状态")
                fingerprint, _ = command_identity(handler, event_name=event, matcher=matcher)
                if handler.get("type") == "skill":
                    from corki.core.context_hooks import render_context
                    render_context(handler)
                if handler.get("type") == "mcp_tool":
                    if handler["server"] not in config.get("mcp", {}).get("servers", {}):
                        raise ValueError("请选择已配置的 MCP 服务")
                events.setdefault(event, []).append({"matcher": matcher, "hooks": [handler]})
                saved_states[id(handler)] = {"enabled": data["enabled"], "trusted_hash": fingerprint}
            for event in HOOK_EVENTS:
                event_key = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
                for gi, group in enumerate(events.get(event, [])):
                    for hi, handler in enumerate(group.get("hooks", [])):
                        rule = saved_states.get(id(handler), {})
                        if rule:
                            states[f"{filename}:{event_key}:{gi}:{hi}"] = rule
            updated = document.set_value(text, ("hooks",), hooks)
            self.write_config(updated, snapshot["revision"], "更新 Hooks")
            if source == "file":
                try:
                    atomic(path, document.dumps(external))
                except OSError:
                    atomic(self.config, text)
                    raise
            if old and old["handler"].get("type") == "skill" and action in ("save", "delete"):
                try:
                    self.discard_hook_upload(old["handler"].get("path"))
                except OSError:
                    pass  # Configuration is committed; cleanup must not imply save failure.
            return {"message": "Hook 已删除" if action == "delete" else "Hook 已保存；新会话使用更新后的配置"}

    def snapshot(self):
        text = self.config_text()
        knowledge_text, _ = self.knowledge_state()
        config = document.loads(text)
        redaction_secrets = self.redaction_secrets(config)
        providers = []
        for name, p in config.get("providers", {}).items():
            providers.append(
                dict(
                    id=name,
                    base_url=p.get("base_url", ""),
                    api_mode=p.get("api_mode", "chat_completions"),
                    has_key=bool(p.get("api_key")),
                    models=p.get("models", {}),
                )
            )
        files = self.files(redaction_secrets)
        rules = parse_skill_rules(config.get("skills", {}).get("config", []), self.home)
        disabled = disabled_skill_paths(
            rules, ((f["name"], Path(f["path"]).resolve()) for f in files if f["kind"] == "skills")
        )
        for item in files:
            if item["kind"] == "skills":
                item["enabled"] = Path(item["path"]).resolve() not in disabled
        tools = []
        for name, tool in config.get("mcp", {}).get("servers", {}).items():
            tools.append(
                dict(
                    id=name,
                    transport=tool.get("transport", "http" if tool.get("url") else "stdio"),
                    url=tool.get("url", ""),
                    command=tool.get("command", ""),
                    enabled=tool.get("enabled", True),
                )
            )
        errors = []
        sessions, usage, totals, executions = [], [], {}, []
        try:
            sessions = self.query("""SELECT t.id,t.cwd,t.updated_at,t.preview,
                (SELECT status FROM turns WHERE thread_id=t.id
                    ORDER BY updated_at DESC LIMIT 1) AS status,
                (SELECT model FROM thread_model_settings WHERE thread_id=t.id) AS model
                FROM threads t ORDER BY t.updated_at DESC LIMIT 40""")
            usage = self.monthly_usage()
            totals = next(
                iter(
                    self.query(
                        "SELECT count(*) AS steps, coalesce(sum(input_tokens),0) AS input, "
                        "coalesce(sum(output_tokens),0) AS output, "
                        "coalesce(sum(cached_tokens),0) AS cached FROM model_steps"
                    )
                ),
                {},
            )
            totals["sessions"] = next(
                iter(self.query("SELECT count(*) AS n FROM threads")), {"n": 0}
            )["n"]
            executions = self.query(
                "SELECT call_id,thread_id,tool_name,status,started_at,completed_at "
                "FROM tool_executions ORDER BY started_at DESC LIMIT 80"
            )
        except sqlite3.Error:
            errors.append("会话数据库暂不可读，配置管理仍可使用。")
        with socket.socket() as sock:
            sock.settimeout(0.15)
            gateway = sock.connect_ex(("127.0.0.1", 17001)) == 0
        return json.loads(
            self.redact(
                json.dumps(
                    dict(
                        revision=revision(text),
                        knowledge_revision=revision(knowledge_text),
                        providers=providers,
                        default=config.get("default", {}),
                        files=files,
                        tools=tools,
                        custom_tools=self.custom_tools(),
                        sessions=sessions,
                        usage=usage,
                        totals=totals,
                        executions=executions,
                        errors=errors,
                        gateway=gateway,
                        home=str(self.home),
                        workspace=str(self.workspace),
                        updated=datetime.now(UTC).isoformat(),
                    ),
                    ensure_ascii=False,
                ),
                redaction_secrets,
            )
        )

    def archive_knowledge(self, item, content):
        folder = self.home / "manager/knowledge-versions" / item["document_id"]
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        entries = sorted(folder.glob("*.json"), reverse=True)
        if not entries or json.loads(entries[0].read_text())["revision"] != revision(content):
            identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex
            atomic(folder / (identity + ".json"), json.dumps(dict(
                id=identity, created=datetime.now(UTC).isoformat(),
                revision=revision(content), content=content,
            ), ensure_ascii=False), backup=False)
        for old in sorted(folder.glob("*.json"), reverse=True)[CONFIG_HISTORY_LIMIT:]:
            old.unlink()
        return folder

    def knowledge_document(self, identity):
        with LOCK:
            item = self.read_file(identity)
            if item["kind"] != "knowledge":
                raise ValueError("知识文档不存在")
            versions = []
            if item["group"] != "builtin":
                raw = Path(item["path"]).read_text()
                folder = self.archive_knowledge(item, raw)
                for path in sorted(folder.glob("*.json"), reverse=True):
                    value = json.loads(path.read_text())
                    raw_content = value["content"]
                    value["content"] = self.redact(raw_content)
                    value["writable"] = item["writable"] and raw_content == value["content"]
                    versions.append(value)
            return {**item, "versions": versions}

    def read_file(self, identity):
        item = next((x for x in self.files() if x["id"] == identity), None)
        if item is None:
            raise ValueError("文件不存在或不属于可管理目录")
        text = Path(item["path"]).read_text()
        redacted = text if item["kind"] == "skills" else self.redact(text)
        return {**item, "content": redacted, "writable": item["writable"] and redacted == text}

    def skill_package(self, identity):
        item = next(
            (f for f in self.files() if f["id"] == identity and f["kind"] == "skills"), None
        )
        if item is None:
            raise ValueError("技能不存在，请刷新列表")
        root = Path(item["path"]).parent
        entries = []
        pending = [root]
        while pending:
            directory = pending.pop()
            for path in sorted(directory.iterdir()):
                if path.name in {".manager-backups", ".git", "__pycache__", ".DS_Store"}:
                    continue
                if len(entries) >= 2000:
                    raise ValueError("技能文件过多，最多浏览 2000 个目录和文件")
                relative = path.relative_to(root).as_posix()
                if path.is_symlink():
                    entries.append(dict(path=relative, type="link", bytes=0))
                elif path.is_dir():
                    entries.append(dict(path=relative, type="directory", bytes=0))
                    pending.append(path)
                elif path.is_file():
                    entries.append(dict(path=relative, type="file", bytes=path.stat().st_size))
        return {**item, "root": str(root), "entries": entries}

    def skill_resource(self, identity, relative, package=None):
        package = package or self.skill_package(identity)
        if not isinstance(relative, str) or "\\" in relative:
            raise ValueError("无效的技能文件路径")
        entry = next((e for e in package["entries"] if e["path"] == relative), None)
        if not entry or entry["type"] != "file":
            raise ValueError("文件不存在或为不可访问的链接")
        root = Path(package["root"])
        path = root / relative
        if path.is_symlink() or any(
            p.is_symlink() for p in path.parents if p != root and root in p.parents
        ):
            raise ValueError("不支持访问符号链接")
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("文件路径超出技能目录")
        return package, path

    def read_skill_resource(self, identity, relative):
        package, path = self.skill_resource(identity, relative)
        length = path.stat().st_size
        result = dict(path=relative, bytes=length, writable=False, type="binary")
        if length > LIMIT:
            return {**result, "type": "large", "message": "文件超过 256 KB，请导出后查看"}
        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8")
            if "\x00" in content:
                raise UnicodeError()
        except UnicodeError:
            mime = mimetypes.guess_type(path.name)[0]
            if mime in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                result.update(
                    type="image", data="data:" + mime + ";base64," + base64.b64encode(raw).decode()
                )
            return result
        return {
            **result,
            "type": "text",
            "content": content,
            "revision": revision(content),
            "writable": package["writable"],
            "redacted": False,
        }

    @staticmethod
    def validate_skill_name_unchanged(content, previous, directory):
        name, _, _ = parse_skill(content, directory)
        old_name, _, _ = parse_skill(previous, directory)
        if name != old_name:
            raise ValueError("不支持修改 Skills 名称，请保留 SKILL.md 原有的 name")

    def save_skill_resource(self, data):
        package, path = self.skill_resource(data.get("id"), data.get("path"))
        if not package["writable"]:
            raise ValueError("内置或项目技能只读")
        current = self.read_skill_resource(data.get("id"), data.get("path"))
        if not current["writable"]:
            raise ValueError("此文件只读")
        if data.get("revision") != current.get("revision"):
            raise ValueError("文件已被其他程序修改；草稿已保留，请重新读取后合并")
        content = data.get("content")
        if not isinstance(content, str) or len(content.encode()) > LIMIT:
            raise ValueError("文本文件不能超过 256 KB")
        if path.name == "SKILL.md":
            if len(content.encode()) > 128 * 1024:
                raise ValueError("SKILL.md 不能超过 128 KB")
            self.validate_skill_name_unchanged(content, current["content"], path.parent.name)
        mode = stat.S_IMODE(path.stat().st_mode)
        atomic(path, content)
        path.chmod(mode)
        return {"message": "文件已保存", "revision": revision(content)}

    def export_skill(self, identity, *, stream=False):
        package = self.skill_package(identity)
        root = Path(package["root"])
        buffer = tempfile.TemporaryFile(prefix="corki-skill-export-", suffix=".zip")
        try:
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED,
                                 strict_timestamps=False) as archive:
                for entry in package["entries"]:
                    if entry["type"] == "link":
                        continue
                    if entry["type"] == "directory":
                        archive.writestr(root.name + "/" + entry["path"] + "/", b"")
                        continue
                    _, path = self.skill_resource(identity, entry["path"], package)
                    archive.write(path, arcname=root.name + "/" + entry["path"])
            buffer.seek(0)
            if stream:
                return buffer, root.name + ".zip"
            return buffer.read(), root.name + ".zip"
        finally:
            if not stream or sys.exc_info()[0] is not None:
                buffer.close()

    def cron_run(self, identity):
        row = self.cron.run(identity)
        if row["status"] == "failed":
            log = row.get("error_log", "")
            if not log and row.get("session_id"):
                try:
                    errors = self.query(
                        "SELECT updated_at,error FROM turns WHERE thread_id=? "
                        "AND error IS NOT NULL AND error != '' ORDER BY updated_at LIMIT 20",
                        (row["session_id"],),
                    )
                    log = "\n\n".join(f"{e['updated_at']}\n{e['error']}" for e in errors)
                except (OSError, sqlite3.Error):
                    pass
            row["error_log"] = safe_log(self.home, log or row["error"])
        return row

    def execution(self, identity):
        rows = self.query(
            "SELECT tool_name,status,result_json FROM tool_executions WHERE call_id=?", (identity,)
        )
        if not rows:
            raise ValueError("执行记录不存在")
        row = rows[0]
        data = json.loads(row.pop("result_json") or "{}")
        return {
            **row,
            "content": self.redact(
                str(data.get("display_content") or data.get("content") or "暂无输出")[:18000]
            ),
        }

    def custom_tool_path(self, identity):
        if not isinstance(identity, str) or not CUSTOM_TOOL_NAME.fullmatch(identity):
            raise ValueError("工具名称无效")
        path = self.home / "tools" / "custom" / (identity + ".json")
        if path.is_symlink():
            raise ValueError("不支持符号链接工具配置")
        return path

    def custom_tools(self):
        rows = []
        for path in custom_tool_files(self.home):
            try:
                data = read_custom_tool(path)
                rows.append(
                    dict(
                        id=path.stem,
                        name=data["name"],
                        description=data["description"],
                        transport=data["transport"],
                        enabled=data.get("enabled", True),
                        revision=revision(path.read_text()),
                        error=None,
                    )
                )
            except (ValueError, OSError):
                rows.append(
                    dict(
                        id=path.stem,
                        name=path.stem,
                        description="配置格式无效",
                        transport="",
                        enabled=False,
                        error="配置格式无效",
                        revision=revision(path.read_text()),
                    )
                )
        return rows

    def custom_tool_document(self, identity):
        path = self.custom_tool_path(identity)
        text = path.read_text()
        return {"config": json.loads(text), "revision": revision(text)}

    def mutate_custom_tool(self, data):
        with LOCK:
            identity = data.get("id")
            path = self.custom_tool_path(identity)
            exists = path.exists()
            text = path.read_text() if exists else ""
            if data.get("is_new") and exists:
                raise ValueError("同名工具已存在")
            if data.get("revision") != (revision(text) if exists else None):
                raise ValueError("工具配置已更新，请重新打开后修改")
            action = data.get("action")
            if action == "delete":
                if not exists:
                    raise ValueError("工具不存在")
                path.unlink()
            elif action in ("save", "toggle"):
                if action == "toggle":
                    if not exists:
                        raise ValueError("工具不存在")
                    value = json.loads(text)
                    value["enabled"] = data.get("enabled")
                else:
                    value = data.get("values")
                value = validate_custom_tool(value)
                if value["name"] != identity:
                    raise ValueError("工具名称不能更改，请新建工具")
                atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            else:
                raise ValueError("不支持的操作")
            return {"message": "已保存；新的 Corki 会话中生效"}

    def test_custom_tool(self, data):
        identity = data.get("id")
        path = self.custom_tool_path(identity)
        with LOCK:
            text = path.read_text() if path.exists() else None
            if data.get("revision") != (revision(text) if text is not None else None):
                raise ValueError("工具配置已更新，请重新打开后测试")
            if data.get("is_new") and text is not None:
                raise ValueError("同名工具已存在")
            if "values" in data:
                config = validate_custom_tool(data["values"])
            elif text is not None:
                config = validate_custom_tool(json.loads(text))
            else:
                raise ValueError("工具不存在")
            if config["name"] != identity:
                raise ValueError("工具名称与配置不一致")
        arguments = data.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("调用参数必须是 JSON 对象")
        expected = data.get("expected_format", "json")
        if expected not in ("json", "text"):
            raise ValueError("请选择 JSON 或文本返回格式")
        if not CUSTOM_TEST_SLOTS.acquire(blocking=False):
            raise ValueError("正在测试的工具较多，请稍后重试")
        started = time.monotonic()
        try:
            config = {**config, "timeout_seconds": min(config.get("timeout_seconds", 30), 30)}
            tool = CustomTool(config)
            registry = ToolRegistry()
            registry.register(tool)
            call = ToolCall(new_tool_call_id(), tool.spec.name, arguments)
            result = asyncio.run(
                ToolExecutor(registry, output_char_budget=1024 * 1024).execute(
                    call, ToolContext(self.workspace)
                )
            )
            output = result.content
            response = {"ok": False, "output": output, "format": "text"}
            if result.is_error:
                response["error"] = output[:1000] or "工具执行失败"
            elif not output.strip():
                response["error"] = "工具返回为空"
            elif expected == "json":
                try:

                    def reject_constant(value):
                        raise ValueError("无效 JSON 常量")

                    parsed = json.loads(output, parse_constant=reject_constant)
                    json.dumps(parsed, allow_nan=False)
                    response.update(ok=True, format="json", result=parsed)
                except (ValueError, RecursionError):
                    response["error"] = "执行完成，但返回内容不是有效的 JSON"
            else:
                response["ok"] = True
            response["duration_ms"] = round((time.monotonic() - started) * 1000)

            def redact(value):
                if isinstance(value, dict):
                    return {key: redact(child) for key, child in value.items()}
                if isinstance(value, list):
                    return [redact(child) for child in value]
                return self.redact(value) if isinstance(value, str) else value

            return redact(response)
        finally:
            CUSTOM_TEST_SLOTS.release()

    def test_mcp(self, data):
        text = self.config_text()
        if data.get("revision") != revision(text):
            raise ValueError("配置已更新，请刷新后重新测试")
        config = document.loads(text)
        identity = data.get("id")
        servers = config.get("mcp", {}).get("servers", {})
        if "values" in data:
            probe = self.mcp_form_config(identity, data["values"], servers, data.get("is_new"))
        else:
            if identity not in servers:
                raise ValueError("MCP 服务不存在")
            probe = servers[identity]
        settings = MCPServerSettings.from_mapping(identity, probe)
        if settings.environment_id != "local":
            raise ValueError("此服务需要在对应的远程运行环境中测试")
        if settings.transport == "stdio" and settings.cwd is None:
            settings = replace(settings, cwd=self.workspace)
        if not MCP_TEST_SLOTS.acquire(blocking=False):
            raise ValueError("正在测试的服务较多，请稍后重试")
        try:
            result = asyncio.run(self._test_mcp(settings, config))
            sensitive = {
                value
                for _, value in (*settings.env, *settings.headers, *(settings.http_headers or ()))
                if value
            }
            if settings.bearer_token_env_var:
                token = os.environ.get(settings.bearer_token_env_var)
                if token:
                    sensitive.add(token)
            for _, variable in settings.env_http_headers or ():
                if os.environ.get(variable):
                    sensitive.add(os.environ[variable])

            def redact_result(value):
                if isinstance(value, dict):
                    return {key: redact_result(child) for key, child in value.items()}
                if isinstance(value, (list, tuple)):
                    return [redact_result(child) for child in value]
                if isinstance(value, str):
                    for secret in sorted(sensitive, key=len, reverse=True):
                        value = value.replace(secret, "[已隐藏]")
                    return self.redact(value)
                return value

            return redact_result(result)
        finally:
            MCP_TEST_SLOTS.release()

    def prepare_model_test(self, data):
        provider = data.get("provider")
        model_id = data.get("model")
        if not isinstance(provider, str) or not isinstance(model_id, str):
            raise ValueError("请选择有效的供应商和模型")
        config = document.loads(self.config_text())
        profile = config.get("providers", {}).get(provider)
        if not isinstance(profile, dict) or model_id not in profile.get("models", {}):
            raise ValueError("模型配置已变化，请刷新页面后重试")
        messages = data.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 20:
            raise ValueError("测试对话最多保留最近 20 条消息")
        total = 0
        items = []
        turn_id = new_turn_id()
        for row in messages:
            if not isinstance(row, dict) or row.get("role") not in ("user", "assistant"):
                raise ValueError("测试消息格式无效")
            content = row.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 12000:
                raise ValueError("每条测试消息需为 1–12000 个字符")
            total += len(content)
            if total > 60000:
                raise ValueError("测试对话总长度不能超过 60000 个字符")
            if row["role"] == "user":
                items.append(UserMessageItem(content=content, turn_id=turn_id))
            else:
                items.append(
                    AssistantMessageItem(
                        content=content,
                        turn_id=turn_id,
                        step_id=new_model_step_id(),
                    )
                )
        if messages[-1]["role"] != "user":
            raise ValueError("请先输入一条测试消息")
        settings = CorkiSettings.for_directory(
            self.home,
            config_file=self.config,
            provider=provider,
            model=model_id,
        )
        settings = replace(
            settings,
            model_max_retries=0,
            model_request_max_retries=0,
            model_unbounded_connection_retries=False,
            execution_permissions=None,
        )
        request = ModelRequest(
            model=model_id,
            instructions="",
            context_items=(),
            items=tuple(items),
            tools=(),
            reasoning_effort=settings.reasoning_effort,
            model_info=settings.model_context_info(model_id),
        )
        return _create_model(settings, _model_capabilities(settings)), request

    async def stream_model_test(self, model, request, send_event):
        try:
            async with asyncio.timeout(180):
                async for event in model.stream(request):
                    if isinstance(event, ModelTextDelta) and event.delta:
                        send_event("delta", {"text": event.delta})
            send_event("done", {})
        except TimeoutError:
            send_event("error", {"message": "模型响应超时，请检查连接后重试"})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            message = str(error).strip() or type(error).__name__
            secret = getattr(model, "_api_key", "")
            if secret:
                message = message.replace(secret, "[已隐藏]")
            send_event("error", {"message": self.redact(message)[:500]})
        finally:
            try:
                await model.aclose()
            except Exception:
                pass

    async def _test_mcp(self, settings, config):
        started = time.monotonic()
        client = None
        response = None
        pages = []
        stage = "连接服务"
        result = {"ok": False, "server": settings.name}
        try:
            client = create_client(settings)
            if hasattr(client, "set_oauth_store"):
                client.set_oauth_store(self.home, config.get("mcp_oauth_credentials_store", "auto"))
            async with asyncio.timeout(min(settings.timeout_seconds, 30)):
                await client.start()
                stage = "校验返回格式"
                tools = await client.list_tools(pages.append)
                response = {"tools": list(tools)}
                names = set()
                for tool in tools:
                    if not tool["name"] or tool["name"] in names:
                        raise ValueError("工具名称为空或重复")
                    names.add(tool["name"])
                    mcp_input_schema(tool["inputSchema"])
                result.update(ok=True, tool_count=len(tools), server_info=client.server_info)
        except TimeoutError:
            result["error"] = f"{stage}超时，请检查服务后重试"
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            result["error"] = f"{stage}失败：{detail[:600]}"
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    result.update(ok=False, error="测试连接释放失败，请检查服务状态")
        result["duration_ms"] = round((time.monotonic() - started) * 1000)
        if response is None and pages:
            response = pages[0]
        if response is not None:
            result["response"] = plain_json(response)
        return result

    def mutate(self, data):
        if data.get("kind") == "hooks":
            return self.mutate_hooks(data)
        if data.get("kind") == "sql-workspace":
            return QueryWorkspace(self.home).mutate(data)
        if data.get("kind") == "database":
            if data.get("action") == "query":
                return DatabaseAdmin(self.home).query(data)
            return DatabaseAdmin(self.home).mutate(data)
        if data.get("kind") == "cron":
            action = data.get("action")
            identity = data.get("id")
            if action == "save":
                values = data.get("values")
                if not isinstance(values, dict) or not values.get("provider") or not values.get("model"):
                    raise ValueError("请选择模型供应商和运行模型")
                identity = self.cron.save(data.get("values"), identity, data.get("version"))
            elif action == "run":
                self.cron_service.run_now(identity)
            else:
                self.cron.change(identity, action, data.get("version"))
            return {"message": {"save": "任务已保存", "run": "任务已开始", "pause": "任务已暂停",
                                "resume": "任务已开启", "delete": "任务已删除",
                                "stop": "正在停止执行"}.get(action, "已更新"), "id": identity}
        if data.get("kind") == "quick-tools":
            action = data.get("action")
            if action == "configure":
                return self.quick_setup(data)
            if action == "add":
                return self.quick_add(data)
            if action == "choose-directory":
                return self.choose_launch_directory()
            if action == "launch":
                try:
                    return self.quick_launch(data)
                except subprocess.TimeoutExpired as error:
                    raise ValueError("检测终端超时，请稍后重试") from error
            raise ValueError("不支持的快捷操作")
        if data.get("kind") == "custom-tools":
            if data.get("action") == "test":
                return self.test_custom_tool(data)
            return self.mutate_custom_tool(data)
        if data.get("kind") == "tools" and data.get("action") == "test-connection":
            return self.test_mcp(data)
        if data.get("kind") == "knowledge":
            if data.get("action") == "parse-document":
                return parse_knowledge_document(data)
            if data.get("action") == "parse-web":
                url = data.get("url")
                if not isinstance(url, str) or len(url) > 4096:
                    raise ValueError("请输入有效的网页地址")
                return fetch_knowledge_page(url.strip())
        with LOCK:
            action, kind = data.get("action"), data.get("kind")
            if kind == "knowledge" and action == "retry":
                item = next((f for f in self.files() if f["id"] == data.get("id")
                             and f["kind"] == "knowledge"), None)
                if item is None or not item["enabled"]:
                    raise ValueError("请先开启知识文档")
                self.start_knowledge(item)
                return {"message": "正在生成摘要"}
            if kind == "knowledge" and action == "toggle":
                return self.toggle_knowledge(data)
            if kind == "config":
                if action == "validate":
                    self.validate_config(data.get("content"))
                    return {"message": "JSON / JSONC 与 Corki 配置校验通过"}
                if action not in ("save", "restore"):
                    raise ValueError("不支持的配置操作")
                if action == "restore" and not data.get("id"):
                    raise ValueError("请选择历史版本")
                content = (
                    self.config_document(data.get("id"))["content"]
                    if action == "restore"
                    else data.get("content")
                )
                self.validate_config(content)
                self.write_config(
                    content,
                    data.get("revision"),
                    "回滚历史版本" if action == "restore" else "编辑配置文件",
                )
                return {"message": "已创建并应用新版本；新 Corki 会话使用此配置"}
            if kind == "skills" and action == "delete":
                return self.delete_skill(data)
            if kind == "skills" and action == "rename":
                raise ValueError("不支持修改 Skills 名称")
            if kind == "skills" and action == "save-resource":
                return self.save_skill_resource(data)
            if kind == "skills" and action in ("import", "validate-import"):
                return self.import_skill(data, preview=action == "validate-import")
            if kind in ("skills", "knowledge", "memories") and action != "toggle":
                return self.mutate_file(data)
            text = self.config_text()
            if data.get("revision") != revision(text):
                raise ValueError("配置已被其他程序更新，请刷新后重试")
            config = document.loads(text)
            before = copy.deepcopy(config)
            values = data.get("values", {})
            identity = data.get("id", "")
            if kind == "providers":
                providers = config.setdefault("providers", {})
                if action == "delete":
                    if config.get("default", {}).get("provider") == identity:
                        raise ValueError("请先切换默认模型，再删除供应商")
                    providers.pop(identity, None)
                elif action == "save":
                    self.identifier(identity)
                    url = values.get("base_url", "").strip()
                    if (
                        urlparse(url).scheme not in ("http", "https")
                        or not urlparse(url).netloc
                        or urlparse(url).username
                    ):
                        raise ValueError("请输入有效的 HTTP(S) API 地址，不要在地址中填写凭据")
                    if values.get("api_mode") not in ("responses", "chat_completions"):
                        raise ValueError("不支持的接口协议")
                    if identity not in providers and any(
                        identity in profile.get("aliases", []) for profile in providers.values()
                    ):
                        raise ValueError("供应商名称已被别名占用")
                    new_identity = values.get("id", identity)
                    if isinstance(new_identity, str):
                        new_identity = new_identity.strip()
                    self.identifier(new_identity)
                    if new_identity != identity:
                        if identity not in providers:
                            raise ValueError("供应商不存在，请刷新后重试")
                        if new_identity in providers or any(
                            new_identity in profile.get("aliases", [])
                            for name, profile in providers.items()
                            if name != identity
                        ):
                            raise ValueError("供应商名称已存在或被其他供应商的别名占用")
                        profile = providers[identity]
                        # Retain old identities for saved sessions and explicit CLI selections.
                        aliases = [a for a in profile.get("aliases", []) if a != new_identity]
                        if identity not in aliases:
                            aliases.append(identity)
                        profile["aliases"] = aliases
                        # Profile identity is distinct from adapter capability detection.
                        profile.setdefault("name", identity if identity != "default" else None)
                        providers = {
                            new_identity if name == identity else name: entry
                            for name, entry in providers.items()
                        }
                        config["providers"] = providers
                        if config.get("default", {}).get("provider") == identity:
                            config["default"]["provider"] = new_identity
                        inline = config.get("provider", {})
                        if inline and inline.get("id", inline.get("name", "default")) == identity:
                            inline["id"] = new_identity
                        identity = new_identity
                    provider = providers.setdefault(identity, {"models": {}})
                    provider.update(base_url=url, api_mode=values["api_mode"])
                    if values.get("api_key"):
                        provider["api_key"] = values["api_key"]
                    if data.get("make_default") is True:
                        models = provider.get("models", {})
                        if not models:
                            raise ValueError("请先为该供应商添加模型")
                        current_model = config.get("default", {}).get("model")
                        model = current_model if current_model in models else next(iter(models))
                        config["default"] = {"provider": identity, "model": model}

                else:
                    raise ValueError("不支持的操作")
            elif kind == "models":
                provider = data.get("provider")
                if provider not in config.get("providers", {}):
                    raise ValueError("供应商不存在")
                models = config["providers"][provider].setdefault("models", {})
                if action == "default":
                    if identity not in models:
                        raise ValueError("模型不存在")
                    config["default"] = {"provider": provider, "model": identity}
                elif action == "delete":
                    if config.get("default") == {"provider": provider, "model": identity}:
                        raise ValueError("不能删除当前默认模型")
                    models.pop(identity, None)
                elif action == "save":
                    self.identifier(identity)
                    effort = values.get("reasoning_effort", "")
                    if effort not in (
                        "",
                        "none",
                        "minimal",
                        "low",
                        "medium",
                        "high",
                        "xhigh",
                        "max",
                        "ultra",
                    ):
                        raise ValueError("无效的推理等级")
                    window = int(values.get("context_window", 0))
                    if not 1024 <= window <= 10_000_000:
                        raise ValueError("上下文窗口需在 1024–10000000 之间")
                    model = models.setdefault(identity, {})
                    if "supported_reasoning_levels" in model and effort and effort not in {
                        entry["effort"] for entry in model["supported_reasoning_levels"]
                    }:
                        raise ValueError("此模型不支持所选推理等级")
                    if model.get("max_context_window") and window > model["max_context_window"]:
                        raise ValueError("超过此模型配置的最大上下文窗口")
                    if data.get("replace"):
                        model.clear()
                        model.update(values)
                    model.update(context_window=window, reasoning_effort=effort or None)
                    if "description" in values:
                        description = values["description"]
                        if not isinstance(description, str) or len(description) > 500:
                            raise ValueError("模型描述需为不超过 500 个字符的文本")
                        model["description"] = description.strip()

                else:
                    raise ValueError("不支持的操作")
            elif kind == "skills" and action == "toggle":
                item = next(
                    (f for f in self.files() if f["id"] == identity and f["kind"] == "skills"), None
                )
                if not item:
                    raise ValueError("技能不存在")
                enabled = values.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("技能开关必须是布尔值")
                skills = config.setdefault("skills", {})
                rules = skills.get("config", [])
                if not isinstance(rules, list) or any(not isinstance(r, dict) for r in rules):
                    raise ValueError("技能配置格式无效，请先修正配置文件")
                target = Path(item["path"]).resolve()
                remaining = []
                metadata = {}
                for rule in rules:
                    path = rule.get("path")
                    matches = (
                        isinstance(path, str)
                        and "name" not in rule
                        and (self.home / Path(path).expanduser()).resolve() == target
                    )
                    if matches:
                        metadata.update(
                            {k: v for k, v in rule.items() if k not in ("path", "enabled")}
                        )
                    else:
                        remaining.append(rule)
                if not enabled or metadata:
                    entry = {"path": str(target), **metadata}
                    if not enabled:
                        entry["enabled"] = False
                    remaining.append(entry)
                if enabled and target in disabled_skill_paths(
                    parse_skill_rules(remaining, self.home), ((item["name"], target),)
                ):
                    raise ValueError(
                        "该技能仍被名称规则禁用，请先在配置文件中移除对应的名称禁用规则"
                    )
                if remaining:
                    skills["config"] = remaining
                else:
                    skills.pop("config", None)
            elif kind == "tools":
                tools = config.setdefault("mcp", {}).setdefault("servers", {})
                if action == "delete":
                    tools.pop(identity, None)
                elif action == "toggle":
                    if identity not in tools:
                        raise ValueError("工具不存在")
                    if not isinstance(values.get("enabled"), bool):
                        raise ValueError("启用状态无效")
                    tools[identity]["enabled"] = values["enabled"]
                elif action == "save":
                    tool = self.mcp_form_config(identity, values, tools, data.get("is_new"))
                    tools[identity] = tool
                else:
                    raise ValueError("不支持的操作")
            else:
                raise ValueError("不支持的操作")
            # Preserve untouched sections and their comments; only replace edited sections.
            for key in config:
                if config[key] != before.get(key):
                    text = document.set_value(text, (key,), config[key])
            if data["revision"] != revision(self.config_text()):
                raise ValueError("配置写入冲突，请刷新后重试")
            if data.get("replace"):
                self.validate_config(text)
            self.write_config(text, data["revision"], f"{kind}: {action}")
            return {"message": "已保存；配置会在新的 Corki 会话中生效"}

    def mcp_form_config(self, identity, values, tools, is_new=False):
        if not isinstance(values, dict):
            raise ValueError("MCP 配置格式无效")
        self.identifier(identity)
        if is_new and identity in tools:
            raise ValueError("同名 MCP 服务已存在")
        tool = copy.deepcopy(tools.get(identity, {}))
        transport = values.get("transport")
        # Preserve auth/advanced settings; avoid implicit protocol migration.
        if (
            identity in tools
            and tool.get("transport", "http" if tool.get("url") else "stdio") != transport
        ):
            raise ValueError("现有连接不能直接更改传输方式，请新建连接")
        tool["transport"] = transport
        field = "url" if transport == "http" else "command"
        tool[field] = values.get(field, "").strip()
        if transport not in ("http", "stdio") or not tool[field]:
            raise ValueError("请填写连接地址或命令")
        for key in (
            ("args", "env", "cwd")
            if transport == "stdio"
            else ("headers", "http_headers", "bearer_token_env_var")
        ):
            if key in values:
                value = values[key]
                if value is None:
                    tool.pop(key, None)
                else:
                    tool[key] = value
        if "timeout_seconds" in values:
            tool["timeout_seconds"] = values["timeout_seconds"]
        MCPServerSettings.from_mapping(identity, tool)
        return tool

    @staticmethod
    def identifier(value):
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[\w][\w./:@+ -]{0,119}", value)
            or ".." in value
        ):
            raise ValueError("名称需为 1–120 个字符，不能包含路径跳转")

    def import_skill(self, data, *, preview=False, private=False):
        """Validate a whole package before publishing it with an atomic directory rename."""
        entries = []
        total = 0

        def add(name, content, executable=False):
            nonlocal total
            if not isinstance(name, str) or "\\" in name or "\x00" in name:
                raise ValueError("技能包含无效路径")
            parts = name.split("/")
            if PurePosixPath(name).is_absolute() or any(p in ("", ".", "..") for p in parts):
                raise ValueError("技能包含不安全路径")
            # A dot prefix alone does not make a file unsafe: packages can
            # require hidden configuration/version files. Skip only metadata.
            if any(p in {".DS_Store", "__MACOSX", ".git", ".manager-backups", "__pycache__"}
                   or p.startswith("._") for p in parts):
                return
            total += len(content)
            entries.append((name, content, executable))

        def decode(value):
            try:
                return base64.b64decode(value, validate=True)
            except (ValueError, TypeError, binascii.Error) as exc:
                raise ValueError("无效的文件编码") from exc

        if "archive" in data:
            try:
                with zipfile.ZipFile(io.BytesIO(decode(data["archive"]))) as archive:
                    infos = archive.infolist()
                    for info in infos:
                        mode = info.external_attr >> 16
                        if stat.S_ISLNK(mode) or (
                            stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                        ):
                            raise ValueError("ZIP 不支持符号链接或特殊文件")
                        if info.is_dir():
                            continue
                        if any(
                            p == "__MACOSX" or p == ".DS_Store" for p in info.filename.split("/")
                        ):
                            continue
                        add(info.filename, archive.read(info), bool(mode & 0o111))
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise ValueError("无法读取 ZIP，请使用未加密的标准 ZIP 文件") from exc
        else:
            files = data.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError("请选择包含 SKILL.md 的技能文件夹、ZIP 或单个 SKILL.md")
            for entry in files:
                if not isinstance(entry, dict):
                    raise ValueError("无效的文件列表")
                add(entry.get("path"), decode(entry.get("data")))
        mains = [name for name, _, _ in entries if PurePosixPath(name).name == "SKILL.md"]
        if len(mains) != 1:
            raise ValueError("一次导入一个技能，且必须包含唯一的 SKILL.md")
        prefix = mains[0][: -len("SKILL.md")]
        if any(not name.startswith(prefix) for name, _, _ in entries):
            raise ValueError("所有文件必须位于 SKILL.md 所在目录内")
        normalized = [
            (name[len(prefix) :], content, executable) for name, content, executable in entries
        ]
        names = [name.casefold() for name, _, _ in normalized]
        if len(set(names)) != len(names):
            raise ValueError("技能包含重名文件")
        if any(any(other.startswith(name + "/") for other in names) for name in names):
            raise ValueError("文件和目录名称冲突")
        main = next(content for name, content, _ in normalized if name == "SKILL.md")
        if len(main) > 128 * 1024:
            raise ValueError("SKILL.md 不能超过运行时读取上限 128 KB")
        try:
            text = main.decode("utf-8")
            name, description, _ = parse_skill(text, "")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"SKILL.md 格式无效：{exc}") from exc
        if not private and "name" in data:
            raise ValueError("不支持自定义技能目录名，导入时使用 SKILL.md 的 name")
        if not private and prefix and PurePosixPath(prefix.rstrip("/")).name != name:
            raise ValueError(f"技能目录名「{PurePosixPath(prefix.rstrip('/')).name}」必须与 SKILL.md 的 name「{name}」一致")
        folder = (data.get("name") or name) if private else name
        if (not isinstance(folder, str)
                or not re.fullmatch(r"[\w-][\w.\-]{0,79}", folder)
                or folder.endswith(".")):
            raise ValueError("技能目录名需为 1–80 个文字、数字、横线、下划线或点号，不能以点号开头或结尾")
        if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", folder, re.I):
            raise ValueError("技能目录名不能使用 Windows 系统保留名称")
        group = data.get("group", "public")
        if group not in ("corki", "public"):
            raise ValueError("请选择扩展 Skills 或公共 Skills")
        root = self.home / "skills" if group == "corki" else self.compatibility_home / ".agents/skills"
        if private:
            root = self.home / "hooks" / "skills"
        target = root / folder
        if target.exists() or target.is_symlink():
            raise ValueError("所选位置已存在同名技能，不会覆盖已有文件")
        if not private and any(f["kind"] == "skills" and f["name"].casefold() == name.casefold()
                               for f in self.files()):
            raise ValueError(f"已存在同名 Skill「{name}」，不会覆盖已有技能")
        summary = dict(
            name=name,
            description=description,
            folder=folder,
            group=group,
            path=str(target),
            files=[dict(path=n, bytes=len(c)) for n, c, _ in normalized],
            bytes=total,
        )
        if preview:
            return summary
        root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".import-", dir=root))
        try:
            for filename, content, executable in normalized:
                path = stage / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(0o700 if executable else 0o600)
            if target.exists() or target.is_symlink():
                raise ValueError("同名技能已存在")
            os.rename(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        label = "扩展 Skills" if group == "corki" else "公共 Skills"
        return {**summary, "message": f"已导入{label}；Corki 下次发现技能时可读取"}

    def delete_skill(self, data):
        item = next((f for f in self.files()
                     if f["kind"] == "skills" and f["id"] == data.get("id")), None)
        if not item:
            raise ValueError("技能不存在，请刷新列表")
        if item["group"] == "builtin":
            raise ValueError("内置 Skills 不可删除")
        if data.get("revision") != item["revision"]:
            raise ValueError("技能已更新，请刷新后重试")
        package = Path(item["path"]).parent
        roots = [self.home / "skills", self.compatibility_home / ".agents/skills",
                 self.workspace / ".corki/skills", self.workspace / ".agents/skills"]
        root = next((r for r in roots if package != r and package.is_relative_to(r)), None)
        if root is None or not package.resolve().is_relative_to(root.resolve()):
            raise ValueError("技能目录超出可管理范围")
        if any(p.is_symlink() for p in [package, *package.parents] if p != root and p.is_relative_to(root)):
            raise ValueError("不支持删除链接技能目录")
        # Remove the entire package, including scripts/resources; rmtree does not
        # follow symlinks inside it. Built-in packages and collection roots stay protected.
        builtin = (self.home / "skills/.system").resolve()
        if package.resolve() == root.resolve() or package.resolve().is_relative_to(builtin) or builtin.is_relative_to(package.resolve()):
            raise ValueError("内置 Skills 不可删除")
        shutil.rmtree(package)
        return {"message": "Skill 已永久删除"}

    def mutate_file(self, data):
        kind, action = data["kind"], data["action"]
        if action == "create":
            if kind == "skills":
                raise ValueError("请使用导入 Skills")
            if kind == "memories":
                raise ValueError("记忆由 Corki 生成，请编辑已有记忆")
            name = data.get("name", "").strip()
            if not re.fullmatch(r"[\w-]{1,80}", name):
                raise ValueError("文件名只允许文字、数字、横线和下划线")
            path = (
                self.home
                / ("skills" if kind == "skills" else "manager/knowledge")
                / (name + ("/SKILL.md" if kind == "skills" else ".md"))
            )
            if path.exists() or path.is_symlink():
                raise ValueError("同名文件已存在")
            if not path.resolve().is_relative_to(self.home):
                raise ValueError("文件路径超出管理目录")
        else:
            item = self.read_file(data.get("id"))
            if item["kind"] != kind or not item["writable"]:
                raise ValueError("此文件只读")
            path = Path(item["path"])
            if data.get("revision") != item["revision"]:
                raise ValueError("文件已更新，请刷新后重试")
        if action == "delete":
            if kind != "knowledge":
                raise ValueError("仅知识库文档支持删除，技能可停用")
            path.unlink()
            _, disabled = self.knowledge_state()
            disabled.discard(self.knowledge_key(path))
            self.write_knowledge_state(disabled)
            self.knowledge.delete(item["document_id"])
            shutil.rmtree(self.home / "manager/knowledge-versions" / item["document_id"],
                          ignore_errors=True)
        elif action in ("create", "save"):
            content = data.get("content", "")
            if not isinstance(content, str) or len(content.encode()) > LIMIT:
                raise ValueError("文档不能超过 256 KB")
            if kind == "skills":
                self.validate_skill_name_unchanged(content, item["content"], path.parent.name)
            if kind == "knowledge" and action == "save":
                self.archive_knowledge(item, Path(path).read_text())
            atomic(path, content, backup=kind != "knowledge")
            if kind == "knowledge":
                title = next((line.lstrip("# ").strip() for line in content.splitlines()
                              if line.startswith("# ")), path.stem)
                doc = self.knowledge.sync(path, title, content)
                self.knowledge.set_enabled(doc["id"], False)
                _, disabled = self.knowledge_state()
                disabled.add(self.knowledge_key(path))
                self.write_knowledge_state(disabled)
                self.archive_knowledge({"document_id": doc["id"]}, content)
        else:
            raise ValueError("不支持的操作")
        return {"message": "文档已保存" if action != "delete" else "文档已移到知识库回收目录"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        if isinstance(body, str):
            body = body.encode()
        streaming = hasattr(body, "read")
        if streaming:
            length = body.seek(0, os.SEEK_END)
            body.seek(0)
        else:
            length = len(body)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        if streaming:
            shutil.copyfileobj(body, self.wfile, length=256 * 1024)
        else:
            self.wfile.write(body)

    def trusted(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        return self.headers.get("Host") in hosts and self.headers.get("Origin") in (
            None,
            *(f"http://{h}" for h in hosts),
        )

    def do_GET(self):
        if not self.trusted():
            return self.respond(403, {"error": "仅允许本机同源访问"})
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/hooks":
                return self.respond(200, self.server.store.hooks_document())
            if parsed.path == "/api/sql-workspace":
                params = parse_qs(parsed.query)
                workspace = QueryWorkspace(self.server.store.home)
                if "result" in params:
                    return self.respond(200, workspace.result(params.get("query", [""])[0], params["result"][0]))
                return self.respond(200, workspace.listing())
            if parsed.path == "/api/databases":
                query = parse_qs(parsed.query)
                admin = DatabaseAdmin(self.server.store.home)
                if "table" in query:
                    return self.respond(200, admin.read(query.get("database", [""])[0], query["table"][0], query.get("offset", [0])[0], query.get("search", [""])[0]))
                return self.respond(200, admin.catalog())
            if parsed.path == "/api/cron":
                query = parse_qs(parsed.query)
                if "records" in query:
                    return self.respond(200, self.server.store.cron.records(
                        offset=int(query.get("offset", ["0"])[0]),
                        query=query.get("query", [""])[0],
                        names=query.get("name", []),
                    ))
                if "run" in query:
                    return self.respond(200, self.server.store.cron_run(query["run"][0]))
                if "id" in query:
                    return self.respond(200, self.server.store.cron.history(query["id"][0]))
                return self.respond(200, self.server.store.cron.list())
            if parsed.path == "/api/quick-templates":
                return self.respond(200, self.server.store.quick_catalog())
            if parsed.path in ("/api/skill-package", "/api/skill-resource", "/api/skill-export"):
                query = parse_qs(parsed.query)
                identity = query.get("id", [""])[0]
                if parsed.path == "/api/skill-package":
                    return self.respond(200, self.server.store.skill_package(identity))
                if parsed.path == "/api/skill-resource":
                    return self.respond(
                        200,
                        self.server.store.read_skill_resource(identity, query.get("path", [""])[0]),
                    )
                body, filename = self.server.store.export_skill(identity, stream=True)
                with body:
                    return self.respond(
                        200, body, "application/zip",
                        {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe="")},
                    )
            if parsed.path == "/api/custom-tool":
                identity = parse_qs(parsed.query).get("id", [""])[0]
                return self.respond(200, self.server.store.custom_tool_document(identity))
            if parsed.path == "/api/mcp-config":
                identity = parse_qs(parsed.query).get("id", [""])[0]
                text = self.server.store.config_text()
                servers = document.loads(text).get("mcp", {}).get("servers", {})
                if identity not in servers:
                    raise ValueError("MCP 服务不存在")
                return self.respond(200, {"config": servers[identity], "revision": revision(text)})
            if parsed.path == "/api/provider-key":
                identity = parse_qs(parsed.query).get("id", [""])[0]
                providers = document.loads(self.server.store.config_text()).get("providers", {})
                if identity not in providers:
                    raise ValueError("供应商不存在")
                return self.respond(200, {"api_key": providers[identity].get("api_key", "")})
            if parsed.path == "/api/config":
                identity = parse_qs(parsed.query).get("id", [None])[0]
                return self.respond(200, self.server.store.config_document(identity))
            if parsed.path == "/api/knowledge-status":
                identity = parse_qs(parsed.query).get("id", [""])[0]
                with LOCK:
                    item = next((f for f in self.server.store.files()
                                 if f["id"] == identity and f["kind"] == "knowledge"), None)
                    if item is None:
                        raise ValueError("知识文档不存在")
                    return self.respond(200, {key: item[key] for key in
                                             ("enabled", "index_status", "index_error")})
            if parsed.path == "/api/knowledge-document":
                identity = parse_qs(parsed.query).get("id", [""])[0]
                return self.respond(200, self.server.store.knowledge_document(identity))
            if parsed.path == "/api/snapshot":
                return self.respond(200, self.server.store.snapshot())
            if parsed.path in ("/api/file", "/api/execution"):
                identity = parse_qs(parsed.query).get("id", [""])[0]
                value = (
                    self.server.store.read_file(identity)
                    if parsed.path.endswith("file")
                    else self.server.store.execution(identity)
                )
                return self.respond(200, value)
            file = STATIC / ("index.html" if parsed.path == "/" else parsed.path.lstrip("/"))
            if not file.resolve().is_relative_to(STATIC.resolve()) or not file.is_file():
                return self.respond(404, {"error": "页面不存在"})
            self.respond(
                200, file.read_bytes(), mimetypes.guess_type(file)[0] or "application/octet-stream"
            )
        except (ValueError, OSError, sqlite3.Error):
            self.respond(400, {"error": "读取失败，请刷新后重试"})

    def do_POST(self):
        if not self.trusted() or self.headers.get("X-Corki-Manager") != "1":
            return self.respond(403, {"error": "仅允许本机管理页面修改配置"})
        if self.path == "/api/model-test":
            return self.do_model_test()
        if self.path != "/api/mutate":
            return self.respond(404, {"error": "接口不存在"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            skill_import = self.headers.get("X-Corki-Skill-Import") == "1"
            if length <= 0 or (not skill_import and length > 12 * 1024 * 1024):
                raise ValueError("请求体过大或为空")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("无效请求")
            if skill_import and not (
                (data.get("kind") == "skills" and data.get("action") in ("import", "validate-import"))
                or (data.get("kind") == "hooks" and data.get("action") == "upload-skill")
            ):
                raise ValueError("无效的技能导入请求")
            if (
                data.get("action") not in ("import", "validate-import", "parse-document", "upload-skill")
                and length > 2 * LIMIT
            ):
                raise ValueError("请求体过大")
            self.respond(200, self.server.store.mutate(data))
        except (ValueError, TypeError, KeyError) as error:
            self.respond(400, {"error": self.server.store.redact(str(error))[:300]})
        except OSError:
            self.respond(500, {"error": "写入失败，请检查文件权限"})

    def do_model_test(self):
        model = None
        acquired = False
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 128 * 1024:
                raise ValueError("测试请求为空或超过 128 KB")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("无效的测试请求")
            model, request = self.server.store.prepare_model_test(data)
        except (ValueError, TypeError, KeyError, OSError) as error:
            return self.respond(400, {"error": self.server.store.redact(str(error))[:300]})
        if not MODEL_TEST_SLOTS.acquire(blocking=False):
            return self.respond(429, {"error": "模型测试请求较多，请稍后重试"})
        acquired = True
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

        def send_event(name, value):
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode())
            self.wfile.flush()

        try:
            asyncio.run(self.server.store.stream_model_test(model, request, send_event))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            if acquired:
                MODEL_TEST_SLOTS.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--home", type=Path, default=Path(os.environ.get("CORKI_HOME", str(Path.home() / ".corki")))
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int, default=17002)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.store = Store(args.home, args.workspace)
    server.store.cron_service.start()
    print(f"Corki Manager: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.store.cron_service.close()
        server.server_close()


if __name__ == "__main__":
    main()
