"""Bounded availability gates for optional telemetry; no database resources."""

from __future__ import annotations

import asyncio
import random
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, TypedDict, TypeVar

from pydantic import BaseModel

_T = TypeVar("_T")
_Key = tuple[str, str]


def _is_query_rejection(status_code: int) -> bool:
    # Bad input, request size, URI size, and semantic query validation are
    # request-specific. Authentication, throttling, and server failures are not.
    return status_code in {400, 413, 414, 422}


def _provider_key(service_id: str, config: BaseModel) -> _Key:
    return service_id, config.model_dump_json()


@dataclass(slots=True)
class _Availability:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    healthy_until: float = 0
    retry_at: float = 0
    generation: int = 0
    active: int = 0
    waiters: int = 0
    checked_at: datetime | None = None
    last_successful_at: datetime | None = None
    failures: int = 0


class _Evidence(TypedDict, total=False):
    state: Literal["ok", "unknown", "unavailable"]
    checked_at: datetime | None
    last_successful_at: datetime | None
    detail: str


class _ProviderAvailability:
    _timeout = 10.0
    _cooldown = 30.0
    _freshness = 30.0
    _max_active = 2
    _max_waiting = 16
    _max_states = 128

    def __init__(self, label: str) -> None:
        self._label = label
        self._states: OrderedDict[_Key, _Availability] = OrderedDict()
        self._active = 0
        self._waiting = 0

    def _state(self, key: _Key, failure_type: type[Exception]) -> _Availability:
        if key in self._states:
            self._states.move_to_end(key)
            return self._states[key]
        if len(self._states) >= self._max_states:
            for candidate, state in self._states.items():
                if not state.active and not state.waiters and not state.lock.locked():
                    del self._states[candidate]
                    break
            else:
                raise failure_type(f"{self._label} availability checks are busy; retry shortly.")
        state = _Availability()
        self._states[key] = state
        return state

    def _paused(self, state: _Availability, failure_type: type[Exception]) -> Exception:
        remaining = max(1, int(state.retry_at - monotonic()) + 1)
        return failure_type(
            f"{self._label} is unavailable. Dependent requests are paused; retry in {remaining} seconds."
        )

    def check(self, key: _Key, failure_type: type[Exception]) -> None:
        state = self._states.get(key)
        if state is not None and state.retry_at > monotonic():
            raise self._paused(state, failure_type)

    async def run(self, key: _Key, operation: Callable[[], Awaitable[_T]], failure_type: type[Exception]) -> _T:
        state = self._state(key, failure_type)
        if state.retry_at > monotonic():
            raise self._paused(state, failure_type)
        if state.healthy_until <= monotonic():
            # One initial/recovery query establishes availability. Waiting
            # callers execute their own operation only after a successful probe.
            if state.lock.locked() or state.waiters:
                if self._waiting >= self._max_waiting:
                    raise failure_type(f"{self._label} availability checks are busy; retry shortly.")
                self._waiting += 1
                state.waiters += 1
                try:
                    try:
                        await asyncio.wait_for(state.lock.acquire(), timeout=self._timeout)
                    except TimeoutError as exc:
                        raise failure_type(
                            f"{self._label} availability check is still pending; retry shortly."
                        ) from exc
                finally:
                    self._waiting -= 1
                    state.waiters -= 1
            else:
                # An uncontended acquire does not yield. Pin the state before
                # another request can evict it from the bounded cache.
                await state.lock.acquire()
            try:
                if state.retry_at > monotonic():
                    raise self._paused(state, failure_type)
                if state.healthy_until <= monotonic():
                    return await self._perform(state, operation, failure_type)
            finally:
                state.lock.release()
        return await self._perform(state, operation, failure_type)

    async def _perform(
        self, state: _Availability, operation: Callable[[], Awaitable[_T]], failure_type: type[Exception]
    ) -> _T:
        if self._active >= self._max_active:
            raise failure_type(f"{self._label} requests are busy; retry shortly.")
        self._active += 1
        state.active += 1
        generation = state.generation
        try:
            try:
                async with asyncio.timeout(self._timeout):
                    result = await operation()
            except (failure_type, TimeoutError) as exc:
                # Providers map query rejections to their existing validation
                # exception type, which escapes without changing outage state.
                state.generation += 1
                state.failures += 1
                state.healthy_until = 0
                delay = min(300.0, self._cooldown * 2 ** min(state.failures - 1, 4))
                state.retry_at = monotonic() + min(300.0, delay * random.uniform(0.8, 1.2))
                state.checked_at = datetime.now(UTC)
                reason = str(exc) or f"{self._label} query exceeded its time budget."
                raise failure_type(f"{reason} {self._paused(state, failure_type)}") from exc
            state.checked_at = state.last_successful_at = datetime.now(UTC)
            # An older in-flight success cannot clear a newer outage decision.
            if generation == state.generation:
                state.failures = 0
                state.retry_at = 0
                state.healthy_until = monotonic() + self._freshness
            return result
        finally:
            state.active -= 1
            self._active -= 1

    def describe(self, key: _Key) -> _Evidence:
        state = self._states.get(key)
        if state is None:
            return {"state": "unknown", "detail": "Configured; availability has not been checked."}
        now = monotonic()
        return {
            "state": "unavailable" if state.retry_at > now else "ok" if state.healthy_until > now else "unknown",
            "checked_at": state.checked_at,
            "last_successful_at": state.last_successful_at,
            "detail": f"{self._label} requests are paused pending recovery."
            if state.retry_at > now
            else "A recent provider query succeeded; empty results also establish connectivity."
            if state.healthy_until > now
            else "Availability evidence expired; the next request will perform a recovery check.",
        }
