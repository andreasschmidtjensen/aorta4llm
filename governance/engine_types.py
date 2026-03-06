"""Shared data types for governance engines (Prolog and Python)."""

from dataclasses import dataclass, field


@dataclass
class PermissionResult:
    """Result of a permission check."""

    permitted: bool
    reason: str
    violation: str | None = None
    severity: str = "hard"  # "hard" (default) or "soft" (confirmation-required)


@dataclass
class NormChange:
    """A single norm state change detected by notify_action."""

    type: str  # "activated", "fulfilled", "violated"
    deontic: str
    objective: str
    deadline: str


@dataclass
class NotifyResult:
    """Result of a notify_action call."""

    norms_changed: list[NormChange] = field(default_factory=list)
