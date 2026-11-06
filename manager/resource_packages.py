"""Optional resource-package workbench. Packages are backups, never runtime plugins."""

import base64
import copy
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from corki.config import document
from corki.config.features import MCPServerSettings
from corki.config.hooks import HOOK_EVENTS
from corki.core.stop_hooks import command_identity
from corki.platforms.paths import archive_parts, is_link_path
from corki.skills.frontmatter import parse as parse_skill

FORMAT = "corki-resource-package"
KINDS = ("skills", "mcp", "hooks")
TITLES = {"skills": "Skills", "mcp": "MCP", "hooks": "Hooks"}
IGNORED = {".DS_Store", "__MACOSX", ".git", "__pycache__", ".manager-backups", ".venv"}
TOKEN = "${PACKAGE_ROOT}/"


def hook_name(row):
    h = row["handler"]
    detail = (
        h.get("command")
        or h.get("prompt")
        or h.get("path")
        or h.get("tool")
        or h.get("type", "Hook")
    )
    return h.get("statusMessage") or f"{row['event']} · {str(detail)[:120]}"


def component_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 300 or "\x00" in value:
        raise ValueError("资源名称无效")
    return value.strip()


def clean_files(entries):
    result, folded = {}, set()
    for name, data, executable in entries:
        parts = archive_parts(name)
        if any(part in IGNORED or part.startswith("._") for part in parts):
            continue
        path = "/".join(parts)
        if path.casefold() in folded:
            raise ValueError("资源包包含重名文件")
        folded.add(path.casefold())
        result[path] = (data, executable)
    for name in folded:
        if any(
            parent in folded
            for parent in (str(p) for p in PurePosixPath(name).parents)
            if parent != "."
        ):
            raise ValueError("资源包文件和目录名称冲突")
    return result


def unzip(raw):
    try:
        entries = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for info in z.infolist():
                archive_parts(info.filename.rstrip("/"))
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFDIR, stat.S_IFREG):
                    raise ValueError("资源包不支持符号链接或特殊文件")
                if not info.is_dir():
                    entries.append((info.filename, z.read(info), bool(mode & 0o111)))
        return clean_files(entries)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
        raise ValueError("无法读取 ZIP，请选择未加密的标准 ZIP 文件") from e


def decode(value):
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as e:
        raise ValueError("上传文件编码无效") from e


def read_tree(root):
    entries = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in IGNORED]
        for name in [*dirs, *files]:
            if is_link_path(Path(parent) / name):
                relative = (Path(parent) / name).relative_to(root).as_posix()
                raise ValueError(f"资源目录包含符号链接：{relative}，请移除后重试")
        for name in files:
            path = Path(parent) / name
            if not path.is_file():
                raise ValueError("资源目录包含特殊文件")
            entries.append(
                (
                    path.relative_to(root).as_posix(),
                    path.read_bytes(),
                    bool(path.stat().st_mode & 0o111),
                )
            )
    return clean_files(entries)


def pack(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path, (data, executable) in sorted(files.items()):
            info = zipfile.ZipInfo(path)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | (0o700 if executable else 0o600)) << 16
            z.writestr(info, data)
    return out.getvalue()


class ResourcePackages:
    def __init__(self, store):
        self.store = store
        # Deliberately outside ~/.corki/plugins and every Skills discovery root.
        self.root = store.home / "manager/resource-packages"
        self.assets = store.home / "manager/imported-resources"

    def directory(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("插件不存在")
        path = self.root / identity
        if is_link_path(path) or not path.is_dir():
            raise ValueError("插件不存在")
        return path

    def listing(self):
        rows = []
        if self.root.exists():
            for path in self.root.iterdir():
                if (
                    not re.fullmatch(r"[0-9a-f]{32}", path.name)
                    or is_link_path(path)
                    or not path.is_dir()
                ):
                    continue
                try:
                    value = json.loads((path / "record.json").read_text(encoding="utf-8"))
                    rows.append({**value, "id": path.name})
                except (OSError, ValueError):
                    continue
        return {"packages": sorted(rows, key=lambda row: row["created"], reverse=True)}

    def inventory(self):
        config = document.loads(self.store.config_text())
        skills = [
            f for f in self.store.files() if f["kind"] == "skills" and f["group"] != "builtin"
        ]
        hooks = self.store.hooks_document()["entries"]
        return {
            "skills": [
                {
                    "id": f["id"],
                    "name": f["name"],
                    "description": f["description"],
                    "group": f["group"],
                    "valid": f.get("valid", True),
                }
                for f in skills
            ],
            "mcp": [
                {"id": name, "name": name} for name in config.get("mcp", {}).get("servers", {})
            ],
            "hooks": [
                {
                    "id": row["id"],
                    "name": hook_name(row),
                    "description": row.get("error") or row["event"],
                    "valid": not row.get("error"),
                }
                for row in hooks
            ],
        }

    def uploaded(self, data):
        if data.get("archive") is not None:
            return unzip(decode(data["archive"]))
        entries = data.get("files", [])
        if not isinstance(entries, list):
            raise ValueError("请选择文件或目录")
        return clean_files((entry["path"], decode(entry["data"]), False) for entry in entries)

    @staticmethod
    def add_tree(files, prefix, tree):
        for path, value in tree.items():
            target = prefix + "/" + path
            if target in files:
                raise ValueError("插件包含重名资源")
            files[target] = value

    def build(self, data):
        name = component_name(data.get("name"))
        description = data.get("description", "")
        if not isinstance(description, str) or len(description) > 1000:
            raise ValueError("插件描述最多 1000 个字符")
        manifest = {
            "format": FORMAT,
            "version": 1,
            "name": name,
            "description": description.strip(),
            **{k: [] for k in KINDS},
        }
        files = {}
        chosen = data.get("selection", {})
        if not isinstance(chosen, dict) or any(
            not isinstance(chosen.get(k, []), list) for k in KINDS
        ):
            raise ValueError("资源选择无效")
        all_skills = {f["id"]: f for f in self.store.files() if f["kind"] == "skills"}
        config = document.loads(self.store.config_text())
        servers = config.get("mcp", {}).get("servers", {})
        hooks = {row["id"]: row for row in self.store.hooks_document()["entries"]}
        for identity in chosen.get("skills", []):
            item = all_skills.get(identity)
            if not item:
                raise ValueError("所选 Skill 已更新或删除，请重新选择")
            if item["group"] == "builtin":
                raise ValueError("内置 Skills 不能添加到插件")
            prefix = "skills/" + item["name"]
            self.add_tree(files, prefix, read_tree(Path(item["path"]).parent))
            manifest["skills"].append({"name": item["name"], "path": prefix})
        for identity in chosen.get("mcp", []):
            if identity not in servers:
                raise ValueError("所选 MCP 已删除")
            settings = copy.deepcopy(servers[identity])
            settings.pop("enabled", None)
            manifest["mcp"].append({"name": identity, "config": settings})
        for identity in chosen.get("hooks", []):
            row = hooks.get(identity)
            if not row or row.get("error"):
                raise ValueError("所选 Hook 已更新或不可用")
            row = copy.deepcopy(row)
            display_name = hook_name(row)
            handler = row["handler"]
            if handler.get("type") == "skill":
                main = Path(handler["path"]).expanduser()
                if not main.is_file() or is_link_path(main):
                    raise ValueError("Hook 引用的技能不存在")
                prefix = "resources/hook-skills/" + uuid.uuid4().hex
                self.add_tree(files, prefix, read_tree(main.parent))
                handler["path"] = TOKEN + prefix + "/" + main.name
            manifest["hooks"].append(
                {
                    "name": display_name,
                    "event": row["event"],
                    "matcher": row["matcher"],
                    "handler": handler,
                }
            )
        uploaded = self.uploaded(data)
        # A local ZIP can contain a skill folder or resources, just like folder selection.
        expanded = {}
        for path, (content, executable) in uploaded.items():
            if path.lower().endswith(".zip"):
                self.add_tree(expanded, PurePosixPath(path).stem, unzip(content))
            else:
                expanded[path] = (content, executable)
        if any(PurePosixPath(path).name == "plugin.json" for path in expanded):
            imported, imported_files = self.validate(expanded)
            for kind in KINDS:
                manifest[kind].extend(imported[kind])
            for path, value in imported_files.items():
                if path == "plugin.json":
                    continue
                if path in files:
                    raise ValueError("插件包含重名资源：" + path)
                files[path] = value
            expanded = {}
        covered = set()
        for path, (content, _) in expanded.items():
            if PurePosixPath(path).name == "SKILL.md":
                skill_name, _, _ = parse_skill(content.decode("utf-8"), "")
                prefix = path[: -len("SKILL.md")]
                tree = {
                    p[len(prefix) :]: value for p, value in expanded.items() if p.startswith(prefix)
                }
                self.add_tree(files, "skills/" + skill_name, tree)
                covered.update(p for p in expanded if p.startswith(prefix))
                manifest["skills"].append({"name": skill_name, "path": "skills/" + skill_name})
        for path, (content, executable) in expanded.items():
            if path in covered:
                continue
            base = PurePosixPath(path).name
            if base in ("mcp.json", ".mcp.json"):
                value = json.loads(content.decode("utf-8-sig"))
                if not isinstance(value, dict):
                    raise ValueError("本地 MCP 配置必须为 JSON 对象")
                mapping = value.get("mcpServers", value.get("servers", value))
                if not isinstance(mapping, dict):
                    raise ValueError("本地 MCP 配置无效")
                for key, settings in mapping.items():
                    settings = copy.deepcopy(settings)
                    if not isinstance(settings, dict):
                        raise ValueError("本地 MCP 配置无效")
                    settings.pop("enabled", None)
                    manifest["mcp"].append({"name": key, "config": settings})
            elif base == "hooks.json":
                value = json.loads(content.decode("utf-8-sig"))
                if not isinstance(value, dict) or not isinstance(value.get("hooks", value), dict):
                    raise ValueError("本地 Hooks 配置必须为 JSON 对象")
                for event, groups in value.get("hooks", value).items():
                    if event in ("state", "enabled", "disabled"):
                        continue
                    if not isinstance(groups, list):
                        raise ValueError("Hook 事件必须为数组")
                    for group in groups:
                        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                            raise ValueError("Hook 配置组无效")
                        for handler in group["hooks"]:
                            if not isinstance(handler, dict):
                                raise ValueError("Hook 处理器无效")
                            row = {
                                "event": event,
                                "matcher": group.get("matcher", "*"),
                                "handler": handler,
                            }
                            manifest["hooks"].append({"name": hook_name(row), **row})
            else:
                files["resources/local/" + path] = (content, executable)
        files["plugin.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2).encode(), False)
        manifest, files = self.validate(files)
        if not any(manifest[k] for k in KINDS) and len(files) == 1:
            raise ValueError("请至少选择一项资源或添加本地文件")
        return manifest, files

    def create(self, data):
        name = component_name(data.get("name"))
        if any(row["name"].casefold() == name.casefold() for row in self.listing()["packages"]):
            raise ValueError("已存在同名插件")
        manifest, files = self.build(data)
        identity = self.save(manifest, files)
        return {"id": identity, "message": "插件已创建", **self.listing()}

    def detail(self, identity):
        manifest, files = self.source({"id": identity})
        record = next(row for row in self.listing()["packages"] if row["id"] == identity)
        prefixes = [row["path"] + "/" for row in manifest["skills"]]
        return {
            **record,
            "files": [
                {"name": path, "bytes": len(value[0])}
                for path, value in sorted(files.items())
                if path != "plugin.json" and not any(path.startswith(p) for p in prefixes)
            ],
        }

    def update(self, data):
        identity = data.get("id")
        detail = self.detail(identity)
        if data.get("revision") != detail["digest"]:
            raise ValueError("插件已发生变化，请刷新后重试")
        manifest, files = self.source({"id": identity})
        removed = data.get("remove", {})
        if not isinstance(removed, dict) or any(
            key not in (*KINDS, "resources")
            or not isinstance(value, list)
            or any(not isinstance(name, str) for name in value)
            for key, value in removed.items()
        ):
            raise ValueError("待移除组件无效")
        for kind in KINDS:
            names = set(removed.get(kind, []))
            if names - {row["name"] for row in manifest[kind]}:
                raise ValueError("待移除组件不存在，请刷新后重试")
            if kind == "skills":
                prefixes = [row["path"] + "/" for row in manifest[kind] if row["name"] in names]
                files = {
                    p: value
                    for p, value in files.items()
                    if not any(p.startswith(prefix) for prefix in prefixes)
                }
            manifest[kind] = [row for row in manifest[kind] if row["name"] not in names]
        for path in removed.get("resources", []):
            if path not in {row["name"] for row in detail["files"]}:
                raise ValueError("待移除文件不存在")
            files.pop(path)
        if data.get("selection") or data.get("files") or data.get("archive"):
            additions, extra = self.build(
                {**data, "name": manifest["name"], "description": manifest.get("description", "")}
            )
            for kind in KINDS:
                manifest[kind].extend(additions[kind])
            for path, value in extra.items():
                if path == "plugin.json":
                    continue
                if path in files:
                    raise ValueError("插件包含重名资源：" + path)
                files[path] = value
        files["plugin.json"] = (json.dumps(manifest, ensure_ascii=False).encode(), False)
        manifest, files = self.validate(files)
        self.save(manifest, files, identity=identity)
        return {"message": "插件已保存", **self.detail(identity)}

    def validate(self, files):
        files = clean_files(
            (path, content, executable) for path, (content, executable) in files.items()
        )
        # Allow a single enclosing ZIP/folder without trusting its directory name.
        candidates = [p for p in files if PurePosixPath(p).name == "plugin.json"]
        roots = [
            p
            for p in candidates
            if not any(p.startswith(other[:-11]) and p != other for other in candidates)
        ]
        if "plugin.json" in files:
            root = ""
        elif len(roots) == 1:
            root = roots[0][: -len("plugin.json")]
        else:
            if files and all(path.lower().endswith(".zip") for path in files):
                raise ValueError("这是批量导出包，请解压后选择其中一个插件 ZIP 导入")
            raise ValueError("资源包需要唯一的 plugin.json 清单")
        if root:
            if any(not p.startswith(root) for p in files):
                raise ValueError("资源必须位于插件根目录中")
            files = {p[len(root) :]: value for p, value in files.items()}
        try:
            manifest = json.loads(files["plugin.json"][0].decode("utf-8-sig"))
        except (ValueError, KeyError) as e:
            raise ValueError("插件清单不是有效 JSON") from e
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != FORMAT
            or manifest.get("version") != 1
        ):
            raise ValueError(
                "请选择 Corki 资源插件包，清单需声明 corki-resource-package 格式及 version: 1"
            )
        manifest["name"] = component_name(manifest.get("name"))
        if not isinstance(manifest.get("description", ""), str):
            raise ValueError("插件描述无效")
        for kind in KINDS:
            rows = manifest.setdefault(kind, [])
            if not isinstance(rows, list):
                raise ValueError("插件资源清单必须为数组")
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("资源清单无效")
                row["name"] = component_name(row.get("name"))
                if row["name"].casefold() in seen:
                    raise ValueError(f"插件包含重名的 {TITLES[kind]}：{row['name']}")
                seen.add(row["name"].casefold())
                if kind == "skills":
                    prefix = "/".join(archive_parts(row.get("path")))
                    main = files.get(prefix + "/SKILL.md")
                    if not main:
                        raise ValueError("Skill 缺少 SKILL.md")
                    name, _, _ = parse_skill(main[0].decode("utf-8"), "")
                    if name != row["name"]:
                        raise ValueError("Skill 名称与清单不一致")
                    tree = {
                        p[len(prefix) + 1 :]: value
                        for p, value in files.items()
                        if p.startswith(prefix + "/")
                    }
                    upload = {
                        "group": "corki",
                        "archive": base64.b64encode(
                            pack({row["name"] + "/" + p: value for p, value in tree.items()})
                        ).decode(),
                    }
                    # Basic validation is shared with the existing Skill import code.
                    # Duplicate installed names are handled by plan(), not this syntax check.
                    row["_upload"] = upload
                elif kind == "mcp":
                    settings = row.get("config")
                    if not isinstance(settings, dict):
                        raise ValueError("MCP 配置无效")
                    settings.pop("enabled", None)
                    MCPServerSettings.from_mapping(
                        row["name"], self.resolve(settings, self.assets / "validation")
                    )
                else:
                    if (
                        row.get("event") not in HOOK_EVENTS
                        or not isinstance(row.get("matcher", "*"), str)
                        or not isinstance(row.get("handler"), dict)
                    ):
                        raise ValueError("Hook 配置无效")
                    if row["handler"].get("type") == "skill" and not row["handler"].get(
                        "path", ""
                    ).startswith(TOKEN):
                        raise ValueError(
                            "Hook 使用的 Skill 必须包含在插件资源中，"
                            "并使用 ${PACKAGE_ROOT}/ 相对路径"
                        )
                    command_identity(
                        self.resolve(row["handler"], self.assets / "validation"),
                        event_name=row["event"],
                        matcher=row.get("matcher", "*"),
                    )
        self.check_references(manifest, files)
        return manifest, files

    @staticmethod
    def check_references(value, files):
        if isinstance(value, dict):
            for child in value.values():
                ResourcePackages.check_references(child, files)
        elif isinstance(value, list):
            for child in value:
                ResourcePackages.check_references(child, files)
        elif isinstance(value, str) and TOKEN in value:
            direct = value.removeprefix(TOKEN)
            if value.startswith(TOKEN) and (
                direct in files or any(p.startswith(direct.rstrip("/") + "/") for p in files)
            ):
                references = [direct]
            else:
                try:
                    tokens = shlex.split(value)
                except ValueError as error:
                    raise ValueError("插件资源路径的引号未闭合") from error
                references = [token.partition(TOKEN)[2] for token in tokens if TOKEN in token]
            for reference in references:
                path = "/".join(archive_parts(reference))
                if path not in files and not any(
                    p.startswith(path.rstrip("/") + "/") for p in files
                ):
                    raise ValueError("插件引用的本地资源不存在")

    @staticmethod
    def resolve(value, root):
        if isinstance(value, dict):
            return {key: ResourcePackages.resolve(child, root) for key, child in value.items()}
        if isinstance(value, list):
            return [ResourcePackages.resolve(child, root) for child in value]
        if isinstance(value, str):
            return value.replace(TOKEN, str(root) + os.sep)
        return value

    @staticmethod
    def public_manifest(manifest):
        return {
            "name": manifest["name"],
            "description": manifest.get("description", ""),
            **{k: [{"name": row["name"]} for row in manifest[k]] for k in KINDS},
        }

    def save(self, manifest, files, *, identity=None):
        # Strip workbench-only validation material before generating the backup.
        exported = copy.deepcopy(manifest)
        for rows in (exported[k] for k in KINDS):
            for row in rows:
                row.pop("_upload", None)
        files = {
            **files,
            "plugin.json": (json.dumps(exported, ensure_ascii=False, indent=2).encode(), False),
        }
        raw = pack(files)
        digest = hashlib.sha256(raw).hexdigest()
        previous = None
        for row in self.listing()["packages"]:
            if row["id"] == identity:
                previous = row
                continue
            if row["digest"] == digest:
                return row["id"]
            if row["name"].casefold() == manifest["name"].casefold():
                raise ValueError("已存在同名插件，请更换插件名称")
        self.root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".create-", dir=self.root))
        identity = identity or uuid.uuid4().hex
        try:
            (stage / "package.zip").write_bytes(raw)
            (stage / "package.zip").chmod(0o600)
            record = {
                **self.public_manifest(manifest),
                "digest": digest,
                "bytes": len(raw),
                "created": previous["created"] if previous else datetime.now(UTC).isoformat(),
                "updated": datetime.now(UTC).isoformat(),
                "resources": sum(p.startswith("resources/") for p in files),
            }
            (stage / "record.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
            destination = self.root / identity
            if previous:
                backup = self.root / (".replace-" + uuid.uuid4().hex)
                os.rename(destination, backup)
                try:
                    os.rename(stage, destination)
                except BaseException:
                    os.rename(backup, destination)
                    raise
                shutil.rmtree(backup)
            else:
                os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return identity

    def source(self, data):
        if data.get("id"):
            files = unzip((self.directory(data["id"]) / "package.zip").read_bytes())
        else:
            files = self.uploaded(data)
        return self.validate(files)

    def plan(self, manifest, files):
        inventory = self.inventory()
        existing = {k: {row["name"].casefold() for row in inventory[k]} for k in KINDS}
        result = {
            "available": [],
            "skipped": [],
            "name": manifest["name"],
            "description": manifest.get("description", ""),
            "resources": sum(p.startswith("resources/") for p in files),
        }
        for kind in KINDS:
            for row in manifest[kind]:
                result[
                    "skipped" if row["name"].casefold() in existing[kind] else "available"
                ].append({"kind": kind, "name": row["name"]})
        if not any(manifest[kind] for kind in KINDS) and result["resources"]:
            digest = hashlib.sha256(
                pack(
                    {
                        **files,
                        "plugin.json": (
                            json.dumps(manifest, ensure_ascii=False, indent=2).encode(),
                            False,
                        ),
                    }
                )
            ).hexdigest()
            if not any(row["digest"] == digest for row in self.listing()["packages"]):
                result["available"].append({"kind": "resources", "name": manifest["name"]})
        result["can_import"] = bool(result["available"])
        return result

    def change(self, data):
        action = data.get("action")
        if action == "create":
            return self.create(data)
        if action == "detail":
            return self.detail(data.get("id"))
        if action == "update":
            return self.update(data)
        if action == "delete":
            path = self.directory(data.get("id"))
            shutil.rmtree(path)
            return {"message": "插件已永久删除", **self.listing()}
        if action not in ("preview-import", "import"):
            raise ValueError("不支持的插件操作")
        manifest, files = self.source(data)
        plan = self.plan(manifest, files)
        if action == "preview-import":
            return plan
        if not plan["can_import"]:
            raise ValueError("所有资源均已存在，无法重复导入插件")
        enabled = data.get("enable_after_import", False)
        if type(enabled) is not bool:
            raise ValueError("导入后开启选项无效")
        allowed = {(row["kind"], row["name"]) for row in plan["available"]}
        before = self.store.config_text()
        config = document.loads(before)
        resource_id = uuid.uuid4().hex
        asset_root = self.assets / resource_id
        created = []
        package_id = None
        was_saved = {row["id"] for row in self.listing()["packages"]}
        try:
            # Validate every Skill before any destination is published.
            for row in manifest["skills"]:
                if ("skills", row["name"]) in allowed:
                    self.store.import_skill(row["_upload"], preview=True)
            # Skills already receive their own installed copy. Avoid a third copy
            # unless a handler explicitly refers to the package's Skill tree.
            references = json.dumps(manifest, ensure_ascii=False)
            referenced_skills = [
                row["path"] + "/"
                for row in manifest["skills"]
                if TOKEN + row["path"] + "/" in references
            ]
            asset_files = {
                path: value
                for path, value in files.items()
                if (path != "plugin.json" and not path.startswith("skills/"))
                or any(path.startswith(prefix) for prefix in referenced_skills)
            }
            if asset_files:
                self.assets.mkdir(parents=True, exist_ok=True)
                asset_root.mkdir()
                created.append(asset_root)
                for path, (content, executable) in asset_files.items():
                    target = asset_root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    target.chmod(0o700 if executable else 0o600)
            for row in manifest["skills"]:
                if ("skills", row["name"]) not in allowed:
                    continue
                result = self.store.import_skill(row["_upload"])
                created.append(Path(result["path"]))
                if not enabled:
                    config.setdefault("skills", {}).setdefault("config", []).append(
                        {"path": str(Path(result["path"]) / "SKILL.md"), "enabled": False}
                    )
            for row in manifest["mcp"]:
                if ("mcp", row["name"]) in allowed:
                    settings = self.resolve(row["config"], asset_root)
                    settings["enabled"] = enabled
                    config.setdefault("mcp", {}).setdefault("servers", {})[row["name"]] = settings
            for row in manifest["hooks"]:
                if ("hooks", row["name"]) not in allowed:
                    continue
                handler = self.resolve(row["handler"], asset_root)
                handler.setdefault("statusMessage", row["name"])
                if handler.get("type") == "skill":
                    from corki.core.context_hooks import render_context

                    render_context(handler)
                if handler.get("type") == "mcp_tool" and handler.get("server") not in config.get(
                    "mcp", {}
                ).get("servers", {}):
                    raise ValueError("Hook 引用的 MCP 服务不存在，请一并导入对应服务")
                event, matcher = row["event"], row.get("matcher", "*")
                fingerprint, _ = command_identity(handler, event_name=event, matcher=matcher)
                hooks = config.setdefault("hooks", {})
                groups = hooks.setdefault(event, [])
                index = len(groups)
                groups.append({"matcher": matcher, "hooks": [handler]})
                event_key = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
                hooks.setdefault("state", {})[f"{self.store.config}:{event_key}:{index}:0"] = {
                    "enabled": enabled,
                    "trusted_hash": fingerprint,
                }
            updated = before
            original = document.loads(before)
            for key in ("skills", "mcp", "hooks"):
                if config.get(key) != original.get(key):
                    updated = document.set_value(updated, (key,), config[key])
            self.store.validate_config(updated)
            package_id = self.save(manifest, files)
            if updated != before:
                self.store.write_config(
                    updated, hashlib.sha256(before.encode()).hexdigest(), "导入资源插件"
                )
        except BaseException:
            if self.store.config_text() != before:
                raise ValueError("配置已发生变化，已保留导入资源，请刷新后检查导入结果") from None
            for path in reversed(created):
                if path.exists():
                    shutil.rmtree(path)
            if package_id and package_id not in was_saved:
                shutil.rmtree(self.directory(package_id))
            raise
        return {
            "message": "插件已导入"
            + (f"，已跳过 {len(plan['skipped'])} 项同名资源" if plan["skipped"] else ""),
            "id": package_id,
            **plan,
        }

    def export(self, identities):
        if (
            not isinstance(identities, list)
            or not identities
            or len(identities) != len(set(identities))
        ):
            raise ValueError("请选择需要导出的插件")
        rows = {row["id"]: row for row in self.listing()["packages"]}
        if len(identities) == 1:
            identity = identities[0]
            if identity not in rows:
                raise ValueError("插件不存在")
            name = re.sub(r"[^\w.\- ]", "-", rows[identity]["name"]).strip(". ") or "plugin"
            return (self.directory(identity) / "package.zip").read_bytes(), name + ".zip"
        files = {}
        for index, identity in enumerate(identities):
            if identity not in rows:
                raise ValueError("插件不存在")
            name = re.sub(r"[^\w.\- ]", "-", rows[identity]["name"]).strip(". ")[:100] or "plugin"
            files[f"{index + 1:02d}-{name}.zip"] = (
                (self.directory(identity) / "package.zip").read_bytes(),
                False,
            )
        return pack(files), "corki-plugins.zip"
