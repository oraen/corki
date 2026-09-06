"""Model request/response contracts and provider adapters."""

from corki.models.base import ModelError, ModelErrorKind, ModelPort
from corki.models.capabilities import (
    ApiMode,
    ProviderCapabilities,
    ReasoningProtocol,
    StructuredOutputProtocol,
    resolve_capabilities,
)
from corki.models.openai_compatible import OpenAICompatibleModel
from corki.models.responses import OpenAIResponsesModel
from corki.models.types import (
    ModelCompleted,
    ModelReasoningDelta,
    ModelRequest,
    ModelRetrying,
    ModelTextDelta,
)

__all__ = [
    "ApiMode",
    "ModelCompleted",
    "ModelError",
    "ModelErrorKind",
    "ModelPort",
    "ModelReasoningDelta",
    "ModelRequest",
    "ModelRetrying",
    "ModelTextDelta",
    "OpenAICompatibleModel",
    "OpenAIResponsesModel",
    "ProviderCapabilities",
    "ReasoningProtocol",
    "StructuredOutputProtocol",
    "resolve_capabilities",
]
