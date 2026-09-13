"""Expected unavailable-call outcomes, distinct from handler programming errors."""


class MCPAdmissionError(Exception):
    """A selected MCP handler no longer has authority to prepare its remote call."""


class MCPUnavailableError(KeyError, MCPAdmissionError):
    """Missing server/tool identity; preserve host callers' KeyError contract."""


class MCPDisabledError(ValueError, MCPAdmissionError):
    """Revoked tool policy; preserve host callers' ValueError contract."""
