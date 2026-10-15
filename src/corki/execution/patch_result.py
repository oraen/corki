"""Native patch outcome; post-start transport failure means unknown effects."""

import json
from dataclasses import dataclass

from corki.protocol.patches import decode_patch_record, parse_patch_delta


@dataclass(frozen=True, slots=True)
class PatchExecution:
    exit_code: int
    stdout: str
    stderr: str
    sandbox_denied: bool


@dataclass(frozen=True, slots=True)
class PatchResult:
    success: bool
    output: str
    delta_json: str
    execution: PatchExecution | None = None

    @classmethod
    def parse(cls, payload: str):
        value = decode_patch_record(payload)
        if (
            set(value)
            != (
                {"version", "success", "output", "delta"}
                | ({"execution"} if value.get("version") == 2 else set())
            )
            or type(value["version"]) is not int
            or value["version"] not in (1, 2)
            or type(value["success"]) is not bool
            or not isinstance(value["output"], str)
        ):
            raise ValueError("invalid native patch outcome")
        delta_json = json.dumps(value["delta"], ensure_ascii=False)
        parse_patch_delta(delta_json)
        execution = None
        if value["version"] == 2:
            raw = value["execution"]
            if (
                not isinstance(raw, dict)
                or set(raw) != {"exit_code", "stdout", "stderr", "sandbox_denied"}
                or type(raw["exit_code"]) is not int
                or raw["exit_code"] != (0 if value["success"] else 1)
                or not isinstance(raw["stdout"], str)
                or not isinstance(raw["stderr"], str)
                or type(raw["sandbox_denied"]) is not bool
                or (value["success"] and raw["sandbox_denied"])
            ):
                raise ValueError("invalid native patch execution evidence")
            execution = PatchExecution(**raw)
        return cls(value["success"], value["output"], delta_json, execution)

    def append_attempt(self, later: "PatchResult") -> "PatchResult":
        before, after = parse_patch_delta(self.delta_json), parse_patch_delta(later.delta_json)
        delta_json = json.dumps(
            {
                "version": 1,
                "exact": before["exact"] and after["exact"],
                "changes": before["changes"] + after["changes"],
            },
            ensure_ascii=False,
        )
        # A combined record must retain the same durable transport bounds. If it
        # cannot fit, never publish a truncated delta as exact.
        delta_notice = ""
        try:
            parse_patch_delta(delta_json)
        except ValueError:
            delta_json = '{"version":1,"exact":false,"changes":[]}'
            delta_notice = " Combined delta exceeded its transport limit; net effects are unknown."
        return PatchResult(
            later.success,
            "Retried once without sandbox after fresh host approval. "
            f"First attempt: {self.output}\nRetry: {later.output}{delta_notice}",
            delta_json,
            later.execution,
        )

    @classmethod
    def unknown(cls, error: Exception):
        return cls(
            False,
            "apply_patch outcome unknown after execution started; no rollback performed. "
            "Do not automatically retry. " + str(error)[:2000],
            '{"version":1,"exact":false,"changes":[]}',
        )
