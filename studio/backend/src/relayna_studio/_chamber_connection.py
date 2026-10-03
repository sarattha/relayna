"""Encrypted operator connection settings and bounded Chamber transport."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .audit_context import current_actor_user_id
from .database import StudioDatabase, _append_audit, operator_settings
from .registry import OutboundUrlPolicyError, StudioOutboundUrlPolicy


class _ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="ui", pattern="^(ui|deployment|disabled)$")
    url: str = Field(default="", max_length=2048)
    token: SecretStr | None = None


def _origin(value: str) -> str:
    parts = urlsplit(value.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
        or any(ord(c) < 33 for c in value.strip())
    ):
        raise HTTPException(422, "Use an HTTP(S) Chamber origin without a path, credentials, query or fragment.")
    try:
        _ = parts.port
    except ValueError as exc:
        raise HTTPException(422, "The Chamber port is invalid.") from exc
    return value.strip().rstrip("/")


class _ConnectionStore:
    KEY = "ampule_chamber_connection"

    def __init__(self, database: StudioDatabase | None, url: str, token: str, policy: StudioOutboundUrlPolicy):
        self.database = database
        self.deployment_url = url
        self.deployment_token = token
        self.policy = policy

    def cipher(self) -> Fernet:
        try:
            return Fernet(os.getenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY", "").encode())
        except ValueError as exc:
            raise HTTPException(
                503, "UI connection storage needs RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY in a backend Secret."
            ) from exc

    def can_store(self) -> bool:
        if self.database is None:
            return False
        try:
            self.cipher()
            return True
        except HTTPException:
            return False

    async def current(self) -> dict[str, Any]:
        if self.database is not None:
            async with self.database.sessions() as session:
                value = await session.scalar(
                    select(operator_settings.c.value).where(operator_settings.c.setting_key == self.KEY)
                )
            if isinstance(value, dict) and value.get("source") != "deployment":
                return value
        return {
            "source": "deployment",
            "url": self.deployment_url,
            "id": sha256(self.deployment_url.encode()).hexdigest()[:24],
        }

    def token(self, connection: dict[str, Any]) -> str:
        if connection.get("source") == "disabled":
            return ""
        if connection.get("source") == "deployment":
            if connection.get("url") != self.deployment_url:
                raise HTTPException(
                    409,
                    "This run uses a previous deployment connection. Restore that endpoint to inspect or cancel it.",
                )
            return self.deployment_token
        try:
            return self.cipher().decrypt(connection["token_cipher"].encode()).decode()
        except (InvalidToken, KeyError) as exc:
            raise HTTPException(
                503,
                (
                    "The stored Chamber credential cannot be decrypted. "
                    "Restore its encryption key or save a new connection."
                ),
            ) from exc

    def public(self, connection: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": connection.get("id"),
            "source": connection["source"],
            "url": connection.get("url", ""),
            "token_configured": bool(
                connection.get("token_cipher") or (connection["source"] == "deployment" and self.deployment_token)
            ),
            "ui_settings_available": self.can_store(),
            "updated_at": connection.get("updated_at"),
            "status": "unchecked" if connection.get("url") else "not_configured",
        }

    async def candidate(self, payload: _ConnectionUpdate) -> dict[str, Any]:
        if payload.mode == "disabled":
            return {"source": "disabled", "url": "", "id": uuid4().hex}
        if payload.mode == "deployment":
            return {
                "source": "deployment",
                "url": self.deployment_url,
                "id": sha256(self.deployment_url.encode()).hexdigest()[:24],
            }
        url = _origin(payload.url)
        try:
            self.policy.validate_url(url, label="Chamber connection")
        except OutboundUrlPolicyError as exc:
            raise HTTPException(
                422,
                (
                    "This Chamber host is outside the backend outbound allowlist. "
                    "Add the internal host or network in deployment settings."
                ),
            ) from exc
        token = payload.token.get_secret_value().strip() if payload.token else ""
        current = await self.current()
        if not token:
            if current.get("url") != url:
                raise HTTPException(422, "Supply a new credential when changing the Chamber endpoint.")
            token = self.token(current)
        if not token or len(token) > 4096 or any(c in token for c in "\r\n"):
            raise HTTPException(422, "Supply a valid Chamber operator or integration token.")
        return {
            "source": "ui",
            "url": url,
            "token_cipher": self.cipher().encrypt(token.encode()).decode(),
            "id": uuid4().hex,
        }

    async def update(self, payload: _ConnectionUpdate) -> dict[str, Any]:
        if self.database is None:
            raise HTTPException(503, "UI connection settings require Studio PostgreSQL storage.")
        connection = await self.candidate(payload)
        connection["updated_at"] = datetime.now(UTC).isoformat()
        async with self.database.transaction() as session:
            await session.execute(
                insert(operator_settings)
                .values(setting_key=self.KEY, value=connection, updated_by=current_actor_user_id())
                .on_conflict_do_update(
                    index_elements=[operator_settings.c.setting_key],
                    set_={"value": connection, "updated_at": datetime.now(UTC), "updated_by": current_actor_user_id()},
                )
            )
            await _append_audit(
                session,
                action="settings.chamber.update",
                target_type="operator_setting",
                target_id=self.KEY,
                details={"source": connection["source"], "connection_id": connection["id"]},
            )
        return self.public(connection)


def _redact(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _environment_references(key, item)
            if key in {"secretEnv", "headersFromEnv"}
            else "[redacted]"
            if _secret_key(key)
            else _redact(item)
            for key, item in value.items()
            if key not in {"config_path", "run_dir"}
        }
    return value


def _environment_references(key: str, value: Any) -> Any:
    """Schema-declared environment names are references, never credential values."""

    def name(item: Any) -> str:
        return item if isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) else "[redacted]"

    if key == "secretEnv" and isinstance(value, list):
        return [name(item) for item in value]
    if key == "headersFromEnv" and isinstance(value, dict):
        return {header: name(item) for header, item in value.items()}
    return "[redacted]"


def _secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return (
        normalized in {"secretenv", "secret", "tokencipher"}
        or normalized.endswith(("authorization", "password", "apikey", "cookie", "token"))
        and normalized != "pathtoken"
    )


def _validation_detail(detail: Any, payload: Any, credential: str) -> str:
    """Keep field/reason diagnostics, excluding Pydantic input and submitted secrets."""
    if isinstance(detail, list):
        detail = "; ".join(
            f"{'.'.join(str(v) for v in item.get('loc', []))}: {item.get('msg', 'Invalid value')}"
            for item in detail[:8]
            if isinstance(item, dict)
        )
    if not isinstance(detail, str):
        return ""
    secrets = [credential]

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if _secret_key(key) and isinstance(item, str):
                    secrets.append(item)
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "[redacted]")
    return re.sub(r"[\x00-\x1f\x7f]", " ", detail)[:1500]


async def _chamber_response(
    client: httpx.AsyncClient,
    store: _ConnectionStore,
    connection: dict[str, Any],
    method: str,
    path: str,
    *,
    payload: Any = None,
    key: str | None = None,
    binary: bool = False,
    upload: tuple[str, bytes, str] | None = None,
    field: str = "file",
) -> tuple[bytes, str]:
    url = connection.get("url")
    if not url:
        raise HTTPException(503, "Load testing is not configured. Ask an administrator to connect Ampule Chamber.")
    request_id = uuid4().hex
    credential = store.token(connection)
    headers = {
        "Authorization": f"Bearer {credential}",
        "X-Request-ID": request_id,
        "X-Chamber-Client": "relayna-studio",
    }
    if key:
        headers["Idempotency-Key"] = key
    kwargs: dict[str, Any] = {"json": payload}
    if upload:
        kwargs = {"files": {"file": upload}, "data": {"field": field}}
    limit = 32 * 1024 * 1024 if binary else 2 * 1024 * 1024
    try:
        async with client.stream(
            method,
            f"{url}/api/v1/{path}",
            headers=headers,
            timeout=30 if binary or upload else 5,
            follow_redirects=False,
            **kwargs,
        ) as response:
            if response.status_code >= 300:
                category = {
                    401: "unauthorized",
                    403: "forbidden",
                    404: "not_found",
                    409: "conflict",
                    400: "validation",
                    422: "validation",
                }.get(response.status_code, "unavailable")
                messages = {
                    "unauthorized": "Chamber rejected its credential. Update the connection token.",
                    "forbidden": "The Chamber credential lacks permission for this API operation.",
                    "not_found": "Chamber could not find this run or API. Check its version and retention.",
                    "conflict": (
                        "Chamber rejected the request because its target is occupied, cleanup is required, "
                        "or the plan conflicts. Check target attention and review the plan."
                    ),
                    "validation": (
                        "Chamber rejected the configuration. "
                        "Check target, required inputs, load limits and supported scenario settings."
                    ),
                    "unavailable": "Chamber is unavailable. Retry after checking its API status.",
                }
                status = 409 if response.status_code in {400, 409, 422} else 502
                diagnostic = ""
                if response.status_code in {400, 409, 422}:
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 16384:
                            break
                    if len(body) <= 16384:
                        try:
                            diagnostic = _validation_detail(json.loads(body).get("detail"), payload, credential)
                        except (ValueError, AttributeError):
                            pass
                raise HTTPException(
                    status,
                    f"{messages[category]} {diagnostic} Reference: {request_id}",
                    headers={"X-Chamber-Error": category, "X-Request-ID": request_id},
                )
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > limit:
                    raise HTTPException(
                        502,
                        (
                            "Chamber response exceeded the supported size. "
                            "Narrow the evidence page or download a smaller artifact."
                        ),
                    )
            return bytes(chunks), response.headers.get("content-type", "application/octet-stream")
    except httpx.HTTPError as exc:
        raise HTTPException(
            502,
            f"Chamber is unavailable or returned an invalid response. You can retry safely. Reference: {request_id}",
            headers={"X-Chamber-Error": "unreachable", "X-Request-ID": request_id},
        ) from exc


async def _chamber_json(
    client: httpx.AsyncClient,
    store: _ConnectionStore,
    connection: dict[str, Any],
    method: str,
    path: str,
    payload: Any = None,
    key: str | None = None,
) -> dict[str, Any]:
    raw, _ = await _chamber_response(client, store, connection, method, path, payload=payload, key=key)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Expected object")
        return data
    except ValueError as exc:
        raise HTTPException(
            502, "Chamber is unavailable or returned an invalid response. You can retry safely."
        ) from exc
