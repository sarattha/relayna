from __future__ import annotations

from .. import policies as _policies
from ..policies import RetryDecision, RetryDecisionAction, RetryDecisionContext
from .context import FailureAction, RetryPolicy


def decide_static_retry(
    context: RetryDecisionContext,
    *,
    retry_policy: RetryPolicy | None,
    failure_action: FailureAction = FailureAction.REJECT,
) -> RetryDecision:
    return _policies.decide_static_retry(
        RetryDecisionContext(
            worker_type=context.worker_type,
            queue_name=context.queue_name,
            retry_attempt=context.retry_attempt,
            reason=context.reason,
            exception_type=context.exception_type,
            task_id=context.task_id,
            task_type=context.task_type,
            workflow_stage=context.workflow_stage,
            headers=dict(context.headers),
            pressure_signals=context.pressure_signals,
            lease_state=context.lease_state,
            max_retries=retry_policy.max_retries if retry_policy is not None else None,
            failure_action=failure_action.value,
        )
    )


__all__ = [
    "RetryDecision",
    "RetryDecisionAction",
    "RetryDecisionContext",
    "decide_static_retry",
]
