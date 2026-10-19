"""Transactional goal state and idempotent usage accounting, separate from history."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import closing, suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import uuid4

STATUSES = frozenset({"active", "paused", "blocked", "usage_limited", "budget_limited", "complete"})
UNSET = object()


def validate_objective(objective):
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("goal objective must not be empty")
    objective = objective.strip()
    if len(objective) > 4000:
        raise ValueError("goal objective must be at most 4000 characters")
    try:
        objective.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("goal objective must be valid UTF-8 text") from exc
    return objective


def validate_budget(budget):
    if budget is not None and (type(budget) is not int or not 0 < budget < 2**63):
        raise ValueError("goal token budget must be a positive integer")


@dataclass(frozen=True)
class Goal:
    thread_id: str
    goal_id: str
    objective: str
    status: str = "active"
    token_budget: int | None = None
    tokens_used: int = 0
    time_used_seconds: int = 0
    created_at: int = 0
    updated_at: int = 0

    def response(self):
        return {
            "goal": asdict(self),
            "remaining_tokens": None
            if self.token_budget is None
            else max(0, self.token_budget - self.tokens_used),
            "completion_budget_report": (
                "Goal achieved. Report final tokens_used/token_budget "
                "and time_used_seconds from this result."
                if self.status == "complete"
                and (self.token_budget is not None or self.time_used_seconds)
                else None
            ),
        }


class GoalStore:
    def __init__(self, path: Path):
        self.path = path

    async def run(self, operation):
        # A cancelled caller must not release its mutation lock while a worker
        # can still commit a stale change.
        task = asyncio.create_task(asyncio.to_thread(self._run, operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(task)
            task.result()
            raise

    def _run(self, operation):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.executescript("""
                CREATE TABLE IF NOT EXISTS thread_goals (
                    thread_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'active','paused','blocked','usage_limited','budget_limited','complete')),
                    token_budget INTEGER, tokens_used INTEGER NOT NULL,
                    time_used_seconds INTEGER NOT NULL, created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS goal_usage (
                    thread_id TEXT NOT NULL, goal_id TEXT NOT NULL, usage_id TEXT NOT NULL,
                    PRIMARY KEY(thread_id, goal_id, usage_id));
                CREATE TABLE IF NOT EXISTS goal_deferrals (thread_id TEXT PRIMARY KEY);
            """)
            self.path.chmod(0o600)
            db.execute("BEGIN IMMEDIATE")
            with db:
                return operation(db)

    @staticmethod
    def read(db, thread_id):
        row = db.execute(
            "SELECT * FROM thread_goals WHERE thread_id=?", (str(thread_id),)
        ).fetchone()
        return Goal(**dict(row)) if row else None

    @staticmethod
    def write(db, goal):
        values = asdict(goal)
        db.execute(
            f"INSERT OR REPLACE INTO thread_goals ({','.join(values)}) "
            f"VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        )
        return goal

    async def get(self, thread_id):
        return await self.run(lambda db: self.read(db, thread_id))

    async def create(self, thread_id, objective, token_budget=None):
        objective = validate_objective(objective)
        validate_budget(token_budget)

        def create(db):
            previous = self.read(db, thread_id)
            if previous and previous.status != "complete":
                raise ValueError("This thread has an unfinished goal; edit or clear it first.")
            db.execute("DELETE FROM goal_deferrals WHERE thread_id=?", (str(thread_id),))
            if previous:
                db.execute(
                    "DELETE FROM goal_usage WHERE thread_id=? AND goal_id=?",
                    (str(thread_id), previous.goal_id),
                )
            now = int(time.time())
            return self.write(
                db,
                Goal(
                    str(thread_id),
                    str(uuid4()),
                    objective,
                    token_budget=token_budget,
                    created_at=now,
                    updated_at=now,
                ),
            )

        return await self.run(create)

    async def update(
        self, thread_id, *, status=None, objective=None, token_budget=UNSET, expected_id=None
    ):
        if status is not None and status not in STATUSES:
            raise ValueError("invalid goal status")
        if objective is not None:
            objective = validate_objective(objective)
        if token_budget is not UNSET:
            validate_budget(token_budget)

        def update(db):
            goal = self.read(db, thread_id)
            if goal is None:
                raise ValueError("This thread has no goal.")
            if expected_id is not None and goal.goal_id != expected_id:
                return goal
            new_status = status or goal.status
            budget = goal.token_budget if token_budget is UNSET else token_budget
            if goal.status == "budget_limited" and new_status in {"paused", "blocked"}:
                new_status = "budget_limited"
            if new_status == "active" and budget is not None and goal.tokens_used >= budget:
                new_status = "budget_limited"
            return self.write(
                db,
                replace(
                    goal,
                    objective=objective or goal.objective,
                    status=new_status,
                    token_budget=budget,
                    updated_at=int(time.time()),
                ),
            )

        return await self.run(update)

    async def clear(self, thread_id):
        def clear(db):
            goal = self.read(db, thread_id)
            db.execute("DELETE FROM thread_goals WHERE thread_id=?", (str(thread_id),))
            db.execute("DELETE FROM goal_deferrals WHERE thread_id=?", (str(thread_id),))
            if goal:
                db.execute(
                    "DELETE FROM goal_usage WHERE thread_id=? AND goal_id=?",
                    (str(thread_id), goal.goal_id),
                )

        await self.run(clear)

    async def account(self, thread_id, goal_id, *, tokens=0, seconds=0, usage_id=None):
        def account(db):
            goal = self.read(db, thread_id)
            if (
                goal is None
                or goal.goal_id != goal_id
                or goal.status not in {"active", "budget_limited"}
            ):
                return goal
            delta = max(0, tokens)
            if usage_id is not None:
                inserted = db.execute(
                    "INSERT OR IGNORE INTO goal_usage VALUES (?,?,?)",
                    (str(thread_id), goal_id, usage_id),
                ).rowcount
                if not inserted:
                    delta = 0
            total = min(2**63 - 1, goal.tokens_used + delta)
            status = (
                "budget_limited"
                if goal.token_budget is not None and total >= goal.token_budget
                else goal.status
            )
            return self.write(
                db,
                replace(
                    goal,
                    tokens_used=total,
                    time_used_seconds=goal.time_used_seconds + max(0, seconds),
                    status=status,
                    updated_at=int(time.time()),
                ),
            )

        return await self.run(account)

    async def deferred(self, thread_id):
        return await self.run(
            lambda db: (
                db.execute(
                    "SELECT 1 FROM goal_deferrals WHERE thread_id=?", (str(thread_id),)
                ).fetchone()
                is not None
            )
        )

    async def clear_deferral(self, thread_id):
        await self.run(
            lambda db: db.execute("DELETE FROM goal_deferrals WHERE thread_id=?", (str(thread_id),))
        )

    async def copy_snapshot(self, thread_id, goal):
        def copy(db):
            if self.read(db, thread_id) is None:
                self.write(db, replace(goal, thread_id=str(thread_id)))
                db.execute("INSERT OR IGNORE INTO goal_deferrals VALUES (?)", (str(thread_id),))

        await self.run(copy)
