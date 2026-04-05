from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import ValidationError

from ..contracts import ContractAliasConfig, WorkflowEnvelope
from ..dlq import DLQRecorder
from ..observability import (
    ConsumerDeadLetterPublished,
    ConsumerRetryScheduled,
    ObservationSink,
    WorkflowMessageReceived,
    WorkflowStageAcked,
    WorkflowStageFailed,
    WorkflowStageStarted,
    emit_observation,
)
from ..rabbitmq import RelaynaRabbitClient, RetryInfrastructure
from ..storage import WorkflowContractStore
from ..topology import SharedStatusWorkflowTopology
from ..topology.workflow_contract import WorkflowContractError, validate_inbound_message_contract
from .context import (
    RetryPolicy,
    RetryStatusConfig,
    WorkflowContext,
    WorkflowHandler,
    _coerce_task_id,
    _failure_message,
    _message_headers,
    _normalize_payload,
    _persist_dlq_record,
    _retry_attempt,
    _retry_headers,
)


class WorkflowConsumer:
    def __init__(
        self,
        *,
        rabbitmq: RelaynaRabbitClient,
        handler: WorkflowHandler,
        stage: str | None = None,
        queue_name: str | None = None,
        consumer_name: str = "relayna-workflow-consumer",
        prefetch: int | None = None,
        consume_arguments: dict[str, Any] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_statuses: RetryStatusConfig | None = None,
        idle_retry_seconds: float = 2.0,
        consume_timeout_seconds: float | None = 1.0,
        observation_sink: ObservationSink | None = None,
        dlq_store: DLQRecorder | None = None,
        alias_config: ContractAliasConfig | None = None,
        contract_store: WorkflowContractStore | None = None,
    ) -> None:
        if stage is None and queue_name is None:
            raise ValueError("WorkflowConsumer requires stage=... or queue_name=...")
        self._rabbitmq = rabbitmq
        self._handler = handler
        self._stage = stage
        self._queue_name = queue_name
        self._consumer_name = consumer_name
        self._prefetch = prefetch
        self._consume_arguments = consume_arguments or {}
        self._retry_policy = retry_policy
        self._retry_statuses = retry_statuses or RetryStatusConfig()
        self._idle_retry_seconds = idle_retry_seconds
        self._consume_timeout_seconds = consume_timeout_seconds
        self._observation_sink = observation_sink
        self._dlq_store = dlq_store
        self._alias_config = alias_config or getattr(rabbitmq, "alias_config", None)
        self._contract_store = contract_store
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        queue_name: str | None = self._queue_name
        retry_infrastructure: RetryInfrastructure | None = None
        started = False
        stage = self._resolve_stage()
        topology = self._workflow_topology()
        stage_config = topology.workflow_stage(stage)
        effective_retry_policy = self._effective_retry_policy(stage)
        prefetch = self._prefetch or stage_config.max_inflight or topology.prefetch_count
        if stage_config.dedup_key_fields and self._contract_store is None:
            raise RuntimeError(f"Workflow stage '{stage}' requires contract_store for dedup enforcement")

        while not self._stop.is_set():
            channel = None
            try:
                if queue_name is None:
                    queue_name = await self._rabbitmq.ensure_workflow_queue(stage)
                if retry_infrastructure is None and effective_retry_policy is not None:
                    retry_infrastructure = await self._rabbitmq.ensure_retry_infrastructure(
                        source_queue_name=queue_name,
                        delay_ms=effective_retry_policy.delay_ms,
                        retry_queue_suffix=effective_retry_policy.retry_queue_suffix,
                        dead_letter_queue_suffix=effective_retry_policy.dead_letter_queue_suffix,
                    )
                if not started:
                    await emit_observation(
                        self._observation_sink,
                        WorkflowStageStarted(consumer_name=self._consumer_name, stage=stage, queue_name=queue_name),
                    )
                    started = True

                channel = await self._rabbitmq.acquire_channel(prefetch=prefetch)
                queue = await channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments=topology.workflow_queue_arguments(stage) or None,
                )
                async with queue.iterator(
                    arguments=self._consume_arguments or None, timeout=self._consume_timeout_seconds
                ) as iterator:
                    async for message in iterator:
                        acked = await self._handle_message(
                            message,
                            stage=stage,
                            source_queue_name=queue_name,
                            retry_infrastructure=retry_infrastructure,
                            retry_policy=effective_retry_policy,
                        )
                        if acked:
                            await message.ack()
                        if self._stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                if self._stop.is_set():
                    return
                await asyncio.sleep(0)
                continue
            except Exception as exc:
                if self._stop.is_set():
                    return
                await emit_observation(
                    self._observation_sink,
                    WorkflowStageFailed(
                        consumer_name=self._consumer_name,
                        queue_name=queue_name,
                        stage=stage,
                        routing_key=None,
                        task_id=None,
                        message_id=None,
                        origin_stage=None,
                        correlation_id=None,
                        exception_type=type(exc).__name__,
                        requeue=False,
                    ),
                )
                await asyncio.sleep(self._idle_retry_seconds)
            finally:
                if channel is not None:
                    try:
                        await channel.close()
                    except Exception:
                        pass

    async def _handle_message(
        self,
        message: Any,
        *,
        stage: str,
        source_queue_name: str,
        retry_infrastructure: RetryInfrastructure | None,
        retry_policy: RetryPolicy | None,
    ) -> bool:
        try:
            payload = json.loads(message.body.decode("utf-8", errors="replace"))
        except Exception:
            if retry_policy is None:
                await message.reject(requeue=False)
            else:
                await self._publish_dead_letter(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=None,
                    retry_attempt=_retry_attempt(message),
                    reason="malformed_json",
                    exception_type=None,
                    retry_policy=retry_policy,
                )
                return True
            return False

        normalized_payload = _normalize_payload(payload, alias_config=self._alias_config)
        try:
            workflow_message = WorkflowEnvelope.model_validate(normalized_payload)
        except ValidationError:
            if retry_policy is None:
                await message.reject(requeue=False)
            else:
                await self._publish_dead_letter(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=_coerce_task_id(normalized_payload),
                    retry_attempt=_retry_attempt(message),
                    reason="invalid_envelope",
                    exception_type="ValidationError",
                    retry_policy=retry_policy,
                )
                return True
            return False

        try:
            validate_inbound_message_contract(
                self._workflow_topology(),
                stage=stage,
                action=workflow_message.action,
                payload=workflow_message.payload,
            )
        except WorkflowContractError as exc:
            if retry_policy is None:
                await message.reject(requeue=False)
                return False
            await self._publish_dead_letter(
                message,
                retry_infrastructure=retry_infrastructure,
                source_queue_name=source_queue_name,
                task_id=workflow_message.task_id,
                retry_attempt=_retry_attempt(message),
                reason=exc.reason,
                exception_type=type(exc).__name__,
                retry_policy=retry_policy,
            )
            return True

        await emit_observation(
            self._observation_sink,
            WorkflowMessageReceived(
                consumer_name=self._consumer_name,
                queue_name=source_queue_name,
                stage=stage,
                routing_key=None,
                task_id=workflow_message.task_id,
                message_id=workflow_message.message_id,
                origin_stage=workflow_message.origin_stage,
                correlation_id=workflow_message.correlation_id or getattr(message, "correlation_id", None),
                delivery_tag=getattr(message, "delivery_tag", None),
                redelivered=bool(getattr(message, "redelivered", False)),
            ),
        )

        context = WorkflowContext(
            rabbitmq=self._rabbitmq,
            consumer_name=self._consumer_name,
            stage=stage,
            raw_payload=workflow_message.model_dump(mode="json", exclude_none=True),
            correlation_id=workflow_message.correlation_id or getattr(message, "correlation_id", None),
            delivery_tag=getattr(message, "delivery_tag", None),
            redelivered=bool(getattr(message, "redelivered", False)),
            _task_id=workflow_message.task_id,
            _message_id=workflow_message.message_id,
            origin_stage=workflow_message.origin_stage,
            retry_attempt=_retry_attempt(message),
            max_retries=retry_policy.max_retries if retry_policy is not None else None,
            source_queue_name=source_queue_name,
            headers=_message_headers(message),
            observation_sink=self._observation_sink,
        )

        dedup_acquired = False
        stage_config = self._workflow_topology().workflow_stage(stage)
        if stage_config.dedup_key_fields:
            if self._contract_store is None:
                raise RuntimeError(f"Workflow stage '{stage}' requires contract_store for dedup enforcement")
            dedup_acquired = await self._contract_store.acquire_dedup(
                stage=stage,
                task_id=workflow_message.task_id,
                action=workflow_message.action,
                payload=workflow_message.payload,
                dedup_key_fields=stage_config.dedup_key_fields,
            )
            if not dedup_acquired:
                if retry_policy is None:
                    await message.reject(requeue=False)
                    return False
                await self._publish_dead_letter(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=workflow_message.task_id,
                    retry_attempt=_retry_attempt(message),
                    reason="dedup_conflict",
                    exception_type="WorkflowContractError",
                    retry_policy=retry_policy,
                )
                return True
            try:
                await self._contract_store.mark_inflight(
                    stage=stage,
                    task_id=workflow_message.task_id,
                    action=workflow_message.action,
                    payload=workflow_message.payload,
                    dedup_key_fields=stage_config.dedup_key_fields,
                )
            except Exception:
                await self._contract_store.release_dedup(
                    stage=stage,
                    task_id=workflow_message.task_id,
                    action=workflow_message.action,
                    payload=workflow_message.payload,
                    dedup_key_fields=stage_config.dedup_key_fields,
                )
                raise

        try:
            timeout_seconds = stage_config.timeout_seconds
            if timeout_seconds is None:
                await self._handler(workflow_message, context)
            else:
                await asyncio.wait_for(self._handler(workflow_message, context), timeout=timeout_seconds)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            await emit_observation(
                self._observation_sink,
                WorkflowStageFailed(
                    consumer_name=self._consumer_name,
                    queue_name=source_queue_name,
                    stage=stage,
                    routing_key=None,
                    task_id=workflow_message.task_id,
                    message_id=workflow_message.message_id,
                    origin_stage=workflow_message.origin_stage,
                    correlation_id=context.correlation_id,
                    exception_type=type(exc).__name__,
                    requeue=False,
                ),
            )
            if retry_policy is None:
                await message.reject(requeue=False)
                return False
            max_retries = retry_policy.max_retries
            if context.retry_attempt < max_retries:
                next_attempt = context.retry_attempt + 1
                await self._publish_retry(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=workflow_message.task_id,
                    retry_attempt=next_attempt,
                    max_retries=max_retries,
                    reason="stage_timeout",
                    exception_type=type(exc).__name__,
                )
                await self._publish_retry_status(context, next_attempt=next_attempt, max_retries=max_retries)
                return True
            await self._publish_dead_letter(
                message,
                retry_infrastructure=retry_infrastructure,
                source_queue_name=source_queue_name,
                task_id=workflow_message.task_id,
                retry_attempt=context.retry_attempt,
                reason="stage_timeout",
                exception_type=type(exc).__name__,
                retry_policy=retry_policy,
            )
            await self._publish_dead_letter_status(context, exc)
            return True
        except Exception as exc:
            await emit_observation(
                self._observation_sink,
                WorkflowStageFailed(
                    consumer_name=self._consumer_name,
                    queue_name=source_queue_name,
                    stage=stage,
                    routing_key=None,
                    task_id=workflow_message.task_id,
                    message_id=workflow_message.message_id,
                    origin_stage=workflow_message.origin_stage,
                    correlation_id=context.correlation_id,
                    exception_type=type(exc).__name__,
                    requeue=False,
                ),
            )
            if retry_policy is None:
                await message.reject(requeue=False)
                return False

            max_retries = retry_policy.max_retries
            if context.retry_attempt < max_retries:
                next_attempt = context.retry_attempt + 1
                await self._publish_retry(
                    message,
                    retry_infrastructure=retry_infrastructure,
                    source_queue_name=source_queue_name,
                    task_id=workflow_message.task_id,
                    retry_attempt=next_attempt,
                    max_retries=max_retries,
                    reason="handler_error",
                    exception_type=type(exc).__name__,
                )
                await self._publish_retry_status(context, next_attempt=next_attempt, max_retries=max_retries)
                return True

            await self._publish_dead_letter(
                message,
                retry_infrastructure=retry_infrastructure,
                source_queue_name=source_queue_name,
                task_id=workflow_message.task_id,
                retry_attempt=context.retry_attempt,
                reason="handler_error",
                exception_type=type(exc).__name__,
                retry_policy=retry_policy,
            )
            await self._publish_dead_letter_status(context, exc)
            return True
        finally:
            if dedup_acquired and stage_config.dedup_key_fields and self._contract_store is not None:
                await self._contract_store.clear_inflight(
                    stage=stage,
                    task_id=workflow_message.task_id,
                    action=workflow_message.action,
                    payload=workflow_message.payload,
                    dedup_key_fields=stage_config.dedup_key_fields,
                )
                await self._contract_store.release_dedup(
                    stage=stage,
                    task_id=workflow_message.task_id,
                    action=workflow_message.action,
                    payload=workflow_message.payload,
                    dedup_key_fields=stage_config.dedup_key_fields,
                )

        await emit_observation(
            self._observation_sink,
            WorkflowStageAcked(
                consumer_name=self._consumer_name,
                queue_name=source_queue_name,
                stage=stage,
                routing_key=None,
                task_id=workflow_message.task_id,
                message_id=workflow_message.message_id,
                origin_stage=workflow_message.origin_stage,
                correlation_id=context.correlation_id,
            ),
        )
        return True

    async def _publish_retry_status(self, context: WorkflowContext, *, next_attempt: int, max_retries: int) -> None:
        if not self._retry_statuses.enabled:
            return
        try:
            await context.publish_status(
                status=self._retry_statuses.retrying_status,
                message=f"Task processing failed. Scheduling retry {next_attempt}/{max_retries}.",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _publish_dead_letter_status(self, context: WorkflowContext, exc: Exception) -> None:
        if not self._retry_statuses.enabled:
            return
        try:
            await context.publish_status(
                status=self._retry_statuses.dead_lettered_status,
                message=_failure_message(exc, include_error_message=self._retry_statuses.include_error_message),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _publish_retry(
        self,
        message: Any,
        *,
        retry_infrastructure: RetryInfrastructure | None,
        source_queue_name: str,
        task_id: str | None,
        retry_attempt: int,
        max_retries: int,
        reason: str,
        exception_type: str | None,
    ) -> None:
        if retry_infrastructure is None:
            raise RuntimeError("Retry infrastructure is not initialized")
        headers = _retry_headers(
            message,
            source_queue_name=source_queue_name,
            retry_attempt=retry_attempt,
            max_retries=max_retries,
            reason=reason,
            exception_type=exception_type,
        )
        await self._rabbitmq.publish_raw_to_queue(
            retry_infrastructure.retry_queue_name,
            message.body,
            correlation_id=getattr(message, "correlation_id", None),
            headers=headers,
            content_type=getattr(message, "content_type", "application/json"),
        )
        await emit_observation(
            self._observation_sink,
            ConsumerRetryScheduled(
                consumer_name=self._consumer_name,
                task_id=task_id,
                queue_name=retry_infrastructure.retry_queue_name,
                retry_attempt=retry_attempt,
                max_retries=max_retries,
                reason=reason,
            ),
        )

    async def _publish_dead_letter(
        self,
        message: Any,
        *,
        retry_infrastructure: RetryInfrastructure | None,
        source_queue_name: str,
        task_id: str | None,
        retry_attempt: int,
        reason: str,
        exception_type: str | None,
        retry_policy: RetryPolicy,
    ) -> None:
        if retry_infrastructure is None:
            raise RuntimeError("Retry infrastructure is not initialized")
        headers = _retry_headers(
            message,
            source_queue_name=source_queue_name,
            retry_attempt=retry_attempt,
            max_retries=retry_policy.max_retries,
            reason=reason,
            exception_type=exception_type,
        )
        await self._rabbitmq.publish_raw_to_queue(
            retry_infrastructure.dead_letter_queue_name,
            message.body,
            correlation_id=getattr(message, "correlation_id", None),
            headers=headers,
            content_type=getattr(message, "content_type", "application/json"),
        )
        await emit_observation(
            self._observation_sink,
            ConsumerDeadLetterPublished(
                consumer_name=self._consumer_name,
                task_id=task_id,
                queue_name=retry_infrastructure.dead_letter_queue_name,
                retry_attempt=retry_attempt,
                max_retries=retry_policy.max_retries,
                reason=reason,
            ),
        )
        await _persist_dlq_record(
            self._dlq_store,
            consumer_name=self._consumer_name,
            queue_name=retry_infrastructure.dead_letter_queue_name,
            source_queue_name=source_queue_name,
            retry_queue_name=retry_infrastructure.retry_queue_name,
            task_id=task_id,
            correlation_id=getattr(message, "correlation_id", None),
            reason=reason,
            exception_type=exception_type,
            retry_attempt=retry_attempt,
            max_retries=retry_policy.max_retries,
            headers=headers,
            content_type=getattr(message, "content_type", "application/json"),
            body=message.body,
            observation_sink=self._observation_sink,
        )

    def _resolve_stage(self) -> str:
        if self._stage is not None:
            return self._stage
        topology = self._workflow_topology()
        if self._queue_name is None:
            raise RuntimeError("WorkflowConsumer requires stage=... or queue_name=...")
        for stage in topology.workflow_stage_names():
            if topology.workflow_queue_name(stage) == self._queue_name:
                return stage
        raise KeyError(f"No workflow stage is bound to queue '{self._queue_name}'")

    def _effective_retry_policy(self, stage: str) -> RetryPolicy | None:
        stage_config = self._workflow_topology().workflow_stage(stage)
        if self._retry_policy is None and stage_config.max_retries is None and stage_config.retry_delay_ms is None:
            return None
        base = self._retry_policy or RetryPolicy()
        return RetryPolicy(
            max_retries=stage_config.max_retries if stage_config.max_retries is not None else base.max_retries,
            delay_ms=stage_config.retry_delay_ms if stage_config.retry_delay_ms is not None else base.delay_ms,
            retry_queue_suffix=base.retry_queue_suffix,
            dead_letter_queue_suffix=base.dead_letter_queue_suffix,
        )

    def _workflow_topology(self) -> SharedStatusWorkflowTopology:
        topology = self._rabbitmq.topology
        if not isinstance(topology, SharedStatusWorkflowTopology):
            raise RuntimeError("WorkflowConsumer requires SharedStatusWorkflowTopology")
        return topology


__all__ = ["WorkflowConsumer", "WorkflowHandler"]
