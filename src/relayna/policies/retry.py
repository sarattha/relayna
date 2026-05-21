from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


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
    max_retries: int | None = None
    failure_action: str = "reject"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    action: RetryDecisionAction
    retry_attempt: int
    max_retries: int | None
    reason: str
    policy_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


class RetryDecisionPolicy(Protocol):
    def decide_retry(self, context: RetryDecisionContext) -> RetryDecision: ...


@dataclass(frozen=True, slots=True)
class StaticRetryDecisionPolicy:
    policy_name: str = "static_retry_policy"

    def decide_retry(self, context: RetryDecisionContext) -> RetryDecision:
        if context.max_retries is None:
            action = (
                RetryDecisionAction.REQUEUE
                if str(context.failure_action).lower() == RetryDecisionAction.REQUEUE.value
                else RetryDecisionAction.REJECT
            )
            return RetryDecision(
                action=action,
                retry_attempt=context.retry_attempt,
                max_retries=None,
                reason=context.reason,
                policy_name="no_retry_policy",
            )

        if context.retry_attempt < context.max_retries:
            return RetryDecision(
                action=RetryDecisionAction.RETRY,
                retry_attempt=context.retry_attempt + 1,
                max_retries=context.max_retries,
                reason=context.reason,
                policy_name=self.policy_name,
            )

        return RetryDecision(
            action=RetryDecisionAction.DEAD_LETTER,
            retry_attempt=context.retry_attempt,
            max_retries=context.max_retries,
            reason=context.reason,
            policy_name=self.policy_name,
        )


@dataclass(frozen=True, slots=True)
class RuntimePolicyEngine:
    retry_policy: RetryDecisionPolicy = field(default_factory=StaticRetryDecisionPolicy)

    def decide_retry(self, context: RetryDecisionContext) -> RetryDecision:
        return self.retry_policy.decide_retry(context)


def decide_static_retry(context: RetryDecisionContext) -> RetryDecision:
    return StaticRetryDecisionPolicy().decide_retry(context)


__all__ = [
    "RetryDecision",
    "RetryDecisionAction",
    "RetryDecisionContext",
    "RetryDecisionPolicy",
    "RuntimePolicyEngine",
    "StaticRetryDecisionPolicy",
    "decide_static_retry",
]
