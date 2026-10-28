"""Local, explicitly installed connectors. No arbitrary package registry execution."""

import base64
import io
import json
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from corki.cron.locking import FileLock

from .programs import environment, find
from .store import BUILTINS, ID


def assets():
    local = Path(__file__).resolve().parents[2] / "connectors"
    return local if local.is_dir() else Path(__file__).with_name("connectors")


def validate_manifest(value):
    if not isinstance(value, dict) or value.get("protocol") != 1:
        raise ValueError("连接器需要声明 protocol: 1")
    identity = value.get("id", "")
    if not isinstance(identity, str) or not ID.fullmatch(identity) or identity in BUILTINS:
        raise ValueError("连接器 ID 无效或为保留名称")
    if not isinstance(value.get("name"), str) or not 1 <= len(value["name"]) <= 60:
        raise ValueError("请填写连接器名称")
    if value.get("runtime") not in ("node", "python"):
        raise ValueError("runtime 只能是 node 或 python")
    entry = value.get("entry", "")
    if not isinstance(entry, str) or not entry or "\\" in entry or ":" in entry:
        raise ValueError("entry 应为包内相对路径")
    parts = PurePosixPath(entry).parts
    if PurePosixPath(entry).is_absolute() or ".." in parts:
        raise ValueError("entry 不能超出连接器目录")
    fields = value.get("fields", [])
    if not isinstance(fields, list) or len(fields) > 24:
        raise ValueError("fields 无效")
    seen = set()
    for field in fields:
        if (
            not isinstance(field, dict)
            or not ID.fullmatch(str(field.get("key", "")))
            or field["key"] in seen | {"workdir"}
        ):
            raise ValueError("配置字段名称无效或重复")
        if not isinstance(field.get("label"), str) or not 1 <= len(field["label"]) <= 60:
            raise ValueError("配置字段缺少 label")
        if any(key in field and type(field[key]) is not bool for key in ("secret", "required")):
            raise ValueError("secret/required 应为布尔值")
        options = field.get("options")
        if options is not None and (
            not isinstance(options, list)
            or not options
            or len(options) > 30
            or any(not isinstance(v, str) or len(v) > 100 for v in options)
        ):
            raise ValueError("options 应为文本数组")
        seen.add(field["key"])
    return {
        k: value[k]
        for k in ("id", "name", "protocol", "runtime", "entry", "fields", "description")
        if k in value
    }


def install(store, encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("连接器 ZIP 不能超过 16 MB")
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (ValueError, zipfile.BadZipFile) as error:
        raise ValueError("请上传有效的连接器 ZIP") from error
    with archive, tempfile.TemporaryDirectory(dir=store.root) as temporary:
        stage = Path(temporary)
        members = archive.infolist()
        if len(members) > 2000 or sum(i.file_size for i in members) > 64 * 1024 * 1024:
            raise ValueError("解压后的连接器过大")
        names = set()
        for item in members:
            name = item.filename
            parts = PurePosixPath(name).parts
            if (
                not parts
                or name.startswith("/")
                or "\\" in name
                or ":" in name
                or ".." in parts
                or any(
                    p.endswith((".", " "))
                    or p.split(".")[0].upper()
                    in {
                        "CON",
                        "PRN",
                        "AUX",
                        "NUL",
                        *(f"COM{i}" for i in range(1, 10)),
                        *(f"LPT{i}" for i in range(1, 10)),
                    }
                    for p in parts
                )
                or stat.S_ISLNK(item.external_attr >> 16)
                or name.casefold() in names
            ):
                raise ValueError("ZIP 含不安全或重复路径")
            names.add(name.casefold())
            target = stage.joinpath(*parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        manifests = list(stage.glob("connector.json")) or list(stage.glob("*/connector.json"))
        if len(manifests) != 1:
            raise ValueError("ZIP 根目录需要唯一的 connector.json")
        manifest = validate_manifest(json.loads(manifests[0].read_text(encoding="utf-8-sig")))
        if store.channel(manifest["id"]):
            raise ValueError("同名连接器已存在")
        source = manifests[0].parent
        if not (source / manifest["entry"]).is_file():
            raise ValueError("连接器入口文件不存在")
        destination = store.root / "plugins" / manifest["id"]
        if destination.exists():
            raise ValueError("连接器目录已存在，请先移除旧连接器")
        destination.parent.mkdir(exist_ok=True)
        shutil.copytree(source, destination)
        try:
            with store.db() as db:
                db.execute(
                    "INSERT INTO channels(id,manifest) VALUES(?,?)",
                    (manifest["id"], json.dumps(manifest)),
                )
        except BaseException:
            shutil.rmtree(destination)
            raise
    return manifest["id"]


def package_assets(identity):
    if identity not in BUILTINS:
        raise ValueError("请选择内置渠道")
    return assets() / "packages" / identity


def dependency_directory(store, identity):
    package_assets(identity)
    folder = store.root / "node" / identity
    legacy = store.root / "node"
    if not (legacy / ".split").exists() and (legacy / ".installed").is_file():
        return legacy
    return folder


def dependencies_ready(store, identity):
    folder = dependency_directory(store, identity)
    return (folder / ".installed").is_file() and find("node") is not None


def installation_status(store):
    return {identity: dependencies_ready(store, identity) for identity in BUILTINS}


@contextmanager
def migration_lock(store):
    lock = FileLock(store.root / "package-migration.lock")
    deadline = time.monotonic() + 60
    while not lock.acquire():
        if time.monotonic() > deadline:
            raise ValueError("旧版渠道组件仍在整理，请稍后重试")
        time.sleep(0.1)
    try:
        yield
    finally:
        lock.close()


def migrate_dependencies(store):
    with migration_lock(store):
        _migrate_dependencies(store)


def _migrate_dependencies(store):
    """Split the previous shared install, without downloading or touching credentials.

    Keep the old tree until existing connector processes have stopped: their lazy
    imports can still reference it. A completed split never falls back to it.
    """
    legacy = store.root / "node"
    if (legacy / ".split").exists() or not (legacy / ".installed").is_file():
        return
    for identity in BUILTINS:
        destination = legacy / identity
        if (destination / ".installed").is_file():
            continue
        with tempfile.TemporaryDirectory(dir=store.root) as temporary:
            stage = Path(temporary) / identity
            stage.mkdir()
            for name in ("package.json", "package-lock.json"):
                shutil.copy2(package_assets(identity) / name, stage / name)
            lock = json.loads((stage / "package-lock.json").read_text())
            missing = False
            for relative in sorted(lock["packages"], key=len):
                if not relative:
                    continue
                source, target = legacy / relative, stage / relative
                if not source.is_dir():
                    if lock["packages"][relative].get("optional"):
                        continue  # npm omits other platforms' optional native packages.
                    missing = True
                    break
                if not target.exists():
                    shutil.copytree(source, target, symlinks=True)
            if missing:
                # Do not claim a damaged or older shared install is usable.
                continue
            (stage / ".installed").write_text("1", encoding="utf-8")
            if destination.exists():
                shutil.rmtree(destination)
            stage.rename(destination)
    (legacy / ".split").write_text("1", encoding="utf-8")


def cleanup_legacy_dependencies(store):
    with migration_lock(store):
        _cleanup_legacy_dependencies(store)


def _cleanup_legacy_dependencies(store):
    legacy = store.root / "node"
    if not (legacy / ".split").exists():
        return
    shutil.rmtree(legacy / "node_modules", ignore_errors=True)
    for name in ("package.json", "package-lock.json", ".installed"):
        (legacy / name).unlink(missing_ok=True)


def uninstall_dependencies(store, identity):
    package_assets(identity)
    migrate_dependencies(store)
    folder = store.root / "node" / identity
    if folder.exists():
        shutil.rmtree(folder)


def install_dependencies(store, identity, progress=lambda stage: None):
    progress("正在检查运行环境…")
    manifest = package_assets(identity)
    node = find("node")
    if not node:
        raise ValueError("请先安装 Node.js 22 或更新版本")
    try:
        version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5)
        if version.returncode or int(version.stdout.strip().lstrip("v").split(".")[0]) < 22:
            raise ValueError("需要 Node.js 22 或更新版本")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("无法运行 Node.js，请检查安装") from error
    progress("正在准备渠道组件…")
    migrate_dependencies(store)
    destination = store.root / "node" / identity
    if dependencies_ready(store, identity):
        return
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=store.root) as temporary:
        folder = Path(temporary) / identity
        folder.mkdir()
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(manifest / name, folder / name)
        # Telegram uses Node's native fetch: no SDK or npm download required.
        if json.loads((folder / "package.json").read_text())["dependencies"]:
            npm = find("npm")
            if not npm:
                raise ValueError("请先安装 npm，再安装此渠道")
            command = [
                npm,
                "ci",
                "--omit=dev",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--registry=https://registry.npmjs.org",
            ]
            if __import__("os").name == "nt":
                from corki.platforms.windows_program import prepare_command

                command = prepare_command(command, env=environment(), cwd=folder)
            progress("正在下载并安装组件…")
            try:
                result = subprocess.run(
                    command, cwd=folder, capture_output=True, timeout=180, env=environment()
                )
            except subprocess.TimeoutExpired as error:
                raise ValueError("渠道安装超时，请检查网络后重试") from error
            if result.returncode:
                raise ValueError("渠道安装失败，请检查 npm 网络连接和 Node.js 版本")
        progress("正在完成安装…")
        (folder / ".installed").write_text("1", encoding="utf-8")
        if destination.exists():
            shutil.rmtree(destination)
        folder.rename(destination)
