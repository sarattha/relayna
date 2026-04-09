from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator


class ServiceStatus(StrEnum):
    REGISTERED = "registered"
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


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
        return ServiceRecord(**self.model_dump(), status=ServiceStatus.REGISTERED, capabilities=None, last_seen_at=None)


class UpdateServiceRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    environment: str | None = None
    tags: list[str] | None = None
    auth_mode: str | None = None
    status: ServiceStatus | None = None

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


class RefreshBlockedResponse(BaseModel):
    detail: str
    blocked_by: str = "capability_discovery"


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


class CapabilityRefreshBlockedError(Exception):
    detail = "Capability refresh is blocked until feature 2 adds GET /relayna/capabilities."
    blocked_by = "capability_discovery"

    def to_response(self) -> RefreshBlockedResponse:
        return RefreshBlockedResponse(detail=self.detail, blocked_by=self.blocked_by)


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
    def __init__(self, *, store: ServiceRegistryStore) -> None:
        self._store = store

    async def create_service(self, request: CreateServiceRequest) -> ServiceRecord:
        return await self._store.create(request.to_record())

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
        return await self._store.update(normalized_service_id, updated)

    async def delete_service(self, service_id: str) -> None:
        normalized_service_id = service_id.strip()
        await self._store.delete(normalized_service_id)

    async def refresh_service(self, service_id: str) -> None:
        normalized_service_id = service_id.strip()
        await self.get_service(normalized_service_id)
        raise CapabilityRefreshBlockedError


def create_service_registry_router(
    *,
    service_registry: ServiceRegistryService,
    prefix: str = "/studio/services",
) -> APIRouter:
    router = APIRouter()

    @router.post(prefix, response_model=ServiceRecord, status_code=status.HTTP_201_CREATED)
    async def create_service(request: CreateServiceRequest) -> ServiceRecord:
        try:
            return await service_registry.create_service(request)
        except DuplicateServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get(prefix, response_model=ServiceListResponse)
    async def list_services() -> ServiceListResponse:
        services = await service_registry.list_services()
        return ServiceListResponse(count=len(services), services=services)

    @router.get(f"{prefix}" + "/{service_id}", response_model=ServiceRecord)
    async def get_service(service_id: str) -> ServiceRecord:
        try:
            return await service_registry.get_service(service_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch(f"{prefix}" + "/{service_id}", response_model=ServiceRecord)
    async def update_service(service_id: str, request: UpdateServiceRequest) -> ServiceRecord:
        try:
            return await service_registry.update_service(service_id, request)
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

    @router.post(f"{prefix}" + "/{service_id}/refresh", response_model=None)
    async def refresh_service(service_id: str) -> JSONResponse:
        try:
            await service_registry.refresh_service(service_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CapabilityRefreshBlockedError as exc:
            return JSONResponse(status_code=501, content=exc.to_response().model_dump(mode="json"))
        raise AssertionError("refresh_service should either raise or return a payload")

    return router


def service_record_from_mapping(data: Mapping[str, Any]) -> ServiceRecord:
    payload = json.loads(json.dumps(dict(data)))
    return ServiceRecord.model_validate(payload)


__all__ = [
    "CapabilityRefreshBlockedError",
    "CreateServiceRequest",
    "DuplicateServiceError",
    "RedisServiceRegistryStore",
    "RefreshBlockedResponse",
    "ServiceListResponse",
    "ServiceNotFoundError",
    "ServiceRecord",
    "ServiceRegistryService",
    "ServiceRegistryStore",
    "ServiceStatus",
    "UpdateServiceRequest",
    "create_service_registry_router",
    "normalize_base_url",
    "service_record_from_mapping",
]
