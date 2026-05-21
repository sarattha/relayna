from relayna.consumer._retry_decision import RetryDecisionAction, RetryDecisionContext, decide_static_retry
from relayna.consumer.context import FailureAction, RetryPolicy


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
        ),
        retry_policy=RetryPolicy(max_retries=3, delay_ms=1000),
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
        ),
        retry_policy=RetryPolicy(max_retries=3, delay_ms=1000),
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
        retry_policy=None,
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
        ),
        retry_policy=None,
        failure_action=FailureAction.REQUEUE,
    )

    assert decision.action is RetryDecisionAction.REQUEUE
    assert decision.retry_attempt == 0
    assert decision.max_retries is None
    assert decision.policy_name == "no_retry_policy"
