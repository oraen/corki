"""Window and output-policy projection of Codex's pinned bundled model catalog.

Source: codex-rs/models-manager/models.json at
ddf04ad26789d040f9ef6a96736f76602e35a6cc. Missing effective percentages
deserialize as 95 upstream. These are reference defaults, not live capacity
claims; explicit catalogs are authoritative. See THIRD_PARTY_NOTICES.md.
"""

from corki.protocol.context import ModelContextInfo
from corki.protocol.truncation import TruncationPolicy

_TOKEN_POLICY = TruncationPolicy("tokens", 10_000)

BUNDLED_MODEL_CONTEXTS = (
    ModelContextInfo("gpt-6-astra", 272_000, 872_000, truncation_policy=_TOKEN_POLICY),
    ModelContextInfo("gpt-5.6-sol", 272_000, 872_000, truncation_policy=_TOKEN_POLICY),
    ModelContextInfo("gpt-5.6-terra", 272_000, 872_000, truncation_policy=_TOKEN_POLICY),
    ModelContextInfo("gpt-5.6-luna", 272_000, 872_000, truncation_policy=_TOKEN_POLICY),
    ModelContextInfo("gpt-daybreak-blue-latest", 272_000, 872_000, truncation_policy=_TOKEN_POLICY),
    ModelContextInfo("gpt-daybreak-red-latest", 372_000, 372_000, truncation_policy=_TOKEN_POLICY),
    ModelContextInfo("gpt-5.5", 272_000, 272_000, truncation_policy=_TOKEN_POLICY),
    ModelContextInfo("gpt-5.4", 272_000, 1_000_000, truncation_policy=_TOKEN_POLICY),
    ModelContextInfo("gpt-5.4-mini", 272_000, 272_000, truncation_policy=_TOKEN_POLICY),
    ModelContextInfo("gpt-5.2", 272_000, 272_000),
    ModelContextInfo("codex-auto-review", 272_000, 872_000, truncation_policy=_TOKEN_POLICY),
)
