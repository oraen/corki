"""Host adaptation of Hermes skill_lock's terminal and file-write admission checks."""

from pathlib import Path

from corki.skills.io import run_skill_io
from corki.tools.builtin.patch import ApplyPatchTool
from corki.tools.builtin.shell import ExecCommandTool

from .state import EvolutionState


class SkillLockGuard:
    def __init__(self, home, service, cwd):
        self.home, self.service, self.cwd = Path(home).resolve(), service, cwd
        self.state = EvolutionState(self.home)

    def _usage_file(self):
        return self.state.path

    def _skills_dir(self):
        return self.home / "skills"

    def locked_skill_names(self):
        return {r["name"] for r in self.state.locked_skills()}

    def skills(self):
        if self.service is None:
            from corki.skills.service import SkillService

            self.service = SkillService(
                home=self.home,
                project_root=self.cwd,
                compatibility_home=Path.home(),
                bundled_enabled=False,
                configured_project_roots=(self.home / "skills/.system",),
            )
        return self.service.snapshot(self.cwd).skills

    async def before_tool(self, call, tool, cwd):
        if not isinstance(tool, (ExecCommandTool, ApplyPatchTool)) or not self.state.path.exists():
            return None
        return await run_skill_io(self.check, call, tool, cwd)

    def check(self, call, tool, cwd):
        from .hermes_guard import skill_lock

        token = skill_lock._context.set(self)
        try:
            args = call.arguments or {}
            if isinstance(tool, ExecCommandTool) and isinstance(args.get("cmd"), str):
                return skill_lock.command_refusal(args["cmd"], args.get("workdir") or str(cwd))
            if isinstance(tool, ApplyPatchTool) and isinstance(args.get("patch"), str):
                paths = []
                # These are the native patch grammar's four path-bearing directives.
                for line in args["patch"].splitlines():
                    for prefix in (
                        "*** Add File: ",
                        "*** Update File: ",
                        "*** Delete File: ",
                        "*** Move to: ",
                    ):
                        if line.startswith(prefix):
                            paths.append(str(Path(cwd) / line[len(prefix) :]))
                return skill_lock.path_refusal(paths)
            return None
        finally:
            skill_lock._context.reset(token)
