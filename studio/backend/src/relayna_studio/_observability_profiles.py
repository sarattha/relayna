"""Reusable, encrypted observability connections owned by the Studio control plane."""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert

from .audit_context import current_actor_user_id
from .database import StudioDatabase, _append_audit, operator_settings, services
from .registry import (
    LokiLogConfig,
    OutboundUrlPolicyError,
    PrometheusMetricsConfig,
    ServiceRecord,
    StudioOutboundUrlPolicy,
    TempoTraceConfig,
)

_PREFIX = "observability-profile:v1:"
_LOCK = 0x4F425350524F46
_CONFIGS = {"loki": "log_config", "prometheus": "metrics_config", "tempo": "trace_config"}
_MODELS = {"loki": LokiLogConfig, "prometheus": PrometheusMetricsConfig, "tempo": TempoTraceConfig}


def _url(value: str) -> str:
    parts = urlsplit(value)
    try:
        _ = parts.port
    except ValueError:
        raise ValueError("Invalid endpoint port.") from None
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or any(ord(c) < 33 for c in value)
    ):
        raise ValueError("Use an HTTP(S) URL without credentials, query parameters, or fragment.")
    return value.rstrip("/")


class _ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    environment: str = Field(min_length=1, max_length=128)
    provider: Literal["loki", "prometheus", "tempo"]
    base_url: str = Field(max_length=2048)
    tenant_id: str | None = Field(default=None, max_length=512)
    defaults: dict[str, Any] = Field(default_factory=dict)
    auth_mode: Literal["none", "bearer", "basic"] = "none"
    token: SecretStr | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_settings(self):
        self.base_url = _url(self.base_url)
        self.tenant_id = self.tenant_id or None
        if self.tenant_id and any(not 32 <= ord(c) <= 126 for c in self.tenant_id):
            raise ValueError("Invalid tenant ID.")
        model = _MODELS[self.provider]
        allowed = set(model.model_fields) - {
            "provider",
            "profile_id",
            "base_url",
            "tenant_id",
            "service_selector_labels",
            "runtime_service_label_value",
        }
        if set(self.defaults) - allowed:
            raise ValueError("Unsupported provider default fields.")
        sample: dict[str, Any] = {
            "base_url": self.base_url,
            "service_selector_labels": {"app": "example"},
            "namespace": "default",
        }
        sample.update(self.defaults)
        model.model_validate(sample)
        if self.defaults.get("public_base_url"):
            _url(self.defaults["public_base_url"])
        return self


def _cipher() -> Fernet:
    try:
        return Fernet(os.getenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY", "").encode())
    except ValueError:
        raise HTTPException(503, "Profile credentials require RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY.") from None


def _check_binding(profile: dict[str, Any], record: ServiceRecord, provider: str) -> None:
    if profile["provider"] != provider or profile["environment"] != record.environment:
        raise HTTPException(422, "The observability profile must match the service provider and environment.")


async def _validate_profile_bindings(session: Any, record: ServiceRecord) -> ServiceRecord:
    """Called inside the registry write transaction, sharing the deletion lock."""
    bound = [
        (provider, field, getattr(record, field))
        for provider, field in _CONFIGS.items()
        if getattr(getattr(record, field), "profile_id", None)
    ]
    if not bound:
        return record
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK})
    changes = {}
    for provider, field, config in bound:
        profile = await session.scalar(
            select(operator_settings.c.value).where(operator_settings.c.setting_key == _PREFIX + config.profile_id)
        )
        if not profile:
            raise HTTPException(422, "The selected observability profile no longer exists.")
        _check_binding(profile, record, provider)
        changes[field] = _effective(config, profile)
    return record.model_copy(update=changes)


def _effective(config: Any, profile: dict[str, Any]) -> Any:
    changes: dict[str, Any] = {"base_url": profile["base_url"]}
    if hasattr(config, "tenant_id"):
        changes["tenant_id"] = profile.get("tenant_id")
    return config.model_copy(update=changes)


class _ProfileStore:
    def __init__(self, database: StudioDatabase | None, policy: StudioOutboundUrlPolicy):
        self.database = database
        self.policy = policy

    def _database(self) -> StudioDatabase:
        if self.database is None:
            raise HTTPException(503, "Observability profiles require Studio PostgreSQL storage.")
        return self.database

    async def current(self, profile_id: str) -> dict[str, Any]:
        async with self._database().sessions() as session:
            value = await session.scalar(
                select(operator_settings.c.value).where(operator_settings.c.setting_key == _PREFIX + profile_id)
            )
        if not value:
            raise HTTPException(404, "Observability profile not found.")
        return value

    @staticmethod
    def public(profile: dict[str, Any], used_by: list[str] | None = None) -> dict[str, Any]:
        return {
            key: profile[key]
            for key in (
                "id",
                "name",
                "environment",
                "provider",
                "base_url",
                "tenant_id",
                "defaults",
                "auth_mode",
                "revision",
                "updated_at",
            )
        } | {"credential_configured": bool(profile.get("credential_cipher")), "used_by": used_by or []}

    async def list_profiles(self) -> list[dict[str, Any]]:
        async with self._database().sessions() as session:
            profiles = list(
                await session.scalars(
                    select(operator_settings.c.value).where(operator_settings.c.setting_key.startswith(_PREFIX))
                )
            )
            records = (
                (
                    await session.execute(
                        select(
                            services.c.service_id,
                            services.c.log_config,
                            services.c.metrics_config,
                            services.c.trace_config,
                        ).where(services.c.deleted_at.is_(None))
                    )
                )
                .mappings()
                .all()
            )
        return [
            self.public(
                p,
                sorted(
                    r["service_id"]
                    for r in records
                    if any((r[field] or {}).get("profile_id") == p["id"] for field in _CONFIGS.values())
                ),
            )
            for p in sorted(profiles, key=lambda p: (p["environment"], p["provider"], p["name"].casefold()))
        ]

    def _credentials(self, profile: dict[str, Any]) -> dict[str, str]:
        if profile["auth_mode"] == "none":
            return {}
        try:
            return json.loads(_cipher().decrypt(profile["credential_cipher"].encode()))
        except (InvalidToken, KeyError, ValueError):
            raise HTTPException(
                503, "The profile credential cannot be decrypted. Restore the settings key or replace the credential."
            ) from None

    async def save(self, payload: _ProfileInput, profile_id: str | None = None) -> dict[str, Any]:
        try:
            self.policy.validate_url(payload.base_url, label="Observability profile endpoint")
        except ValueError:
            raise HTTPException(422, "The profile endpoint is outside the backend outbound allowlist.") from None
        async with self._database().transaction() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK})
            previous = (
                await session.scalar(
                    select(operator_settings.c.value).where(operator_settings.c.setting_key == _PREFIX + profile_id)
                )
                if profile_id
                else None
            )
            if profile_id and previous is None:
                raise HTTPException(404, "Observability profile not found.")
            if previous:
                if payload.revision != previous["revision"]:
                    raise HTTPException(409, "This profile changed. Reload it before saving.")
                if (payload.provider, payload.environment) != (previous["provider"], previous["environment"]):
                    raise HTTPException(
                        422, "Profile provider and environment cannot change. Create another profile instead."
                    )
            others = await session.scalars(
                select(operator_settings.c.value).where(operator_settings.c.setting_key.startswith(_PREFIX))
            )
            if any(
                p["id"] != profile_id
                and (p["environment"], p["provider"], p["name"].casefold())
                == (payload.environment, payload.provider, payload.name.casefold())
                for p in others
            ):
                raise HTTPException(409, "A profile with this name already exists for this environment and provider.")
            credentials = {
                key: value.get_secret_value()
                for key in ("token", "username", "password")
                if (value := getattr(payload, key)) is not None
            }
            required = {"bearer": {"token"}, "basic": {"username", "password"}, "none": set()}[payload.auth_mode]
            if credentials and (
                set(credentials) != required
                or any(not v or len(v) > 4096 or any(ord(c) < 32 for c in v) for v in credentials.values())
            ):
                raise HTTPException(422, "Supply complete, valid credentials for the selected authentication mode.")
            if payload.auth_mode == "bearer" and any(ord(c) > 126 for c in credentials.get("token", "")):
                raise HTTPException(422, "Bearer tokens must contain printable ASCII characters.")
            if payload.auth_mode == "basic" and ":" in credentials.get("username", ""):
                raise HTTPException(422, "Basic authentication usernames cannot contain a colon.")
            cipher = None
            if required:
                if credentials:
                    cipher = _cipher().encrypt(json.dumps(credentials).encode()).decode()
                elif (
                    previous
                    and previous["auth_mode"] == payload.auth_mode
                    and previous["base_url"] == payload.base_url
                    and previous.get("tenant_id") == payload.tenant_id
                ):
                    cipher = previous.get("credential_cipher")
                if not cipher:
                    raise HTTPException(
                        422, "Supply new credentials when changing the endpoint, tenant, or authentication mode."
                    )
            profile = payload.model_dump(exclude={"token", "username", "password"}) | {
                "id": profile_id or uuid4().hex,
                "revision": (previous["revision"] if previous else 0) + 1,
                "credential_cipher": cipher,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            await session.execute(
                insert(operator_settings)
                .values(setting_key=_PREFIX + profile["id"], value=profile, updated_by=current_actor_user_id())
                .on_conflict_do_update(
                    index_elements=[operator_settings.c.setting_key],
                    set_={"value": profile, "updated_at": datetime.now(UTC), "updated_by": current_actor_user_id()},
                )
            )
            await _append_audit(
                session,
                action="observability_profile.update" if previous else "observability_profile.create",
                target_type="operator_setting",
                target_id=profile["id"],
                details={"provider": profile["provider"], "revision": profile["revision"]},
            )
        return self.public(profile)

    async def remove(self, profile_id: str, revision: int) -> None:
        async with self._database().transaction() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK})
            profile = await session.scalar(
                select(operator_settings.c.value).where(operator_settings.c.setting_key == _PREFIX + profile_id)
            )
            if profile is None:
                raise HTTPException(404, "Observability profile not found.")
            if revision != profile["revision"]:
                raise HTTPException(409, "This profile changed. Reload it before deleting.")
            configs = (
                await session.execute(
                    select(services.c.log_config, services.c.metrics_config, services.c.trace_config).where(
                        services.c.deleted_at.is_(None)
                    )
                )
            ).mappings()
            if any(
                (row[field] or {}).get("profile_id") == profile_id for row in configs for field in _CONFIGS.values()
            ):
                raise HTTPException(409, "This profile is used by registered services. Detach those services first.")
            await session.execute(
                delete(operator_settings).where(operator_settings.c.setting_key == _PREFIX + profile_id)
            )
            await _append_audit(
                session, action="observability_profile.delete", target_type="operator_setting", target_id=profile_id
            )

    async def resolve(self, config: Any, environment: str, *, credentials: bool = True) -> tuple[Any, dict[str, str]]:
        headers = {"Accept": "application/json"}
        if config.profile_id:
            try:
                profile = await self.current(config.profile_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                raise HTTPException(503, "The service's observability profile is missing.") from None
            if profile["environment"] != environment or profile["provider"] != config.provider:
                raise HTTPException(503, "The service's observability profile has an incompatible scope.")
            self.policy.validate_url(profile["base_url"], label="Observability profile endpoint")
            config = _effective(config, profile)
            if credentials:
                secret = self._credentials(profile)
                if profile["auth_mode"] == "bearer":
                    headers["Authorization"] = "Bearer " + secret["token"]
                elif profile["auth_mode"] == "basic":
                    headers["Authorization"] = (
                        "Basic " + base64.b64encode((secret["username"] + ":" + secret["password"]).encode()).decode()
                    )
        if getattr(config, "tenant_id", None):
            headers["X-Scope-OrgID"] = config.tenant_id
        return config, headers

    async def record(self, record: ServiceRecord, *, strict: bool = False) -> ServiceRecord:
        changes = {}
        for field in _CONFIGS.values():
            config = getattr(record, field)
            if config is not None and config.profile_id:
                try:
                    changes[field], _ = await self.resolve(config, record.environment, credentials=False)
                except (HTTPException, OutboundUrlPolicyError):
                    # Keep damaged bindings visible/editable in the registry. The
                    # provider resolves strictly before every authenticated request.
                    if strict:
                        raise
        return record.model_copy(update=changes) if changes else record

    async def test(self, profile_id: str, client: httpx.AsyncClient) -> dict[str, Any]:
        profile = await self.current(profile_id)
        model = _MODELS[profile["provider"]]
        config = model.model_validate(
            {
                "profile_id": profile_id,
                "base_url": profile["base_url"],
                "namespace": "default",
                "service_selector_labels": {"app": "profile-test"},
            }
        )
        config, headers = await self.resolve(config, profile["environment"])
        paths = {
            "loki": ("/loki/api/v1/labels", {}),
            "prometheus": ("/api/v1/query", {"query": "vector(1)"}),
            "tempo": ("/api/search/tags", {}),
        }
        path, params = paths[profile["provider"]]
        try:
            response = await client.get(
                config.base_url + path, params=params, headers=headers, follow_redirects=False, timeout=10
            )
        except httpx.HTTPError:
            return {"ok": False, "state": "unreachable", "detail": "Unable to reach the provider."}
        if response.status_code in {401, 403}:
            return {
                "ok": False,
                "state": "unauthorized",
                "detail": "The provider rejected the configured credentials or permissions.",
            }
        if response.status_code != 200:
            return {"ok": False, "state": "provider_error", "detail": f"Provider returned HTTP {response.status_code}."}
        try:
            payload = response.json()
        except ValueError:
            payload = None
        valid = isinstance(payload, dict) and (
            payload.get("status") == "success"
            if profile["provider"] != "tempo"
            else isinstance(payload.get("tagNames"), list) or isinstance(payload.get("scopes"), list)
        )
        return {
            "ok": valid,
            "state": "connected" if valid else "provider_error",
            "detail": "Connection verified." if valid else "Provider returned an unexpected payload.",
        }


def _create_profile_router(store: _ProfileStore, client: httpx.AsyncClient) -> APIRouter:
    router = APIRouter(prefix="/studio/admin/observability-profiles")

    async def parse(request: Request) -> _ProfileInput:
        try:
            return _ProfileInput.model_validate(await request.json())
        except (ValidationError, ValueError, RequestValidationError):
            raise HTTPException(
                422, "Invalid profile settings. Check the name, environment, endpoint, and provider defaults."
            ) from None

    @router.get("")
    async def list_profiles(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"profiles": await store.list_profiles()}

    @router.post("", status_code=201)
    async def create_profile(request: Request):
        return await store.save(await parse(request))

    @router.get("/{profile_id}")
    async def get_profile(profile_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return store.public(await store.current(profile_id))

    @router.put("/{profile_id}")
    async def update_profile(profile_id: str, request: Request):
        return await store.save(await parse(request), profile_id)

    @router.delete("/{profile_id}", status_code=204)
    async def delete_profile(profile_id: str, revision: int):
        await store.remove(profile_id, revision)
        return Response(status_code=204)

    @router.post("/{profile_id}/test")
    async def test_profile(profile_id: str):
        return await store.test(profile_id, client)

    return router
