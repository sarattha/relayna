from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RelaynaObservation(Protocol):
    component: str
    timestamp: datetime


ObservationSink = Callable[[RelaynaObservation], Awaitable[None]]


async def emit_observation(sink: ObservationSink | None, event: RelaynaObservation) -> None:
    if sink is None:
        return
    try:
        await sink(event)
    except Exception:
        return


@dataclass(slots=True)
class SSEStreamStarted:
    task_id: str
    resume_requested: bool
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["sse"] = field(init=False, default="sse")


@dataclass(slots=True)
class SSEResumeRequested:
    task_id: str
    last_event_id: str
    history_match_found: bool
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["sse"] = field(init=False, default="sse")


@dataclass(slots=True)
class SSEHistoryReplayed:
    task_id: str
    replayed_count: int
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["sse"] = field(init=False, default="sse")


@dataclass(slots=True)
class SSEKeepaliveSent:
    task_id: str
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["sse"] = field(init=False, default="sse")


@dataclass(slots=True)
class SSELiveEventSent:
    task_id: str
    event_id: str | None
    status: str | None
    source: Literal["history", "pubsub"]
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["sse"] = field(init=False, default="sse")


@dataclass(slots=True)
class SSEMalformedPubsubPayload:
    task_id: str
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["sse"] = field(init=False, default="sse")


@dataclass(slots=True)
class SSEStreamEnded:
    task_id: str
    terminal_status: str | None
    sent_count: int
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["sse"] = field(init=False, default="sse")


@dataclass(slots=True)
class TaskConsumerStarted:
    consumer_name: str
    queue_name: str
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["consumer"] = field(init=False, default="consumer")


@dataclass(slots=True)
class TaskMessageReceived:
    consumer_name: str
    task_id: str | None
    delivery_tag: int | None
    redelivered: bool
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["consumer"] = field(init=False, default="consumer")


@dataclass(slots=True)
class TaskMessageAcked:
    consumer_name: str
    task_id: str
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["consumer"] = field(init=False, default="consumer")


@dataclass(slots=True)
class TaskMessageRejected:
    consumer_name: str
    task_id: str | None
    requeue: bool
    reason: str
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["consumer"] = field(init=False, default="consumer")


@dataclass(slots=True)
class TaskHandlerFailed:
    consumer_name: str
    task_id: str
    exception_type: str
    requeue: bool
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["consumer"] = field(init=False, default="consumer")


@dataclass(slots=True)
class TaskLifecycleStatusPublished:
    consumer_name: str
    task_id: str
    status: str
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["consumer"] = field(init=False, default="consumer")


@dataclass(slots=True)
class TaskConsumerLoopError:
    consumer_name: str
    exception_type: str
    retry_delay_seconds: float
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["consumer"] = field(init=False, default="consumer")


@dataclass(slots=True)
class StatusHubStarted:
    queue_name: str
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["status_hub"] = field(init=False, default="status_hub")


@dataclass(slots=True)
class StatusHubStoredEvent:
    task_id: str
    event_id: str | None
    status: str | None
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["status_hub"] = field(init=False, default="status_hub")


@dataclass(slots=True)
class StatusHubMalformedMessage:
    reason: str
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["status_hub"] = field(init=False, default="status_hub")


@dataclass(slots=True)
class StatusHubStoreWriteFailed:
    task_id: str
    exception_type: str
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["status_hub"] = field(init=False, default="status_hub")


@dataclass(slots=True)
class StatusHubLoopError:
    exception_type: str
    retry_delay_seconds: float
    timestamp: datetime = field(default_factory=_utcnow)
    component: Literal["status_hub"] = field(init=False, default="status_hub")
