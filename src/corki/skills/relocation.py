"""Move skill packages without changing their public identity or overwriting files."""

import copy
import errno
import hashlib
import json
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from corki.config import document
from corki.config.skills import canonical_skill_rule_path
from corki.platforms.paths import is_link_path
from corki.storage.file_lock import lock, open_lock, unlock_and_close

_LOCK = threading.RLock()


def locations_path(store):
    return store.home / "manager/skill-locations.json"


def locations(store):
    path = locations_path(store)
    if not path.exists():
        return {}
    if is_link_path(path):
        raise ValueError("技能位置记录不能是符号链接")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                         for k, v in data.items()):
        raise ValueError("技能位置记录格式无效")
    return data


def roots(store):
    return {"corki": store.home / "skills", "work": store.home / "skills/work",
            "evolution": store.home / "skills/self-improving",
            "public": store.compatibility_home / ".agents/skills"}


def validate_package(store, package):
    collections = [*roots(store).values(), store.home / "self-improving",
                   store.workspace / ".corki/skills",
                   store.workspace / ".agents/skills"]
    root = next((r for r in sorted(collections, key=lambda p: len(p.parts), reverse=True)
                 if package != r and package.is_relative_to(r)), None)
    protected = [store.home / "skills/.system", *collections]
    if root is None or any(package == p or p.is_relative_to(package) for p in protected):
        raise ValueError("不能移动技能空间或内置 Skills 目录")
    if package.is_relative_to(store.home / "skills/.system"):
        raise ValueError("内置 Skills 不支持移动")
    for part in [package, *package.parents]:
        if is_link_path(part):
            raise ValueError("不支持移动链接技能目录")
        if part in (store.home, store.compatibility_home, store.workspace):
            break
    if not package.resolve().is_relative_to(root.resolve()):
        raise ValueError("技能目录超出可管理范围")


def _pending(store):
    return store.home / "manager/skill-move.pending.json"


def _recover(store, atomic):
    """A pending move rolls back, never replays a user action after restart."""
    pending = _pending(store)
    if not pending.exists():
        return
    if is_link_path(pending):
        raise ValueError("技能移动恢复记录不能是符号链接")
    job = json.loads(pending.read_text(encoding="utf-8"))
    source, target = Path(job["source"]), Path(job["target"])
    validate_package(store, source)
    validate_package(store, target)
    allowed = {store.config, locations_path(store), store.home / "hooks.json"}
    for entry in job["files"]:
        path = Path(entry["path"])
        if path not in allowed or is_link_path(path):
            raise ValueError("技能移动恢复路径无效")
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current not in (entry["before"], entry["after"]):
            raise ValueError("移动期间配置已被外部修改，已保留恢复记录，请先核对配置")
    if target.exists():
        if source.exists():
            raise ValueError("原位置和目标位置同时存在，请先核对技能目录")
        target.rename(source)
    elif not source.exists():
        raise ValueError("技能目录不可用，已保留移动恢复记录")
    for entry in reversed(job["files"]):
        path = Path(entry["path"])
        if entry["before"] is None:
            path.unlink(missing_ok=True)
        else:
            atomic(path, entry["before"], backup=False)
    pending.unlink()


def _relocate_hooks(value, source, target, base):
    if isinstance(value, dict):
        if (value.get("type") == "skill" and isinstance(value.get("path"), str)
                and canonical_skill_rule_path(value["path"], base) == source):
            value["path"] = str(target)
            return True
        changed = False
        for item in value.values():
            changed = _relocate_hooks(item, source, target, base) or changed
        return changed
    if isinstance(value, list):
        changed = False
        for item in value:
            changed = _relocate_hooks(item, source, target, base) or changed
        return changed
    return False


def _move(store, data, atomic):
    _recover(store, atomic)
    group = data.get("group")
    if group not in roots(store):
        raise ValueError("请选择有效的 Skills 空间")
    files = [f for f in store.files() if f["kind"] == "skills"]
    item = next((f for f in files if f["id"] == data.get("id")), None)
    if not item:
        raise ValueError("技能不存在，请刷新列表")
    if item["group"] == "builtin":
        raise ValueError("内置 Skills 不支持移动")
    if item["group"] == group:
        raise ValueError("技能已在此空间中")
    if item["revision"] != data.get("skill_revision"):
        raise ValueError("技能已更新，请刷新后重试")
    text = store.config_text()
    if hashlib.sha256(text.encode()).hexdigest() != data.get("revision"):
        raise ValueError("配置已被其他程序更新，请刷新后重试")
    if is_link_path(store.config) or is_link_path(store.home / "hooks.json"):
        raise ValueError("配置文件不能是符号链接，请先调整配置文件位置")
    source = Path(item["path"]).parent
    target_root = roots(store)[group]
    target = target_root / source.name
    validate_package(store, source)
    validate_package(store, target)
    if target_root.exists() and any(p.name.casefold() == source.name.casefold()
                                    for p in target_root.iterdir()):
        raise ValueError("目标空间已存在同名目录，不会覆盖已有文件")
    if any(f["id"] != item["id"] and f["group"] == group
           and f["name"].casefold() == item["name"].casefold() for f in files):
        raise ValueError("目标空间已存在同名 Skill，不会覆盖已有技能")
    if any(Path(f["path"]).is_relative_to(source) and f["id"] != item["id"] for f in files):
        raise ValueError("此目录包含其他 Skills，请先整理为独立技能目录")
    source_file, target_file = source / "SKILL.md", target / "SKILL.md"
    config = document.loads(text)
    rules = copy.deepcopy(config.get("skills", {}).get("config", []))
    if not isinstance(rules, list) or any(not isinstance(r, dict) for r in rules):
        raise ValueError("技能配置格式无效，请先修正配置文件")
    for rule in rules:
        if (isinstance(rule.get("path"), str)
                and canonical_skill_rule_path(rule["path"], store.home) == source_file.resolve()):
            rule["path"] = str(target_file)
    updated = text
    if rules != config.get("skills", {}).get("config", []):
        updated = document.set_value(updated, ("skills", "config"), rules)
    hooks = copy.deepcopy(config.get("hooks", {}))
    hook_changed = _relocate_hooks(hooks, source_file.resolve(), target_file, store.home)
    if hook_changed:
        updated = document.set_value(updated, ("hooks",), hooks)
    writes = []
    if updated != text:
        store.validate_config(updated, allow_adaptors=True)
        writes.append({"path": str(store.config), "before": text, "after": updated})
    hook_path = store.home / "hooks.json"
    if hook_path.exists():
        raw = hook_path.read_text(encoding="utf-8")
        external = json.loads(raw)
        if _relocate_hooks(external, source_file.resolve(), target_file, store.home):
            writes.append({"path": str(hook_path), "before": raw,
                           "after": json.dumps(external, ensure_ascii=False, indent=2) + "\n"})
            hook_changed = True
    mapping = locations(store)
    # Keep existing application/version references stable. Reserve a fresh identity
    # for any future package imported at the old path; never alias two real skills.
    mapping[str(source_file)] = uuid.uuid4().hex
    mapping[str(target_file)] = item["id"]
    location = locations_path(store)
    writes.append({"path": str(location),
                   "before": location.read_text(encoding="utf-8") if location.exists() else None,
                   "after": json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"})
    target_root.mkdir(parents=True, exist_ok=True)
    pending = _pending(store)
    atomic(pending, json.dumps({"source": str(source), "target": str(target), "files": writes},
                              ensure_ascii=False), backup=False)
    try:
        source.rename(target)
        for entry in writes:
            path = Path(entry["path"])
            if path == store.config:
                store.write_config(entry["after"], data["revision"], "移动 Skill")
            else:
                atomic(path, entry["after"])
        pending.unlink()
    except BaseException as error:
        _recover(store, atomic)
        if isinstance(error, OSError) and error.errno == errno.EXDEV:
            raise ValueError("两个 Skills 空间位于不同磁盘，暂不支持直接移动") from error
        raise
    return {"message": "Skill 已移动" + ("，相关 Hook 需重新确认授权" if hook_changed else ""),
            "id": item["id"], "group": group}


@contextmanager
def _transaction(store):
    with _LOCK:
        directory = store.home / "manager"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        owner = open_lock(directory / "skill-move.lock")
        held = False
        try:
            lock(owner)
            held = True
            yield
        finally:
            if held:
                unlock_and_close(owner)
            else:
                owner.close()


def recover(store, atomic):
    if not _pending(store).exists():
        return
    with _transaction(store):
        _recover(store, atomic)


def move(store, data, atomic):
    with _transaction(store):
        return _move(store, data, atomic)
