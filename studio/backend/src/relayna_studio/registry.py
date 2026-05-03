from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import ip_address, ip_network
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from relayna.api import CapabilityDocument, build_legacy_fallback_capability_document

if TYPE_CHECKING:
    from .health import StudioHealthRefreshService
    from .search import StudioSearchIndexer


class ServiceStatus(StrEnum):
    REGISTERED = "registered"
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    normalized = value.strip()
    return normalized or None


class LokiLogConfig(BaseModel):
    provider: Literal["loki"] = "loki"
    base_url: str
    tenant_id: str | None = None
    service_selector_labels: dict[str, str] = Field(default_factory=dict)
    source_label: str | None = None
    task_id_label: str | None = None
    correlation_id_label: str | None = None
    level_label: str | None = None
    task_match_mode: Literal["label", "contains", "regex"] = "label"
    task_match_template: str | None = None

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        return normalize_base_url(value)

    @field_validator(
        "tenant_id",
        "source_label",
        "task_id_label",
        "correlation_id_label",
        "level_label",
        "task_match_template",
        mode="before",
    )
    @classmethod
    def _normalize_optional_strings(cls, value: Any) -> str | None:
        return _normalize_optional_string(value)

    @field_validator("service_selector_labels", mode="before")
    @classmethod
    def _normalize_service_selector_labels(cls, value: Any) -> dict[str, str]:
        if value is None:
            raise ValueError("service_selector_labels must not be empty")
        if not isinstance(value, dict):
            raise ValueError("service_selector_labels must be an object")
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = _normalize_optional_string(raw_key)
            field_value = _normalize_optional_string(raw_value)
            if key is None or field_value is None:
                raise ValueError("service_selector_labels must contain non-empty string keys and values")
            normalized[key] = field_value
        if not normalized:
            raise ValueError("service_selector_labels must not be empty")
        return normalized


_UNSUPPORTED_METRIC_SELECTOR_LABELS = {"task_id", "correlation_id", "request_id", "worker_id"}


class PrometheusMetricsConfig(BaseModel):
    provider: Literal["prometheus"] = "prometheus"
    base_url: str
    namespace: str
    service_selector_labels: dict[str, str] = Field(default_factory=dict)
    runtime_service_label_value: str | None = None
    namespace_label: str = "namespace"
    pod_label: str = "pod"
    container_label: str = "container"
    step_seconds: int = Field(default=30, ge=5, le=3600)
    task_window_padding_seconds: int = Field(default=120, ge=0, le=3600)

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        return normalize_base_url(value)

    @field_validator("namespace", "namespace_label", "pod_label", "container_label", mode="before")
    @classmethod
    def _normalize_required_string(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        if normalized in _UNSUPPORTED_METRIC_SELECTOR_LABELS:
            raise ValueError(f"metrics_config must not use high-cardinality selector label '{normalized}'")
        return normalized

    @field_validator("runtime_service_label_value", mode="before")
    @classmethod
    def _normalize_runtime_service_label_value(cls, value: Any) -> str | None:
        return _normalize_optional_string(value)

    @field_validator("service_selector_labels", mode="before")
    @classmethod
    def _normalize_service_selector_labels(cls, value: Any) -> dict[str, str]:
        if value is None:
            raise ValueError("service_selector_labels must not be empty")
        if not isinstance(value, dict):
            raise ValueError("service_selector_labels must be an object")
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = _normalize_optional_string(raw_key)
            field_value = _normalize_optional_string(raw_value)
            if key is None or field_value is None:
                raise ValueError("service_selector_labels must contain non-empty string keys and values")
            if key in _UNSUPPORTED_METRIC_SELECTOR_LABELS:
                raise ValueError(f"metrics_config must not use high-cardinality selector label '{key}'")
            normalized[key] = field_value
        if not normalized:
            raise ValueError("service_selector_labels must not be empty")
        return normalized


class ServiceRecord(BaseModel):
    service_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    base_url: str
    environment: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    auth_mode: str = Field(min_length=1)
    status: ServiceStatus = ServiceStatus.REGISTERED
    capabilities: dict[str, Any] | None = None
    last_seen_at: datetime | None = None
    log_config: LokiLogConfig | None = None
    metrics_config: PrometheusMetricsConfig | None = None
    health: dict[str, Any] | None = None

    @field_validator("service_id", "name", "environment", "auth_mode", mode="before")
    @classmethod
    def _strip_required_strings(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("base_url must be a string")
        return normalize_base_url(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tags must be a list")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("tags must contain strings")
            stripped = item.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            normalized.append(stripped)
        return normalized


class CreateServiceRequest(BaseModel):
    service_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    base_url: str
    environment: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    auth_mode: str = Field(min_length=1)
    log_config: LokiLogConfig | None = None
    metrics_config: PrometheusMetricsConfig | None = None

    @field_validator("service_id", "name", "environment", "auth_mode", mode="before")
    @classmethod
    def _strip_required_strings(cls, value: Any) -> str:
        return ServiceRecord._strip_required_strings(value)

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        return ServiceRecord._normalize_base_url(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: Any) -> list[str]:
        return ServiceRecord._normalize_tags(value)

    def to_record(self) -> ServiceRecord:
        return ServiceRecord(
            **self.model_dump(),
            status=ServiceStatus.REGISTERED,
            capabilities=None,
            last_seen_at=None,
        )


class UpdateServiceRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    environment: str | None = None
    tags: list[str] | None = None
    auth_mode: str | None = None
    status: ServiceStatus | None = None
    log_config: LokiLogConfig | None = None
    metrics_config: PrometheusMetricsConfig | None = None

    @field_validator("name", "environment", "auth_mode", mode="before")
    @classmethod
    def _strip_optional_strings(cls, value: Any) -> str | None:
        if value is None:
            return None
        return ServiceRecord._strip_required_strings(value)

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str | None:
        if value is None:
            return None
        return ServiceRecord._normalize_base_url(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        return ServiceRecord._normalize_tags(value)

    @model_validator(mode="after")
    def _require_change(self) -> UpdateServiceRequest:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


class ServiceListResponse(BaseModel):
    count: int
    services: list[ServiceRecord] = Field(default_factory=list)


def normalize_base_url(url: str) -> str:
    stripped = url.strip()
    if not stripped:
        raise ValueError("base_url must not be empty")
    parts = urlsplit(stripped)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("base_url must use http or https")
    if not parts.netloc:
        raise ValueError("base_url must include a hostname")
    if parts.query or parts.fragment:
        raise ValueError("base_url must not include a query string or fragment")
    if parts.username or parts.password:
        raise ValueError("base_url must not include user info")
    hostname = parts.hostname
    if hostname is None:
        raise ValueError("base_url must include a hostname")
    normalized_host = hostname.lower()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    path = parts.path.rstrip("/")
    normalized = SplitResult(
        scheme=parts.scheme.lower(),
        netloc=netloc,
        path=path,
        query="",
        fragment="",
    )
    return urlunsplit(normalized)


class DuplicateServiceError(Exception):
    pass


class ServiceNotFoundError(Exception):
    pass


class CapabilityRefreshError(Exception):
    pass


class OutboundUrlPolicyError(ValueError):
    pass


class CapabilityFetcher(Protocol):
    async def fetch(self, base_url: str) -> CapabilityDocument: ...


def _default_async_client_factory(timeout_seconds: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout_seconds)


DEFAULT_CAPABILITY_REFRESH_ALLOWED_HOSTS = (
    ".svc.local",
    ".svc.cluster.local",
    ".cluster.local",
    ".example.com",
    ".example.net",
    ".example.org",
    ".example.test",
    ".invalid",
    ".test",
)


class StudioOutboundUrlPolicy:
    def __init__(
        self,
        *,
        allowed_hosts: Collection[str] | None = None,
        allowed_networks: Collection[str] | None = None,
    ) -> None:
        trusted_hosts = DEFAULT_CAPABILITY_REFRESH_ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts
        self._allowed_host_patterns = tuple(self._normalize_host_pattern(item) for item in trusted_hosts)
        self._allowed_networks = tuple(ip_network(item.strip(), strict=False) for item in (allowed_networks or ()))

    def validate_url(self, url: str, *, label: str = "outbound request target") -> None:
        hostname = urlsplit(url).hostname
        if hostname is None:
            raise OutboundUrlPolicyError(f"{label} '{url}' is missing a hostname.")

        normalized_host = hostname.lower()
        try:
            parsed_ip = ip_address(normalized_host)
        except ValueError:
            parsed_ip = None

        if parsed_ip is not None:
            if any(parsed_ip in network for network in self._allowed_networks):
                return
            raise OutboundUrlPolicyError(f"{label} '{url}' is not in the trusted host/network allowlist.")

        if self._is_allowed_hostname(normalized_host):
            return

        raise OutboundUrlPolicyError(f"{label} '{url}' is not in the trusted host/network allowlist.")

    def _is_allowed_hostname(self, hostname: str) -> bool:
        return any(self._matches_host_pattern(hostname, pattern) for pattern in self._allowed_host_patterns)

    @staticmethod
    def _normalize_host_pattern(pattern: str) -> str:
        normalized = pattern.strip().lower()
        if not normalized:
            raise ValueError("allowed_hosts entries must not be empty")
        if normalized.startswith("*."):
            return normalized[1:]
        return normalized

    @staticmethod
    def _matches_host_pattern(hostname: str, pattern: str) -> bool:
        if pattern.startswith("."):
            return hostname.endswith(pattern)
        return hostname == pattern


class HttpCapabilityFetcher:
    def __init__(
        self,
        *,
        capability_path: str = "/relayna/capabilities",
        timeout_seconds: float = 5.0,
        client_factory: Callable[[float], httpx.AsyncClient] = _default_async_client_factory,
        allowed_hosts: Collection[str] | None = None,
        allowed_networks: Collection[str] | None = None,
    ) -> None:
        normalized_path = capability_path.strip() or "/relayna/capabilities"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        self._capability_path = normalized_path
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory
        self._outbound_policy = StudioOutboundUrlPolicy(
            allowed_hosts=allowed_hosts,
            allowed_networks=allowed_networks,
        )

    async def fetch(self, base_url: str) -> CapabilityDocument:
        self._validate_base_url(base_url)
        url = f"{base_url.rstrip('/')}{self._capability_path}"
        try:
            async with self._client_factory(self._timeout_seconds) as client:
                response = await client.get(url)
        except httpx.TimeoutException as exc:
            raise CapabilityRefreshError(f"Capability refresh timed out for '{base_url}'.") from exc
        except httpx.HTTPError as exc:
            raise CapabilityRefreshError(f"Capability refresh failed for '{base_url}'.") from exc

        if response.status_code in {404, 405, 501}:
            return build_legacy_fallback_capability_document(capability_path=self._capability_path)
        if response.status_code != 200:
            raise CapabilityRefreshError(
                f"Capability endpoint for '{base_url}' returned unexpected status {response.status_code}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise CapabilityRefreshError(f"Capability endpoint for '{base_url}' returned invalid JSON.") from exc

        try:
            return CapabilityDocument.model_validate(payload)
        except ValidationError as exc:
            raise CapabilityRefreshError(
                f"Capability endpoint for '{base_url}' returned an invalid capability document."
            ) from exc

    def _validate_base_url(self, base_url: str) -> None:
        try:
            self._outbound_policy.validate_url(base_url, label="Capability refresh target")
        except OutboundUrlPolicyError as exc:
            raise CapabilityRefreshError(str(exc)) from exc


class ServiceRegistryStore(Protocol):
    async def create(self, record: ServiceRecord) -> ServiceRecord: ...

    async def list_records(self) -> list[ServiceRecord]: ...

    async def get(self, service_id: str) -> ServiceRecord | None: ...

    async def update(self, service_id: str, record: ServiceRecord) -> ServiceRecord: ...

    async def delete(self, service_id: str) -> None: ...


class RedisServiceRegistryStore:
    def __init__(self, redis: Any, *, prefix: str = "studio:services") -> None:
        self._redis = redis
        self._prefix = prefix

    async def create(self, record: ServiceRecord) -> ServiceRecord:
        await self._create_record(record)
        return record

    async def list_records(self) -> list[ServiceRecord]:
        service_ids = sorted(self._decode_members(await self._redis.smembers(self._all_key())))
        items: list[ServiceRecord] = []
        for service_id in service_ids:
            record = await self.get(service_id)
            if record is not None:
                items.append(record)
        items.sort(key=lambda item: (item.environment, item.name.lower(), item.service_id))
        return items

    async def get(self, service_id: str) -> ServiceRecord | None:
        raw = await self._redis.get(self._record_key(service_id))
        if raw is None:
            return None
        payload = raw.decode() if isinstance(raw, bytes) else raw
        return ServiceRecord.model_validate_json(payload)

    async def update(self, service_id: str, record: ServiceRecord) -> ServiceRecord:
        existing = await self.get(service_id)
        if existing is None:
            raise ServiceNotFoundError(f"Service '{service_id}' was not found.")

        conflict_service_id = await self._get_env_url_owner(record.environment, record.base_url)
        if conflict_service_id is not None and conflict_service_id != service_id:
            raise DuplicateServiceError(
                "A service is already registered for "
                f"environment '{record.environment}' and base_url '{record.base_url}'."
            )

        await self._write_record(record)
        previous_env_url_key = self._env_url_key(existing.environment, existing.base_url)
        next_env_url_key = self._env_url_key(record.environment, record.base_url)
        if previous_env_url_key != next_env_url_key:
            await self._redis.delete(previous_env_url_key)
        return record

    async def delete(self, service_id: str) -> None:
        existing = await self.get(service_id)
        if existing is None:
            raise ServiceNotFoundError(f"Service '{service_id}' was not found.")

        await self._redis.delete(self._record_key(service_id))
        await self._redis.delete(self._env_url_key(existing.environment, existing.base_url))
        await self._redis.srem(self._all_key(), service_id)

    async def _write_record(self, record: ServiceRecord) -> None:
        await self._redis.set(self._record_key(record.service_id), record.model_dump_json())
        await self._redis.sadd(self._all_key(), record.service_id)
        await self._redis.set(self._env_url_key(record.environment, record.base_url), record.service_id)

    async def _create_record(self, record: ServiceRecord) -> None:
        record_key = self._record_key(record.service_id)
        env_url_key = self._env_url_key(record.environment, record.base_url)
        record_reserved = await self._redis.set(record_key, record.model_dump_json(), nx=True)
        if not record_reserved:
            raise DuplicateServiceError(f"Service '{record.service_id}' is already registered.")

        env_url_reserved = await self._redis.set(env_url_key, record.service_id, nx=True)
        if not env_url_reserved:
            await self._redis.delete(record_key)
            raise DuplicateServiceError(
                "A service is already registered for "
                f"environment '{record.environment}' and base_url '{record.base_url}'."
            )

        try:
            await self._redis.sadd(self._all_key(), record.service_id)
        except Exception:
            await self._redis.delete(record_key, env_url_key)
            raise

    async def _get_env_url_owner(self, environment: str, normalized_base_url: str) -> str | None:
        raw = await self._redis.get(self._env_url_key(environment, normalized_base_url))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    def _record_key(self, service_id: str) -> str:
        return f"{self._prefix}:by-id:{service_id}"

    def _all_key(self) -> str:
        return f"{self._prefix}:all"

    def _env_url_key(self, environment: str, normalized_base_url: str) -> str:
        return f"{self._prefix}:by-env-url:{environment}:{normalized_base_url}"

    @staticmethod
    def _decode_members(values: set[Any]) -> set[str]:
        decoded: set[str] = set()
        for value in values:
            decoded.add(value.decode() if isinstance(value, bytes) else str(value))
        return decoded


class ServiceRegistryService:
    def __init__(
        self,
        *,
        store: ServiceRegistryStore,
        capability_fetcher: CapabilityFetcher | None = None,
        search_indexer: StudioSearchIndexer | None = None,
        outbound_policy: StudioOutboundUrlPolicy | None = None,
    ) -> None:
        self._store = store
        self._capability_fetcher = capability_fetcher or HttpCapabilityFetcher()
        self._search_indexer = search_indexer
        self._outbound_policy = outbound_policy or StudioOutboundUrlPolicy()

    def set_search_indexer(self, search_indexer: StudioSearchIndexer | None) -> None:
        self._search_indexer = search_indexer

    async def create_service(self, request: CreateServiceRequest) -> ServiceRecord:
        record = request.to_record()
        self._validate_record_outbound_urls(record)
        created = await self._store.create(record)
        if self._search_indexer is not None:
            await self._search_indexer.upsert_service_document(created)
        return created

    async def list_services(self) -> list[ServiceRecord]:
        return await self._store.list_records()

    async def get_service(self, service_id: str) -> ServiceRecord:
        normalized_service_id = service_id.strip()
        record = await self._store.get(normalized_service_id)
        if record is None:
            raise ServiceNotFoundError(f"Service '{service_id}' was not found.")
        return record

    async def update_service(self, service_id: str, request: UpdateServiceRequest) -> ServiceRecord:
        normalized_service_id = service_id.strip()
        existing = await self.get_service(normalized_service_id)
        changes = request.model_dump(exclude_unset=True)
        next_status = changes.pop("status", None)
        if next_status == ServiceStatus.HEALTHY:
            raise ValueError("Service status 'healthy' can only be set by refresh.")

        data = existing.model_dump()
        data.update(changes)
        if next_status is not None:
            data["status"] = next_status
        updated = ServiceRecord.model_validate(data)
        self._validate_record_outbound_urls(updated)
        saved = await self._store.update(normalized_service_id, updated)
        if self._search_indexer is not None:
            await self._search_indexer.upsert_service_document(saved)
        return saved

    def _validate_record_outbound_urls(self, record: ServiceRecord) -> None:
        self._outbound_policy.validate_url(record.base_url, label="Service base_url")
        if record.log_config is not None:
            self._outbound_policy.validate_url(record.log_config.base_url, label="Loki log_config.base_url")
        if record.metrics_config is not None:
            self._outbound_policy.validate_url(
                record.metrics_config.base_url,
                label="Prometheus metrics_config.base_url",
            )

    async def delete_service(self, service_id: str) -> None:
        normalized_service_id = service_id.strip()
        await self.get_service(normalized_service_id)
        await self._store.delete(normalized_service_id)
        if self._search_indexer is not None:
            await self._search_indexer.delete_task_documents_for_service(normalized_service_id)
            await self._search_indexer.delete_service_document(normalized_service_id)

    async def refresh_service(self, service_id: str) -> ServiceRecord:
        _, capability_document = await self.fetch_capability_document(service_id)
        return await self.store_capability_document(service_id, capability_document, mark_healthy=True)

    async def fetch_capability_document(self, service_id: str) -> tuple[ServiceRecord, CapabilityDocument]:
        normalized_service_id = service_id.strip()
        existing = await self.get_service(normalized_service_id)
        capability_document = await self._capability_fetcher.fetch(existing.base_url)
        return existing, capability_document

    async def store_capability_document(
        self,
        service_id: str,
        capability_document: CapabilityDocument,
        *,
        mark_healthy: bool,
    ) -> ServiceRecord:
        normalized_service_id = service_id.strip()
        existing = await self.get_service(normalized_service_id)
        refreshed = existing.model_copy(
            update={
                "capabilities": capability_document.model_dump(mode="json"),
                "last_seen_at": datetime.now(UTC),
                "status": (
                    ServiceStatus.DISABLED if existing.status == ServiceStatus.DISABLED else ServiceStatus.HEALTHY
                )
                if mark_healthy
                else existing.status,
            }
        )
        saved = await self._store.update(normalized_service_id, refreshed)
        if self._search_indexer is not None:
            await self._search_indexer.upsert_service_document(saved)
        return saved


def create_service_registry_router(
    *,
    service_registry: ServiceRegistryService,
    health_service: StudioHealthRefreshService | None = None,
    prefix: str = "/studio/services",
) -> APIRouter:
    router = APIRouter()

    async def attach_health(record: ServiceRecord) -> ServiceRecord:
        if health_service is None:
            return record
        return await health_service.build_service_record(record)

    @router.post(prefix, response_model=ServiceRecord, status_code=status.HTTP_201_CREATED)
    async def create_service(request: CreateServiceRequest) -> ServiceRecord:
        try:
            created = await service_registry.create_service(request)
            return await attach_health(created)
        except DuplicateServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(prefix, response_model=ServiceListResponse)
    async def list_services() -> ServiceListResponse:
        services = await service_registry.list_services()
        if health_service is not None:
            services = await health_service.build_service_records(services)
        return ServiceListResponse(count=len(services), services=services)

    @router.get(f"{prefix}" + "/{service_id}", response_model=ServiceRecord)
    async def get_service(service_id: str) -> ServiceRecord:
        try:
            record = await service_registry.get_service(service_id)
            return await attach_health(record)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch(f"{prefix}" + "/{service_id}", response_model=ServiceRecord)
    async def update_service(service_id: str, request: UpdateServiceRequest) -> ServiceRecord:
        try:
            updated = await service_registry.update_service(service_id, request)
            return await attach_health(updated)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DuplicateServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete(f"{prefix}" + "/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_service(service_id: str) -> Response:
        try:
            await service_registry.delete_service(service_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(f"{prefix}" + "/{service_id}/refresh", response_model=ServiceRecord)
    async def refresh_service(service_id: str) -> ServiceRecord:
        try:
            refreshed = await service_registry.refresh_service(service_id)
            if health_service is not None:
                await health_service.refresh_health(service_id, use_cached_capabilities=True)
                refreshed = await health_service.build_service_record(refreshed)
            return refreshed
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CapabilityRefreshError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router


def service_record_from_mapping(data: Mapping[str, Any]) -> ServiceRecord:
    payload = json.loads(json.dumps(dict(data)))
    return ServiceRecord.model_validate(payload)


__all__ = [
    "CapabilityFetcher",
    "CapabilityRefreshError",
    "CreateServiceRequest",
    "DuplicateServiceError",
    "HttpCapabilityFetcher",
    "LokiLogConfig",
    "OutboundUrlPolicyError",
    "PrometheusMetricsConfig",
    "RedisServiceRegistryStore",
    "ServiceListResponse",
    "ServiceNotFoundError",
    "ServiceRecord",
    "ServiceRegistryService",
    "ServiceRegistryStore",
    "ServiceStatus",
    "StudioOutboundUrlPolicy",
    "UpdateServiceRequest",
    "create_service_registry_router",
    "normalize_base_url",
    "service_record_from_mapping",
]
