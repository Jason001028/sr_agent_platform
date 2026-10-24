"""Stable scheduler control results, independent of model answer text."""

from enum import Enum


class RoundOutcome(str, Enum):
    CONTINUE = "CONTINUE"
    BUDGET_YIELD = "BUDGET_YIELD"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    COMPLETED_CANDIDATE = "COMPLETED_CANDIDATE"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    RETRYABLE_ERROR = "RETRYABLE_ERROR"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
