from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TypedDict


class StudioAppKwargs(TypedDict):
    redis_url: str
    title: str
    app_state_key: str
    registry_prefix: str
    capability_refresh_allowed_hosts: tuple[str, ...] | None
    capability_refresh_allowed_networks: tuple[str, ...] | None
    federation_timeout_seconds: float
    event_store_prefix: str
    event_store_ttl_seconds: int | None
    event_history_maxlen: int
    push_ingest_enabled: bool
    pull_sync_interval_seconds: float | None
    health_store_prefix: str
    health_refresh_interval_seconds: float | None
    capability_stale_after_seconds: int
    observation_stale_after_seconds: int
    worker_heartbeat_stale_after_seconds: int
    task_search_index_prefix: str
    task_index_ttl_seconds: int
    retention_prune_interval_seconds: float | None


def _env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set.")
    return value


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped or default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value.strip())


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return float(value.strip())


def _env_optional_int(name: str, default: int | None) -> int | None:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip().lower()
    if stripped in {"", "none", "null", "off"}:
        return None
    return int(stripped)


def _env_optional_float(name: str, default: float | None) -> float | None:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip().lower()
    if stripped in {"", "none", "null", "off"}:
        return None
    return float(stripped)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip().lower()
    if not stripped:
        return default
    return stripped in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> tuple[str, ...] | None:
    value = os.getenv(name)
    if value is None:
        return None
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or None


@dataclass(slots=True, frozen=True)
class StudioBackendSettings:
    redis_url: str
    title: str = "Relayna Studio Backend"
    host: str = "0.0.0.0"
    port: int = 8000
    app_state_key: str = "studio"
    registry_prefix: str = "studio:services"
    capability_refresh_allowed_hosts: tuple[str, ...] | None = None
    capability_refresh_allowed_networks: tuple[str, ...] | None = None
    federation_timeout_seconds: float = 5.0
    event_store_prefix: str = "studio:events"
    event_store_ttl_seconds: int | None = 86400
    event_history_maxlen: int = 5000
    push_ingest_enabled: bool = False
    pull_sync_interval_seconds: float | None = 5.0
    health_store_prefix: str = "studio:health"
    health_refresh_interval_seconds: float | None = 60.0
    capability_stale_after_seconds: int = 180
    observation_stale_after_seconds: int = 300
    worker_heartbeat_stale_after_seconds: int = 90
    task_search_index_prefix: str = "studio:search"
    task_index_ttl_seconds: int = 86400
    retention_prune_interval_seconds: float | None = 60.0

    @classmethod
    def from_env(cls) -> StudioBackendSettings:
        return cls(
            redis_url=_env_required("RELAYNA_STUDIO_REDIS_URL"),
            title=_env_str("RELAYNA_STUDIO_TITLE", "Relayna Studio Backend"),
            host=_env_str("RELAYNA_STUDIO_HOST", "0.0.0.0"),
            port=_env_int("RELAYNA_STUDIO_PORT", 8000),
            app_state_key=_env_str("RELAYNA_STUDIO_APP_STATE_KEY", "studio"),
            registry_prefix=_env_str("RELAYNA_STUDIO_REGISTRY_PREFIX", "studio:services"),
            capability_refresh_allowed_hosts=_env_csv("RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS"),
            capability_refresh_allowed_networks=_env_csv("RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_NETWORKS"),
            federation_timeout_seconds=_env_float("RELAYNA_STUDIO_FEDERATION_TIMEOUT_SECONDS", 5.0),
            event_store_prefix=_env_str("RELAYNA_STUDIO_EVENT_STORE_PREFIX", "studio:events"),
            event_store_ttl_seconds=_env_optional_int("RELAYNA_STUDIO_EVENT_STORE_TTL_SECONDS", 86400),
            event_history_maxlen=_env_int("RELAYNA_STUDIO_EVENT_HISTORY_MAXLEN", 5000),
            push_ingest_enabled=_env_bool("RELAYNA_STUDIO_PUSH_INGEST_ENABLED", False),
            pull_sync_interval_seconds=_env_optional_float("RELAYNA_STUDIO_PULL_SYNC_INTERVAL_SECONDS", 5.0),
            health_store_prefix=_env_str("RELAYNA_STUDIO_HEALTH_STORE_PREFIX", "studio:health"),
            health_refresh_interval_seconds=_env_optional_float(
                "RELAYNA_STUDIO_HEALTH_REFRESH_INTERVAL_SECONDS",
                60.0,
            ),
            capability_stale_after_seconds=_env_int("RELAYNA_STUDIO_CAPABILITY_STALE_AFTER_SECONDS", 180),
            observation_stale_after_seconds=_env_int("RELAYNA_STUDIO_OBSERVATION_STALE_AFTER_SECONDS", 300),
            worker_heartbeat_stale_after_seconds=_env_int(
                "RELAYNA_STUDIO_WORKER_HEARTBEAT_STALE_AFTER_SECONDS",
                90,
            ),
            task_search_index_prefix=_env_str("RELAYNA_STUDIO_TASK_SEARCH_INDEX_PREFIX", "studio:search"),
            task_index_ttl_seconds=_env_int("RELAYNA_STUDIO_TASK_INDEX_TTL_SECONDS", 86400),
            retention_prune_interval_seconds=_env_optional_float(
                "RELAYNA_STUDIO_RETENTION_PRUNE_INTERVAL_SECONDS",
                60.0,
            ),
        )

    def to_app_kwargs(self) -> StudioAppKwargs:
        return {
            "redis_url": self.redis_url,
            "title": self.title,
            "app_state_key": self.app_state_key,
            "registry_prefix": self.registry_prefix,
            "capability_refresh_allowed_hosts": self.capability_refresh_allowed_hosts,
            "capability_refresh_allowed_networks": self.capability_refresh_allowed_networks,
            "federation_timeout_seconds": self.federation_timeout_seconds,
            "event_store_prefix": self.event_store_prefix,
            "event_store_ttl_seconds": self.event_store_ttl_seconds,
            "event_history_maxlen": self.event_history_maxlen,
            "push_ingest_enabled": self.push_ingest_enabled,
            "pull_sync_interval_seconds": self.pull_sync_interval_seconds,
            "health_store_prefix": self.health_store_prefix,
            "health_refresh_interval_seconds": self.health_refresh_interval_seconds,
            "capability_stale_after_seconds": self.capability_stale_after_seconds,
            "observation_stale_after_seconds": self.observation_stale_after_seconds,
            "worker_heartbeat_stale_after_seconds": self.worker_heartbeat_stale_after_seconds,
            "task_search_index_prefix": self.task_search_index_prefix,
            "task_index_ttl_seconds": self.task_index_ttl_seconds,
            "retention_prune_interval_seconds": self.retention_prune_interval_seconds,
        }


__all__ = ["StudioAppKwargs", "StudioBackendSettings"]
