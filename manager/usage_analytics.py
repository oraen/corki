"""Read-only usage projections over the authoritative model-step ledger."""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


class UsageAnalytics:
    def __init__(self, home):
        self.home = Path(home)
        self.database = self.home / "sessions/corki.db"

    def connect(self):
        db = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def attach(db, path, schema, tables):
        if not path.is_file():
            return False
        db.execute(f"ATTACH DATABASE ? AS {schema}", (path.as_uri() + "?mode=ro",))
        available = {
            r[0] for r in db.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")
        }
        return set(tables) <= available

    def dashboard(self, today=None):
        today = today or datetime.now().astimezone().date()
        first = today - timedelta(days=29)
        days = {
            str(first + timedelta(days=i)): dict(
                day=str(first + timedelta(days=i)), input=0, output=0, steps=0
            )
            for i in range(30)
        }
        providers, types = {}, {"会话": 0, "钉钉": 0, "定时任务": 0}
        if not self.database.is_file():
            return dict(days=list(days.values()), providers=[], types=[])
        with closing(self.connect()) as db:
            # Aggregate once, without loading prompts, responses or tool payloads.
            rows = db.execute(
                """
                SELECT date(s.created_at,'localtime') day,
                  s.provider provider,
                  CASE WHEN t.kind='cron' THEN '定时任务'
                    WHEN t.kind='dingtalk' THEN '钉钉'
                    WHEN t.kind='wechat' THEN '微信'
                    WHEN t.kind='feishu' THEN '飞书'
                    WHEN t.kind IN ('telegram','whatsapp') THEN t.kind
                    WHEN t.kind='gateway' THEN '机器人' ELSE '会话' END kind,
                  sum(s.input_tokens) input, sum(s.output_tokens) output, count(*) steps
                FROM usage_events s LEFT JOIN usage_threads t ON t.thread_id=s.thread_id
                WHERE julianday(s.created_at)>=julianday(?,'utc')
                  AND julianday(s.created_at)<julianday(?,'utc')
                GROUP BY day,provider,kind
            """,
                (str(first), str(today + timedelta(days=1))),
            )
            for row in rows:
                day = days[row["day"]]
                for key in ("input", "output", "steps"):
                    day[key] += row[key] or 0
                tokens = (row["input"] or 0) + (row["output"] or 0)
                provider = row["provider"]
                if not isinstance(provider, str) or not provider.strip():
                    provider = "未记录供应商"
                providers[provider] = providers.get(provider, 0) + tokens
                types[row["kind"]] = types.get(row["kind"], 0) + tokens

        def slices(values):
            return [
                dict(name=name, tokens=value)
                for name, value in sorted(values.items(), key=lambda pair: (-pair[1], pair[0]))
                if value > 0
            ]

        return dict(days=list(days.values()), providers=slices(providers), types=slices(types))

    def cron(self, payload, *, jobs=False):
        """Enrich only requested rows; job totals include every retained run."""
        rows = payload.get("jobs" if jobs else "records", [])
        if not rows:
            return payload
        totals = {}
        try:
            if self.database.is_file():
                with closing(self.connect()) as db:
                    key = "job_id" if jobs else "run_id"
                    for start in range(0, len(rows), 400):
                        ids = [row["id"] for row in rows[start : start + 400]]
                        marks = ",".join("?" for _ in ids)
                        result = db.execute(
                            f"""
                            SELECT o.{key} owner,sum(s.input_tokens+s.output_tokens) tokens
                            FROM usage_threads o CROSS JOIN usage_events s
                                ON s.thread_id=o.thread_id
                            WHERE o.{key} IN ({marks}) GROUP BY o.{key}
                        """,
                            ids,
                        )
                        totals.update({row["owner"]: row["tokens"] for row in result})
            for row in rows:
                row["token_usage"] = totals.get(row["id"], 0)
        except sqlite3.Error:
            # Unreadable is distinct from a successfully measured zero.
            for row in rows:
                row["token_usage"] = None
        return payload
