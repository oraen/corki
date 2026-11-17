"""Application management and host routing, kept outside Corki core."""

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from .execution import ExecutionHost
from .models import AppError
from .store import ApplicationStore


class Applications:
    def __init__(self, manager, **runner_options):
        self.manager = manager
        self.store = ApplicationStore(manager.home)
        self.execution = ExecutionHost(self.store, **runner_options)
        self.execution.compatibility_home = manager.compatibility_home
        self.submit_lock = threading.RLock()
        from .access_store import AccessStore

        self.access = AccessStore(self.store.root)
        from .gateway import ApplicationGateway

        self.gateway = ApplicationGateway(self)
        from .kafka_service import KafkaService

        self.kafka = KafkaService(self)

    def skills(self, config):
        from corki.config.skills import disabled_skill_paths, parse_skill_rules

        files = [f for f in self.manager.files() if f.get("kind") == "skills"]
        rules = parse_skill_rules(config.get("skills", {}).get("config", []), self.store.home)
        disabled = disabled_skill_paths(rules, ((f["name"], Path(f["path"])) for f in files))
        for file in files:
            file["enabled"] = file.get("group") == "work" or (
                config.get("skills", {}).get("enabled", True)
                and Path(file["path"]).resolve() not in disabled
            )
        return files

    def catalog(self):
        try:
            return self._catalog()
        except (ValueError, TypeError, AttributeError, OSError):
            return dict(
                providers=[],
                default={},
                permissions=[],
                skills=[],
                error="Corki 配置无效，请在开发者 → 配置文件中修复后重试",
            )

    def _catalog(self):
        from corki.config import document
        from corki.config.providers import effective_default

        config = document.loads(self.manager.config_text())
        profiles = config.get("providers", {})
        return dict(
            providers=[
                dict(
                    id=name, models=list(p.get("models", {})), default_model=p.get("default_model")
                )
                for name, p in profiles.items()
                if p.get("enabled", True) and p.get("models")
            ],
            default=effective_default(config),
            permissions=[
                ":read-only",
                ":workspace",
                ":danger-full-access",
                *config.get("permissions", {}),
            ],
            skills=[
                {k: f[k] for k in ("id", "name", "group") if k in f}
                for f in self.skills(config)
                if f["enabled"]
            ],
        )

    def prepare(self, app, directory):
        from corki.config import document
        from corki.config.hooks import ManagedHookPolicy
        from corki.config.permissions import parse_execution_permissions
        from corki.config.providers import effective_default, provider_default_model
        from corki.config.settings import CorkiSettings
        from corki.protocol.input_mentions import InputMention

        try:
            config = document.loads(self.manager.config_text())
            if not isinstance(config, dict) or not isinstance(config.get("providers", {}), dict):
                raise ValueError()
            if any(
                not isinstance(p, dict)
                or not isinstance(p.get("models", {}), dict)
                or any(not isinstance(m, dict) for m in p.get("models", {}).values())
                for p in config.get("providers", {}).values()
            ):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, OSError) as error:
            raise AppError("Corki 配置无效，请先修复配置文件") from error
        profiles = config.get("providers", {})
        default = effective_default(config)
        provider = app["provider"] or default.get("provider")
        profile = profiles.get(provider)
        if not profile or not profile.get("enabled", True) or not profile.get("models"):
            raise AppError("应用供应商不可用，请检查模型管理或应用配置")
        model = app["model"] or provider_default_model(config, provider, profile)
        if model not in profile["models"] or profile["models"][model].get("enabled", True) is False:
            raise AppError("应用模型不可用，请重新选择")
        path = Path(directory).expanduser().resolve()
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            raise AppError("工作目录不存在或没有访问权限")
        settings = CorkiSettings.for_directory(
            path, config_file=self.manager.config, provider=provider, model=model
        )
        settings = replace(
            settings,
            memories_enabled=False,
            memories_use=False,
            memories_generate=False,
            memories_background_enabled=False,
            self_evolution_enabled=False,
            hooks_enabled=False,
            managed_hook_policy=ManagedHookPolicy(
                (self.store.root / "host-hooks.json").resolve(), only_managed=True
            ),
            goals_enabled=False,
        )
        if app["permissions"]:
            available = {
                ":read-only",
                ":workspace",
                ":danger-full-access",
                *config.get("permissions", {}),
            }
            if app["permissions"] not in available:
                raise AppError("应用操作权限配置已不存在")
            selected = parse_execution_permissions(
                None, path, configuration={**config, "default_permissions": app["permissions"]}
            )
            if settings.execution_permissions is not None:
                selected = replace(
                    selected, requirements=settings.execution_permissions.requirements
                )
            settings = replace(settings, execution_permissions=selected)
        mentions = []
        if app["skills"]:
            if not settings.skills_enabled:
                raise AppError("Skills 已在全局配置中关闭，请先开启后再使用")
            files = {f["id"]: f for f in self.skills(config)}
            for skill in app["skills"]:
                file = files.get(skill)
                if not file or not file["enabled"]:
                    raise AppError("应用选择的 Skill 已不存在或被停用")
                path_value = file.get("path")
                if not path_value:
                    raise AppError("无法定位应用选择的 Skill")
                mentions.append(InputMention(file["name"], path_value, "skill"))
        return settings, tuple(mentions)

    def get(self, query):
        action = query.get("action", "list")
        app = query.get("app", "")
        if action == "list":
            return self.store.listing(
                query.get("q", ""), query.get("type", ""), int(query.get("offset", 0))
            )
        if action in ("access", "kafka"):
            owner = app or ""
            if owner:
                with self.store.db() as db:
                    if not db.execute("SELECT 1 FROM apps WHERE id=?", (owner,)).fetchone():
                        raise AppError("应用不存在", 404)
            if action == "access":
                if not owner:
                    raise AppError("请选择应用")
                return {
                    **self.access.app(owner),
                    "global_enabled": self.access.global_state()["enabled"],
                }
            return dict(
                connections=self.access.connections(owner),
                global_state=self.access.global_state(),
                runtime=self.kafka.status(),
                metrics=self.access.metrics(),
            )
        if action == "catalog":
            return self.catalog()
        if action == "gateway-status":
            return self.gateway.status(include_key=query.get("include_key") == "1")
        if action == "detail":
            return self.store.prompt_history(
                self.store.app(app, int(query["version"]) if query.get("version") else None)
            )
        if action == "sessions":
            return self.store.sessions(app, int(query.get("offset", 0)))
        if action == "submission":
            self.store.app(app)
            with self.store.db() as db:
                row = db.execute(
                    "SELECT r.id AS run,r.session,r.input_sha FROM runs r "
                    "JOIN sessions s ON s.id=r.session WHERE s.app=? AND r.request_id=?",
                    (app, query.get("request")),
                ).fetchone()
            if row is None:
                raise AppError("尚未确认收到该请求，请稍后重试", 404)
            return dict(row)
        if action == "history":
            session = self.store.session(app, query.get("session"))
            # A process restart must reconcile stale runs before showing them as live.
            if self.execution.loop is None:
                try:
                    self.execution.start()
                except AppError as error:
                    if error.status != 409:
                        raise
            return self.history(self.store.app(app), session, query.get("before"))
        if action == "run":
            session = self.store.session(app, query.get("session"))
            run = self.store.run(query.get("run"))
            if run["session"] != session["id"]:
                raise AppError("运行任务不属于当前会话", 403)
            live = self.execution.snapshot(run["id"])
            if live and str(live["sequence"]) == query.get("sequence"):
                return {"unchanged": True}
            if live:
                run.update(live)
            elif self.store.app(app)["type"] == "agent":
                run = self.agent_run_view(run, session)
            return run
        raise AppError("未知应用查询")

    def mutate(self, data):
        action, identity = data.get("action"), data.get("app")
        if action in ("save-access", "save-kafka", "delete-kafka", "retry-kafka", "kafka-global"):
            owner = identity or ""
            if owner:
                with self.store.db() as db:
                    if not db.execute("SELECT 1 FROM apps WHERE id=?", (owner,)).fetchone():
                        raise AppError("应用不存在", 404)
            if action == "save-access":
                if not owner:
                    raise AppError("请选择应用")
                with self.submit_lock:
                    result = self.access.save_app(owner, data)
                self.kafka.wake()
                return result
            if action == "kafka-global":
                return self.kafka.global_enabled(data.get("enabled"), data.get("expected"))
            if action == "save-kafka":
                return self.kafka.save(
                    owner, data.get("values"), data.get("connection"), data.get("expected")
                )
            if action == "delete-kafka":
                return self.kafka.delete(
                    data.get("connection"), owner, data.get("expected"),
                    stop=data.get("stop") is True,
                )
            self.access.connection(data.get("connection"), owner)
            self.kafka.ensure_owner()
            self.access.retry(data.get("connection"))
            self.kafka.wake()
            return {"ok": True}
        if action == "gateway-configure":
            return self.gateway.change(data)
        if action == "set-enabled":
            with self.submit_lock:
                result = self.store.set_enabled(identity, data.get("enabled"))
            self.kafka.wake()
            return result
        if action == "save":
            reason = "编辑提示词" if data.get("section") == "prompt" else "编辑应用"
            return self.store.prompt_history(
                self.store.save(data.get("values"), identity, data.get("expected"), reason)
            )
        if action == "restore-prompt":
            version = data.get("version")
            if type(version) is not int or version <= 0:
                raise AppError("请选择有效的提示词版本")
            current = self.store.app(identity)
            past = self.store.app(identity, version)
            values = {key: current[key] for key in (
                "name", "description", "type", "provider", "model", "prompt",
                "context", "directory", "permissions", "skills",
            )}
            values["prompt"] = past["prompt"]
            return self.store.prompt_history(
                self.store.save(values, identity, data.get("expected"), "回滚提示词")
            )
        if action == "restore":
            past = self.store.app(identity, data.get("version"))
            values = {
                k: past[k]
                for k in (
                    "name",
                    "description",
                    "type",
                    "provider",
                    "model",
                    "prompt",
                    "context",
                    "directory",
                    "permissions",
                    "skills",
                )
            }
            return self.store.save(values, identity, data.get("expected"), "回滚历史版本")
        if action == "choose-directory":
            return {"directory": self.manager.choose_launch_directory().get("path") or ""}
        if action == "send":
            return self.submit(data)
        if action in ("stop", "respond"):
            self.store.session(identity, data.get("session"))
            run = self.store.run(data.get("run"))
            if run["session"] != data.get("session"):
                raise AppError("运行任务不属于当前会话", 403)
            if self.execution.loop:
                self.execution.call(
                    self.execution.stop(run["id"])
                    if action == "stop"
                    else self.execution.respond(run["id"], data)
                )
            return {"ok": True}
        if action == "rename-session":
            self.store.session(identity, data.get("session"))
            title = data.get("title")
            if not isinstance(title, str) or not title.strip() or len(title) > 100:
                raise AppError("请填写有效的会话名称")
            with self.store.db() as db:
                db.execute(
                    "UPDATE sessions SET title=? WHERE id=?", (title.strip(), data["session"])
                )
            return {"ok": True}
        if action in ("delete", "delete-session"):
            return self.delete(data)
        raise AppError("未知应用操作")

    def submit(self, data, *, transport=None):
        text, request = data.get("text"), data.get("request")
        if not isinstance(text, str) or not text.strip() or len(text) > 262144:
            raise AppError("请输入消息，单条消息不能超过 256K 字符")
        if not isinstance(request, str) or not 1 <= len(request) <= 100:
            raise AppError("提交标识无效")
        self.execution.start()
        with self.submit_lock:
            app = self.store.app(data.get("app"))
            with self.store.db() as db:
                old = db.execute(
                    (
                        "SELECT r.*,s.app FROM runs r JOIN sessions s ON s.id=r.session "
                        "WHERE r.request_id=?"
                    ),
                    (request,),
                ).fetchone()
            if old:
                if old["app"] != app["id"] or (
                    data.get("session") and data["session"] != old["session"]
                ):
                    raise AppError("提交标识已属于其他会话", 409)
                if old["input_sha"] != hashlib.sha256(text.encode()).hexdigest():
                    raise AppError("上次提交已被接收，请刷新会话确认后再发送新消息", 409)
                return {"session": old["session"], "run": old["id"]}
            self.access.authorize(app, transport)
            if not app["enabled"]:
                raise AppError("应用已关闭，请先开启", 409)
            if data.get("session"):
                session = self.store.session(app["id"], data["session"])
                directory = session["directory"]
            else:
                directory = app["directory"] or str(self.store.home / "main/default")
                if not app["directory"]:
                    Path(directory).mkdir(parents=True, exist_ok=True)
                session = None
            settings, mentions = self.prepare(app, directory)
            if session is None:
                session = self.store.new_session(app["id"], str(settings.working_directory), text)
            run, fresh = self.store.accept(session["id"], request, app, text)
            if fresh:
                self.execution.call(
                    self.execution.schedule(run, app, session, text, settings, mentions)
                )
            return {"session": session["id"], "run": run["id"]}

    def history(self, app, session, before=None):
        runs, more = self.store.runs(session["id"], before)
        for run in runs:
            live = self.execution.snapshot(run["id"])
            if live:
                run.update(live)
            elif app["type"] == "agent":
                run.update(self.agent_run_view(run, session))
        return {"items": runs, "has_more": more, "session": session}

    def agent_run_view(self, run, session):
        canonical = self.agent_history(session["id"], run["core_turn"])
        canonical["input"] = canonical.get("input") or run["input"]
        # Like desktop, retain only unfinished visible text that was never a
        # completed model item. It is display-only, never replayed to the model.
        if run["answer"] and not any(m["content"] == run["answer"] for m in canonical["messages"]):
            canonical["messages"].append(dict(id=run["id"] + ":partial", content=run["answer"]))
        return {**run, **canonical}

    def agent_history(self, session, turn):
        if not turn or not self.execution.database.exists():
            return dict(input="", messages=[], tools={})
        result = dict(input="", messages=[], tools={})
        with closing(sqlite3.connect(self.execution.database)) as db:
            rows = db.execute(
                (
                    "SELECT kind,payload_json FROM chat_history WHERE thread_id=? AND "
                    "turn_id=? ORDER BY sequence"
                ),
                (session, turn),
            )
            for kind, payload in rows:
                p = json.loads(payload)
                if kind == "user_message":
                    result["input"] += p.get("content", "")
                elif kind == "assistant_message":
                    result["messages"].append(dict(id=p.get("id"), content=p.get("content", "")))
                elif kind == "tool_call":
                    call = p.get("call", {})
                    result["tools"][call.get("id", "")] = dict(
                        name=call.get("name", ""),
                        arguments=json.dumps(call.get("arguments", {}), ensure_ascii=False),
                        output="",
                        status="unknown",
                    )
                elif kind == "tool_result":
                    tool = result["tools"].get(p.get("call_id"))
                    if tool:
                        tool.update(
                            status="failed" if p.get("is_error") else "completed",
                            output=str(p.get("content", ""))[-65536:],
                        )
        return result

    def delete(self, data):
        app = self.store.app(data.get("app"))
        self.execution.start()
        with self.submit_lock:
            if data["action"] == "delete-session":
                sessions = [self.store.session(app["id"], data.get("session"))]
            else:
                with self.store.db() as db:
                    sessions = [
                        dict(r)
                        for r in db.execute("SELECT * FROM sessions WHERE app=?", (app["id"],))
                    ]
            for session in sessions:
                with self.store.db() as db:
                    active = db.execute(
                        (
                            "SELECT id FROM runs WHERE session=? AND status IN "
                            "('submitted','running','waiting','stopping')"
                        ),
                        (session["id"],),
                    ).fetchall()
                if active and data.get("stop") is not True:
                    raise AppError("应用有运行中的任务，确认后将停止并永久删除", 409)
                for row in active:
                    if self.execution.loop:
                        self.execution.call(self.execution.stop(row["id"]))
                if self.execution.database.exists() and app["type"] == "agent":
                    from corki.storage.thread_delete import SQLiteThreadDeleteStore

                    from .ownership import thread_family

                    with closing(sqlite3.connect(self.execution.database)) as core:
                        family = thread_family(core, session["id"])

                    self.execution.start()
                    for thread in reversed(family):
                        self.execution.call(
                            SQLiteThreadDeleteStore(self.execution.database).delete(thread)
                        )
                with self.store.db() as db:
                    db.execute("DELETE FROM runs WHERE session=?", (session["id"],))
                    db.execute("DELETE FROM sessions WHERE id=?", (session["id"],))
            if data["action"] == "delete":
                with self.store.db() as db:
                    db.execute("DELETE FROM versions WHERE app=?", (app["id"],))
                    db.execute("DELETE FROM apps WHERE id=?", (app["id"],))
                self.access.remove_app(app["id"])
        self.kafka.wake()
        return {"ok": True}

    def close(self):
        self.kafka.close()
        self.gateway.close()
        self.execution.close()
