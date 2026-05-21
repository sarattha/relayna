from relayna.policies import (
    RetryDecision,
    RetryDecisionAction,
    RetryDecisionContext,
    RuntimePolicyEngine,
    decide_static_retry,
)


def test_static_retry_decision_retries_below_max_attempts() -> None:
    decision = decide_static_retry(
        RetryDecisionContext(
            worker_type="task",
            queue_name="tasks.queue",
            retry_attempt=1,
            reason="handler_error",
            exception_type="RuntimeError",
            task_id="task-123",
            task_type="task.demo",
            max_retries=3,
        ),
    )

    assert decision.action is RetryDecisionAction.RETRY
    assert decision.retry_attempt == 2
    assert decision.max_retries == 3
    assert decision.reason == "handler_error"
    assert decision.policy_name == "static_retry_policy"


def test_static_retry_decision_dead_letters_at_max_attempts() -> None:
    decision = decide_static_retry(
        RetryDecisionContext(
            worker_type="workflow",
            queue_name="workflow.stage",
            retry_attempt=3,
            reason="stage_timeout",
            exception_type="TimeoutError",
            task_id="task-123",
            task_type="action.demo",
            workflow_stage="stage-a",
            max_retries=3,
        ),
    )

    assert decision.action is RetryDecisionAction.DEAD_LETTER
    assert decision.retry_attempt == 3
    assert decision.max_retries == 3
    assert decision.reason == "stage_timeout"
    assert decision.policy_name == "static_retry_policy"


def test_static_retry_decision_rejects_without_retry_policy_by_default() -> None:
    decision = decide_static_retry(
        RetryDecisionContext(
            worker_type="task",
            queue_name="tasks.queue",
            retry_attempt=0,
            reason="handler_error",
        ),
    )

    assert decision.action is RetryDecisionAction.REJECT
    assert decision.retry_attempt == 0
    assert decision.max_retries is None
    assert decision.policy_name == "no_retry_policy"


def test_static_retry_decision_preserves_requeue_failure_action_without_retry_policy() -> None:
    decision = decide_static_retry(
        RetryDecisionContext(
            worker_type="task",
            queue_name="tasks.queue",
            retry_attempt=0,
            reason="handler_error",
            failure_action="requeue",
        ),
    )

    assert decision.action is RetryDecisionAction.REQUEUE
    assert decision.retry_attempt == 0
    assert decision.max_retries is None
    assert decision.policy_name == "no_retry_policy"


class _AlwaysRejectPolicy:
    def decide_retry(self, context: RetryDecisionContext) -> RetryDecision:
        return RetryDecision(
            action=RetryDecisionAction.REJECT,
            retry_attempt=context.retry_attempt,
            max_retries=context.max_retries,
            reason="custom_reject",
            policy_name="always_reject",
        )


def test_runtime_policy_engine_delegates_to_custom_retry_policy() -> None:
    engine = RuntimePolicyEngine(retry_policy=_AlwaysRejectPolicy())

    decision = engine.decide_retry(
        RetryDecisionContext(
            worker_type="task",
            queue_name="tasks.queue",
            retry_attempt=1,
            reason="handler_error",
            max_retries=3,
        )
    )

    assert decision.action is RetryDecisionAction.REJECT
    assert decision.retry_attempt == 1
    assert decision.max_retries == 3
    assert decision.reason == "custom_reject"
    assert decision.policy_name == "always_reject"
