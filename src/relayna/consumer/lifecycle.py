from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from ..dlq import DLQRecorder
from ..observability import ObservationSink
from ..rabbitmq import RelaynaRabbitClient
from ..topology import RelaynaTopology
from .context import AggregationHandler, RetryPolicy, RetryStatusConfig
from .task_consumer import AggregationConsumer


class AggregationWorkerRuntime:
    """Owns one or more aggregation consumer loops outside the FastAPI lifecycle."""

    def __init__(
        self,
        *,
        handler: AggregationHandler,
        shard_groups: list[list[int]],
        topology: RelaynaTopology | None = None,
        rabbitmq: RelaynaRabbitClient | None = None,
        connection_name: str = "relayna-aggregation-runtime",
        consumer_name_prefix: str = "relayna-aggregation-worker",
        prefetch: int | None = None,
        consume_arguments: dict[str, object] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_statuses: RetryStatusConfig | None = None,
        consume_timeout_seconds: float | None = 1.0,
        observation_sink: ObservationSink | None = None,
        dlq_store: DLQRecorder | None = None,
    ) -> None:
        if rabbitmq is None and topology is None:
            raise ValueError("Pass rabbitmq=... or topology=... to AggregationWorkerRuntime.")
        self._owns_rabbitmq = rabbitmq is None
        if rabbitmq is not None:
            self._rabbitmq = rabbitmq
        else:
            if topology is None:
                raise ValueError("Pass rabbitmq=... or topology=... to AggregationWorkerRuntime.")
            self._rabbitmq = RelaynaRabbitClient(topology=topology, connection_name=connection_name)
        self._consumers = [
            AggregationConsumer(
                rabbitmq=self._rabbitmq,
                handler=handler,
                shards=shards,
                consumer_name=f"{consumer_name_prefix}-{index}",
                prefetch=prefetch,
                consume_arguments=consume_arguments,
                retry_policy=retry_policy,
                retry_statuses=retry_statuses,
                consume_timeout_seconds=consume_timeout_seconds,
                observation_sink=observation_sink,
                dlq_store=dlq_store,
            )
            for index, shards in enumerate(shard_groups)
        ]
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        await self._rabbitmq.initialize()
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(consumer.run_forever(), name=f"aggregation-{index}")
            for index, consumer in enumerate(self._consumers)
        ]

    async def stop(self) -> None:
        for consumer in self._consumers:
            consumer.stop()
        try:
            if self._tasks:
                await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=5.0)
        except TimeoutError:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            self._tasks = []
            if self._owns_rabbitmq:
                await self._rabbitmq.close()


@dataclass(slots=True)
class WorkerHeartbeat:
    worker_name: str
    running: bool
    last_heartbeat_at: datetime | None = None


async def stop_runtime(runtime: AggregationWorkerRuntime) -> None:
    await runtime.stop()


__all__ = ["AggregationWorkerRuntime", "WorkerHeartbeat", "stop_runtime"]
