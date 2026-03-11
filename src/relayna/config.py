from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _pick(settings: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(settings, name):
            return getattr(settings, name)
    return default


@dataclass(slots=True)
class RelaynaTopologyConfig:
    rabbitmq_url: str
    tasks_exchange: str
    tasks_queue: str
    tasks_routing_key: str
    status_exchange: str
    status_queue: str
    dead_letter_exchange: str | None = None
    prefetch_count: int = 1
    tasks_message_ttl_ms: int | None = None
    status_use_streams: bool = True
    status_queue_ttl_ms: int | None = None
    status_stream_max_length_gb: int | None = None
    status_stream_max_segment_size_mb: int | None = None
    status_stream_initial_offset: str = "last"

    @classmethod
    def from_settings(cls, settings: Any) -> RelaynaTopologyConfig:
        return cls(
            rabbitmq_url=str(_pick(settings, "rabbitmq_url", "RABBITMQ_URL", default="")),
            tasks_exchange=str(_pick(settings, "tasks_exchange", "TASKS_EXCHANGE", default="tasks.exchange")),
            tasks_queue=str(_pick(settings, "tasks_queue", "BACKEND_TASK_QUEUE", default="tasks.queue")),
            tasks_routing_key=str(_pick(settings, "tasks_routing_key", "INGESTION_ROUTING_KEY", default="task.request")),
            status_exchange=str(_pick(settings, "status_exchange", "STATUS_EXCHANGE", default="status.exchange")),
            status_queue=str(_pick(settings, "status_queue", "DOCUMENT_STATUS_QUEUE", default="status.queue")),
            dead_letter_exchange=_pick(settings, "dead_letter_exchange", "DEAD_LETTER_EXCHANGE", default=None),
            prefetch_count=int(_pick(settings, "prefetch_count", "PREFETCH_COUNT", default=1)),
            tasks_message_ttl_ms=_pick(settings, "tasks_message_ttl_ms", "TASKS_MESSAGE_TTL_MS", default=None),
            status_use_streams=bool(_pick(settings, "status_use_streams", "STATUS_USE_STREAMS", default=True)),
            status_queue_ttl_ms=_pick(settings, "status_queue_ttl_ms", "STATUS_QUEUE_TTL_MS", default=None),
            status_stream_max_length_gb=_pick(settings, "status_stream_max_length_GB", "STATUS_STREAM_MAX_LENGTH_GB"),
            status_stream_max_segment_size_mb=_pick(
                settings, "status_stream_max_segment_size_MB", "STATUS_STREAM_MAX_SEGMENT_SIZE_MB"
            ),
            status_stream_initial_offset=str(_pick(settings, "status_stream_initial_offset", default="last")),
        )

    def connection_string(self, connection_name: str | None = None) -> str:
        if connection_name:
            separator = "&" if "?" in self.rabbitmq_url else "?"
            return f"{self.rabbitmq_url}{separator}name={connection_name}"
        return self.rabbitmq_url

    def task_queue_arguments(self) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if self.tasks_message_ttl_ms:
            args["x-message-ttl"] = int(self.tasks_message_ttl_ms)
        if self.dead_letter_exchange:
            args["x-dead-letter-exchange"] = self.dead_letter_exchange
        return args

    def status_queue_arguments(self) -> dict[str, Any]:
        if self.status_use_streams:
            args: dict[str, Any] = {"x-queue-type": "stream"}
            if self.status_stream_max_length_gb is not None:
                args["x-max-length-bytes"] = int(self.status_stream_max_length_gb) * 1024**3
            if self.status_stream_max_segment_size_mb is not None:
                args["x-stream-max-segment-size-bytes"] = int(self.status_stream_max_segment_size_mb) * 1024**2
            return args
        args: dict[str, Any] = {}
        if self.status_queue_ttl_ms:
            args["x-expires"] = int(self.status_queue_ttl_ms)
        return args

    def status_stream_consume_arguments(self) -> dict[str, Any]:
        if not self.status_use_streams:
            return {}
        return {"x-stream-offset": self.status_stream_initial_offset}
