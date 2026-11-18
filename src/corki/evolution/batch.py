"""Hermes skill_manager_batch: preflight, one approval, ordered locks, snapshot and rollback.

Host storage/field adapters for official Hermes 67e4d22d83b2 (MIT, see hermes_guard/LICENSE.txt).
"""

import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path

from corki.storage.file_lock import lock, open_lock, unlock_and_close


def operation_schema():
    def op(action, fields, required):
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": [action]},
                "name": {"type": "string"},
                **fields,
            },
            "required": ["name", "action", *required],
        }

    text = {"type": "string"}
    return {
        "anyOf": [
            op("create", {"content": text}, ["content"]),
            op(
                "patch",
                {
                    "old_text": text,
                    "new_text": text,
                    "file": text,
                    "replace_all": {"type": "boolean"},
                },
                ["old_text", "new_text"],
            ),
            op("patch", {"content": text}, ["content"]),
            op("write_file", {"file": text, "content": text}, ["file", "content"]),
            op("remove_file", {"file": text}, ["file"]),
            op("delete", {"absorbed_into": text}, []),
        ]
    }


def execute_batch(writer, args):
    from .tools import _BATCH_APPROVED, _BATCH_LOCKS, _NAME

    operations = args["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= 20:
        raise ValueError("operations 须为 1–20 项操作")
    operations = [
        dict(op, name=op.get("name") or args.get("name")) if isinstance(op, dict) else op
        for op in operations
    ]
    if any(
        not isinstance(op, dict) or not isinstance(op.get("name"), str) or not op["name"]
        for op in operations
    ):
        raise ValueError("每项操作须指定 name 和 action")
    if any(op.get("action") == "delete" for op in operations):
        if len(operations) != 1:
            raise ValueError("delete 必须单独提交，不能和其他操作混用")
        return writer._execute(operations[0])
    snapshot = writer._snapshot()
    directories, touched = {}, set()
    for index, op in enumerate(operations):
        action, name = op.get("action"), op["name"]
        if action == "create" and (not _NAME.fullmatch(name) or len(name) > 64):
            raise ValueError("新技能名称无效")
        if action not in {"create", "patch", "write_file", "remove_file"}:
            raise ValueError(f"operations[{index}] 不支持此操作")
        file = op.get("file", op.get("file_path", "SKILL.md"))
        if not isinstance(file, str):
            raise ValueError(f"operations[{index}] file 须为字符串")
        content = op.get("content", op.get("file_content"))
        if action == "create" or (action == "patch" and "content" in op):
            file = "SKILL.md"
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"operations[{index}] 须提供完整 content")
        if action == "patch":
            old = op.get("old_text", op.get("old_string"))
            new = op.get("new_text", op.get("new_string"))
            if "content" in op and (old is not None or new is not None):
                raise ValueError("patch 不能混用 content 和局部替换参数")
            if "content" not in op and (
                not isinstance(old, str) or not old or not isinstance(new, str)
            ):
                raise ValueError(f"operations[{index}] 须提供 old_text 和 new_text")
        if action in {"write_file", "remove_file"}:
            if not op.get("file", op.get("file_path")):
                raise ValueError(f"operations[{index}] 须指定支持文件 file")
            if action == "write_file" and not isinstance(content, str):
                raise ValueError(f"operations[{index}] 须提供 content")
        skill = snapshot.resolve(name)
        if skill is None:
            # Disabled invocation does not block content maintenance.
            from dataclasses import replace

            skill = replace(snapshot, disabled_paths=frozenset()).resolve(name)
        if skill is None and action != "create" and name not in directories:
            raise ValueError(f"operations[{index}] 技能不存在")
        if action == "create" and (skill is not None or name in directories):
            raise ValueError(f"operations[{index}] create 必须在技能的其他操作之前，且名称不重复")
        directory = skill.path.parent if skill is not None else writer.root / name
        if name in directories:
            directory = directories[name]
        writer._target(directory, file)
        key = (str(directory.resolve()), str(Path(file)))
        destructive = action in {"create", "write_file", "remove_file"} or "content" in op
        if destructive and key in touched:
            raise ValueError(f"operations[{index}] 会覆盖本批次前一项操作；请合并修改")
        touched.add(key)
        directories[name] = directory
        if writer._locked(directory, writing=True):
            raise ValueError("技能已由用户锁定，不能修改")
    if (
        not writer.approved
        and not _BATCH_APPROVED.get()
        and writer.fence.switch.option("skills", "write_approval", False) is True
    ):
        pending = writer.state.stage({"operations": operations}, background=bool(writer.review))
        return {"staged": True, "pending_id": pending, "message": "整批已暂存，等待用户审批"}
    locks = writer.state.root / "locks"
    locks.mkdir(parents=True, exist_ok=True, mode=0o700)
    owners, backups, results = [], {}, []
    temporary = Path(tempfile.mkdtemp(prefix="corki-skill-batch-"))
    rollback_failed = False
    token = approval = None
    try:
        for directory in sorted(set(directories.values())):
            owner = open_lock(
                locks / (hashlib.sha256(str(directory).encode()).hexdigest() + ".lock")
            )
            try:
                lock(owner)
            except BaseException:
                owner.close()
                raise
            owners.append(owner)
        token = _BATCH_LOCKS.set(frozenset(str(p.resolve()) for p in directories.values()))
        approval = _BATCH_APPROVED.set(True)
        writer.fence.check()
        for index, directory in enumerate(dict.fromkeys(directories.values())):
            backup = temporary / str(index)
            if directory.exists():
                shutil.copytree(directory, backup, symlinks=True)
                backups[directory] = backup
            else:
                backups[directory] = None
        try:
            for op in operations:
                results.append(writer._execute(op))
        except BaseException as error:
            failures = []
            for directory, backup in backups.items():
                aside = directory.with_name(directory.name + ".rollback-" + uuid.uuid4().hex)
                try:
                    if directory.exists():
                        directory.rename(aside)
                    if backup is not None:
                        try:
                            shutil.copytree(backup, directory, symlinks=True)
                        except BaseException:
                            shutil.rmtree(directory, ignore_errors=True)
                            if aside.exists():
                                aside.rename(directory)
                            raise
                    shutil.rmtree(aside, ignore_errors=True)
                except Exception as failure:
                    failures.append(str(failure))
            rollback_failed = bool(failures)
            if rollback_failed:
                raise ValueError(f"批量回滚未完成，恢复副本保留在 {temporary}") from error
            if isinstance(error, Exception):
                raise ValueError(f"operations[{len(results)}] 失败，整批已回滚：{error}") from error
            raise
        return {
            "changed": any(result.get("changed") for result in results),
            "operations_applied": len(results),
            "results": results,
        }
    finally:
        if token is not None:
            _BATCH_LOCKS.reset(token)
        if approval is not None:
            _BATCH_APPROVED.reset(approval)
        for owner in reversed(owners):
            unlock_and_close(owner)
        if not rollback_failed:
            shutil.rmtree(temporary, ignore_errors=True)
