from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .context import FailureAction, RetryPolicy


class RetryDecisionAction(StrEnum):
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"
    REJECT = "reject"
    REQUEUE = "requeue"


@dataclass(frozen=True, slots=True)
class RetryDecisionContext:
    worker_type: str
    queue_name: str
    retry_attempt: int
    reason: str
    exception_type: str | None = None
    task_id: str | None = None
    task_type: str | None = None
    workflow_stage: str | None = None
    headers: dict[str, Any] = field(default_factory=dict)
    pressure_signals: tuple[Any, ...] = ()
    lease_state: Any | None = None


@dataclass(frozen=True, slots=True)
class RetryDecision:
    action: RetryDecisionAction
    retry_attempt: int
    max_retries: int | None
    reason: str
    policy_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


def decide_static_retry(
    context: RetryDecisionContext,
    *,
    retry_policy: RetryPolicy | None,
    failure_action: FailureAction = FailureAction.REJECT,
) -> RetryDecision:
    if retry_policy is None:
        action = RetryDecisionAction.REQUEUE if failure_action is FailureAction.REQUEUE else RetryDecisionAction.REJECT
        return RetryDecision(
            action=action,
            retry_attempt=context.retry_attempt,
            max_retries=None,
            reason=context.reason,
            policy_name="no_retry_policy",
        )

    max_retries = retry_policy.max_retries
    if context.retry_attempt < max_retries:
        return RetryDecision(
            action=RetryDecisionAction.RETRY,
            retry_attempt=context.retry_attempt + 1,
            max_retries=max_retries,
            reason=context.reason,
            policy_name="static_retry_policy",
        )

    return RetryDecision(
        action=RetryDecisionAction.DEAD_LETTER,
        retry_attempt=context.retry_attempt,
        max_retries=max_retries,
        reason=context.reason,
        policy_name="static_retry_policy",
    )


__all__ = [
    "RetryDecision",
    "RetryDecisionAction",
    "RetryDecisionContext",
    "decide_static_retry",
]
