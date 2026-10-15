"""Best-effort memory redaction adapted from pinned Codex secrets/sanitizer.rs.

Matching order, thresholds and replacements follow that source. This heuristic
is not a guarantee that all credentials can be recognized or safely published.
See THIRD_PARTY_NOTICES.md for the upstream Apache-2.0 attribution.
"""

import re

_BEARER = re.compile(r"(?i:\bBearer)[ \t]+[A-Za-z0-9._~+/-]{16,}=*")
_OPENAI = re.compile(r"sk-[A-Za-z0-9]{20,}")
_AWS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password)\b(\s*[:=]\s*)([\"']?)[^\s\"']{8,}"
)


def redact_secrets(value: str) -> str:
    result = _BEARER.sub("Bearer [REDACTED_SECRET]", value)
    result = _OPENAI.sub("[REDACTED_SECRET]", result)
    result = _AWS.sub("[REDACTED_SECRET]", result)
    return _ASSIGNMENT.sub(lambda match: f"{match[1]}{match[2]}{match[3]}[REDACTED_SECRET]", result)
