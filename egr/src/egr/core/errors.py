"""EGR error taxonomy."""


class EGRError(Exception):
    """Base class for all EGR errors."""


class WorkspaceNotFound(EGRError):
    """No egr workspace found (missing egr.yaml)."""


class ConfigError(EGRError):
    """Invalid configuration."""


class PolicyDenied(EGRError):
    def __init__(self, reason: str, rule_id: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.rule_id = rule_id


class ApprovalRequired(EGRError):
    def __init__(self, approval_id: str, reason: str = "", required_role: str | None = None):
        super().__init__(reason or f"approval {approval_id} required")
        self.approval_id = approval_id
        self.reason = reason
        self.required_role = required_role


class ToolError(EGRError):
    """A tool failed or refused to execute."""


class ToolNotFound(ToolError):
    pass


class ProviderError(EGRError):
    """Model provider failure."""


class NoProviderAvailable(ProviderError):
    pass


class SandboxViolation(ToolError):
    """A tool tried to escape its allowed roots."""


class ConditionError(EGRError):
    """A policy condition could not be evaluated (treated as deny)."""
