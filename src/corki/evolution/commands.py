"""User-directed maintenance and approval; model tools cannot unlock or approve themselves."""

import asyncio
import json
from pathlib import Path

from corki.config import CorkiPaths, CorkiSettings
from corki.skills.service import SkillService

from .maintenance import Curator, consolidate
from .state import EvolutionState
from .switch import EvolutionSwitch
from .tools import SkillManageTool, WriteFence


def configure_parser(commands):
    parser = commands.add_parser("curator", help="inspect and maintain learned skills")
    actions = parser.add_subparsers(dest="curator_action", required=True)
    for name in ("status", "pause", "resume", "approvals", "archives"):
        actions.add_parser(name)
    run = actions.add_parser("run")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--consolidate", action="store_true")
    for name in (
        "lock",
        "unlock",
        "pin",
        "unpin",
        "adopt",
        "archive",
        "restore",
        "approve",
        "reject",
    ):
        child = actions.add_parser(name)
        child.add_argument("name")


def run(arguments, *, home=None, cwd=None, compatibility_home=None):
    home = Path(home or CorkiPaths.discover().home).expanduser().resolve()
    cwd = Path(cwd or Path.cwd()).resolve()
    state, switch = EvolutionState(home), EvolutionSwitch(home)
    switch.enabled()
    service = SkillService(
        home=home, project_root=cwd, compatibility_home=compatibility_home or Path.home()
    )
    curator = Curator(home, service, cwd, switch.document)
    action = arguments.curator_action
    if action == "status":
        return {
            **state.status(),
            "skills": state.usage(),
            "maintenance": state.maintenance_value("last_report"),
        }
    if action in {"pause", "resume"}:
        state.save_maintenance("paused", action == "pause")
        return {"paused": action == "pause"}
    if action == "approvals":
        return {"pending": state.pending()}
    if action == "archives":
        with state.database() as db:
            return {
                "archives": [
                    dict(r)
                    for r in db.execute("SELECT * FROM skill_archives ORDER BY created_at DESC")
                ]
            }
    if action == "run":
        report = curator.run(dry_run=arguments.dry_run, force=True)
        if arguments.consolidate and not arguments.dry_run:
            settings = CorkiSettings.for_directory(cwd, config_file=home / "settings.json")
            asyncio.run(consolidate(curator, settings, switch))
        return report
    identity = arguments.name
    if action == "restore":
        return {"restored": curator.restore(identity)}
    if action in {"approve", "reject"}:
        pending = next((r for r in state.pending() if r["id"] == identity), None)
        if pending is None:
            raise ValueError("审批记录不存在")
        result = {"rejected": identity}
        if action == "approve":
            writer = SkillManageTool(
                home,
                service,
                cwd,
                switch,
                fence=WriteFence(switch, enabled=True),
                approved=True,
                background_origin=pending["origin"] == "review",
            )
            result = writer._execute(json.loads(pending["payload"]))
        state.remove_pending(identity)
        return result
    skill = service.snapshot(cwd).resolve(identity)
    if skill is None:
        raise ValueError("技能不存在、已禁用或名称不唯一")
    path = str(skill.path.parent.resolve())
    state.ensure_skill_usage(path, skill.qualified_name)
    if action in {"lock", "unlock", "pin", "unpin"}:
        state.set_skill_flags(
            path,
            locked=action == "lock" if action in {"lock", "unlock"} else None,
            pinned=action == "pin" if action in {"pin", "unpin"} else None,
        )
        return {"skill": identity, "action": action}
    if action == "adopt":
        if (
            not Path(path).is_relative_to(home / "skills")
            or skill.scope.value == "system"
            or skill.plugin_id
        ):
            raise ValueError("只能将本地用户技能交给定期整理，不能接管内置或外部来源的技能")
        with state.database() as db:
            db.execute("UPDATE skill_usage SET created_by='agent' WHERE path=?", (path,))
        return {"adopted": identity}
    if action == "archive":
        if state.skill_locked(path):
            raise ValueError("此技能已锁定")
        return {"archived": curator.archive(Path(path), skill.qualified_name, external=True)}
    raise ValueError("不支持的维护操作")
