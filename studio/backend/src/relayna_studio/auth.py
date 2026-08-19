from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlencode, urlparse

import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from starlette.types import ASGIApp, Receive, Scope, Send

from .audit_context import reset_actor_user_id, set_actor_user_id

SESSION_COOKIE = "relayna_studio_session"
LOGIN_COOKIE = "relayna_studio_login"
CSRF_HEADER = "x-csrf-token"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _safe_return_to(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    return value


class StudioRole(StrEnum):
    ADMIN = "admin"
    READONLY = "readonly"


class StudioMemberStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"


class StudioMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    tenant_id: str
    object_id: str
    email: str
    display_name: str
    role: StudioRole
    status: StudioMemberStatus
    created_at: str
    updated_at: str
    last_sign_in_at: str | None = None


class StudioSessionResponse(BaseModel):
    user: StudioMember
    csrf_token: str


class StudioUserListResponse(BaseModel):
    count: int
    users: list[StudioMember]


class StudioUserUpdate(BaseModel):
    role: StudioRole | None = None
    status: StudioMemberStatus | None = None


@dataclass(slots=True, frozen=True)
class StudioEntraConfig:
    application_id: str
    tenant_id: str
    issuer: str
    discovery_url: str
    redirect_uri: str
    private_key_path: str
    certificate_path: str
    admin_emails: tuple[str, ...] = ()
    admin_object_ids: tuple[str, ...] = ()
    session_ttl_seconds: int = 28_800
    login_ttl_seconds: int = 600
    session_cookie_secure: bool = True
    redis_prefix: str = "studio:auth"
    jwks_cache_ttl_seconds: int = 300
    clock_skew_seconds: int = 60

    def __post_init__(self) -> None:
        required = {
            "application_id": self.application_id,
            "tenant_id": self.tenant_id,
            "issuer": self.issuer,
            "discovery_url": self.discovery_url,
            "redirect_uri": self.redirect_uri,
            "private_key_path": self.private_key_path,
            "certificate_path": self.certificate_path,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise RuntimeError(f"Studio Entra configuration is missing: {', '.join(missing)}.")
        if bool(self.admin_emails) != bool(self.admin_object_ids):
            raise RuntimeError("Studio Entra admin email and object-id allowlists must be configured together.")
        if self.session_ttl_seconds <= 0 or self.login_ttl_seconds <= 0:
            raise RuntimeError("Studio Entra session and login TTLs must be positive.")
        urls = (("issuer", self.issuer), ("discovery_url", self.discovery_url), ("redirect_uri", self.redirect_uri))
        for name, value in urls:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RuntimeError(f"Studio Entra {name} must be an absolute HTTP(S) URL.")
            if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
                raise RuntimeError(f"Studio Entra {name} must use HTTPS outside localhost.")
        normalized_emails = tuple(item.strip().lower() for item in self.admin_emails if item.strip())
        object.__setattr__(self, "admin_emails", normalized_emails)
        object.__setattr__(
            self,
            "admin_object_ids",
            tuple(item.strip().lower() for item in self.admin_object_ids if item.strip()),
        )


@dataclass(slots=True, frozen=True)
class _LoginTransaction:
    state: str
    nonce: str
    code_verifier: str
    return_to: str

    def dump(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def load(cls, value: str | bytes) -> _LoginTransaction:
        payload = json.loads(value)
        return cls(**payload)


@dataclass(slots=True, frozen=True)
class _Session:
    user_id: str
    csrf_token: str

    def dump(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def load(cls, value: str | bytes) -> _Session:
        payload = json.loads(value)
        return cls(**payload)


class StudioAuthStore:
    _UPDATE_MEMBER_SCRIPT = """
local current_raw = redis.call('GET', KEYS[1])
if not current_raw then return 'NOT_FOUND' end
local current = cjson.decode(current_raw)
local next = cjson.decode(ARGV[1])
local current_admin = current['role'] == 'admin' and current['status'] == 'active'
local next_admin = next['role'] == 'admin' and next['status'] == 'active'
if current_admin and not next_admin and redis.call('SCARD', KEYS[2]) <= 1 then
  return 'LAST_ADMIN'
end
redis.call('SET', KEYS[1], ARGV[1])
if next_admin then redis.call('SADD', KEYS[2], next['user_id'])
else redis.call('SREM', KEYS[2], next['user_id']) end
return 'OK'
"""

    def __init__(self, redis: Redis, *, prefix: str) -> None:
        self.redis = redis
        self.prefix = prefix.rstrip(":")

    def _member_key(self, user_id: str) -> str:
        return f"{self.prefix}:member:{user_id.lower()}"

    @property
    def _members_key(self) -> str:
        return f"{self.prefix}:members"

    @property
    def _active_admins_key(self) -> str:
        return f"{self.prefix}:active-admins"

    async def initialize(self, config: StudioEntraConfig) -> None:
        admin_count = await cast(Awaitable[int], self.redis.scard(self._active_admins_key))
        if admin_count == 0 and not config.admin_emails:
            raise RuntimeError(
                "Studio has no active administrator; configure RELAYNA_STUDIO_ENTRA_ADMIN_EMAILS and "
                "RELAYNA_STUDIO_ENTRA_ADMIN_OBJECT_IDS for bootstrap."
            )

    async def upsert_login(self, claims: dict[str, Any], config: StudioEntraConfig) -> StudioMember:
        object_id = str(claims["oid"]).strip().lower()
        tenant_id = str(claims["tid"]).strip().lower()
        user_id = f"{tenant_id}:{object_id}"
        email = str(claims["email"]).strip().lower()
        now = _iso(_now())
        key = self._member_key(user_id)
        existing_raw = await self.redis.get(key)
        if existing_raw:
            existing = StudioMember.model_validate_json(existing_raw)
            member = existing.model_copy(
                update={
                    "email": email,
                    "display_name": str(claims.get("name") or email),
                    "updated_at": now,
                    "last_sign_in_at": now,
                }
            )
        else:
            bootstrap = email in config.admin_emails and object_id in config.admin_object_ids
            member = StudioMember(
                user_id=user_id,
                tenant_id=tenant_id,
                object_id=object_id,
                email=email,
                display_name=str(claims.get("name") or email),
                role=StudioRole.ADMIN if bootstrap else StudioRole.READONLY,
                status=StudioMemberStatus.ACTIVE if bootstrap else StudioMemberStatus.PENDING,
                created_at=now,
                updated_at=now,
                last_sign_in_at=now,
            )
        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.set(key, member.model_dump_json())
            pipeline.sadd(self._members_key, user_id)
            if member.role is StudioRole.ADMIN and member.status is StudioMemberStatus.ACTIVE:
                pipeline.sadd(self._active_admins_key, user_id)
            await pipeline.execute()
        return member

    async def get_member(self, user_id: str) -> StudioMember | None:
        raw = await self.redis.get(self._member_key(user_id))
        return StudioMember.model_validate_json(raw) if raw else None

    async def list_members(self) -> list[StudioMember]:
        members = await cast(Awaitable[set[str | bytes]], self.redis.smembers(self._members_key))
        user_ids = sorted(members)
        normalized = [item.decode() if isinstance(item, bytes) else str(item) for item in user_ids]
        if not normalized:
            return []
        values = await self.redis.mget([self._member_key(item) for item in normalized])
        members = [StudioMember.model_validate_json(value) for value in values if value]
        return sorted(members, key=lambda item: (item.email, item.object_id))

    async def update_member(
        self,
        user_id: str,
        update: StudioUserUpdate,
        *,
        actor_user_id: str,
    ) -> StudioMember:
        current = await self.get_member(user_id)
        if current is None:
            raise KeyError(user_id)
        next_member = current.model_copy(
            update={
                "role": update.role or current.role,
                "status": update.status or current.status,
                "updated_at": _iso(_now()),
            }
        )
        if user_id == actor_user_id and (
            next_member.role is not StudioRole.ADMIN or next_member.status is not StudioMemberStatus.ACTIVE
        ):
            raise ValueError("Administrators cannot demote or block themselves.")
        result = await cast(
            Awaitable[str | bytes],
            self.redis.eval(
                self._UPDATE_MEMBER_SCRIPT,
                2,
                self._member_key(user_id),
                self._active_admins_key,
                next_member.model_dump_json(),
            ),
        )
        normalized = result.decode() if isinstance(result, bytes) else str(result)
        if normalized == "LAST_ADMIN":
            raise ValueError("At least one active administrator must remain.")
        if normalized == "NOT_FOUND":
            raise KeyError(user_id)
        return next_member

    async def save_login(self, raw_token: str, transaction: _LoginTransaction, ttl: int) -> None:
        await self.redis.set(f"{self.prefix}:login:{_token_digest(raw_token)}", transaction.dump(), ex=ttl, nx=True)

    async def consume_login(self, raw_token: str) -> _LoginTransaction | None:
        raw = await self.redis.getdel(f"{self.prefix}:login:{_token_digest(raw_token)}")
        return _LoginTransaction.load(raw) if raw else None

    async def save_session(self, raw_token: str, session: _Session, ttl: int) -> None:
        await self.redis.set(f"{self.prefix}:session:{_token_digest(raw_token)}", session.dump(), ex=ttl)

    async def get_session(self, raw_token: str) -> _Session | None:
        raw = await self.redis.get(f"{self.prefix}:session:{_token_digest(raw_token)}")
        return _Session.load(raw) if raw else None

    async def delete_session(self, raw_token: str) -> None:
        await self.redis.delete(f"{self.prefix}:session:{_token_digest(raw_token)}")


class StudioAuthService:
    def __init__(
        self,
        *,
        config: StudioEntraConfig,
        store: Any,
        http_client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self.store = store
        self.http_client = http_client
        self._private_key, self._certificate_thumbprint = self._load_credentials(config)
        self._discovery: tuple[float, dict[str, Any]] | None = None
        self._jwks: tuple[float, dict[str, Any]] | None = None
        self._cache_lock = asyncio.Lock()

    @staticmethod
    def _load_credentials(config: StudioEntraConfig) -> tuple[rsa.RSAPrivateKey, str]:
        private_key = serialization.load_pem_private_key(Path(config.private_key_path).read_bytes(), password=None)
        certificate = x509.load_pem_x509_certificate(Path(config.certificate_path).read_bytes())
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise RuntimeError("Studio Entra private key must be RSA.")
        certificate_public_key = certificate.public_key()
        if not isinstance(certificate_public_key, rsa.RSAPublicKey):
            raise RuntimeError("Studio Entra certificate must contain an RSA public key.")
        if private_key.public_key().public_numbers() != certificate_public_key.public_numbers():
            raise RuntimeError("Studio Entra certificate does not match the configured private key.")
        thumbprint = _b64url(certificate.fingerprint(hashes.SHA256()))
        return private_key, thumbprint

    async def initialize(self) -> None:
        await self.store.initialize(self.config)

    async def _document(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._discovery and self._discovery[0] > now:
            return self._discovery[1]
        async with self._cache_lock:
            if self._discovery and self._discovery[0] > now:
                return self._discovery[1]
            response = await self.http_client.get(self.config.discovery_url)
            response.raise_for_status()
            document = response.json()
            required = {"authorization_endpoint", "token_endpoint", "jwks_uri"}
            if not required.issubset(document):
                raise RuntimeError("Studio Entra discovery document is incomplete.")
            if document.get("issuer") != self.config.issuer:
                raise RuntimeError("Studio Entra discovery issuer does not match the configured issuer.")
            self._discovery = (now + self.config.jwks_cache_ttl_seconds, document)
            return document

    async def _jwks_document(self, uri: str, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force_refresh and self._jwks and self._jwks[0] > now:
            return self._jwks[1]
        async with self._cache_lock:
            if not force_refresh and self._jwks and self._jwks[0] > now:
                return self._jwks[1]
            response = await self.http_client.get(uri)
            response.raise_for_status()
            document = response.json()
            if not isinstance(document.get("keys"), list):
                raise RuntimeError("Studio Entra JWKS document is incomplete.")
            self._jwks = (now + self.config.jwks_cache_ttl_seconds, document)
            return document

    async def start_login(self, return_to: str | None) -> tuple[str, str]:
        document = await self._document()
        raw_login = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        await self.store.save_login(
            raw_login,
            _LoginTransaction(state=state, nonce=nonce, code_verifier=verifier, return_to=_safe_return_to(return_to)),
            self.config.login_ttl_seconds,
        )
        query = urlencode(
            {
                "client_id": self.config.application_id,
                "response_type": "code",
                "redirect_uri": self.config.redirect_uri,
                "response_mode": "query",
                "scope": "openid profile email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{document['authorization_endpoint']}?{query}", raw_login

    def _client_assertion(self, token_endpoint: str) -> str:
        issued_at = int(time.time())
        return jwt.encode(
            {
                "aud": token_endpoint,
                "iss": self.config.application_id,
                "sub": self.config.application_id,
                "jti": str(uuid.uuid4()),
                "iat": issued_at,
                "nbf": issued_at,
                "exp": issued_at + 300,
            },
            self._private_key,
            algorithm="PS256",
            headers={"x5t#S256": self._certificate_thumbprint},
        )

    async def finish_login(self, *, raw_login: str, state: str, code: str) -> tuple[str, StudioMember, str]:
        transaction = await self.store.consume_login(raw_login)
        if transaction is None or not secrets.compare_digest(transaction.state, state):
            raise HTTPException(status_code=400, detail="The login transaction is invalid or expired.")
        document = await self._document()
        token_endpoint = str(document["token_endpoint"])
        response = await self.http_client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "client_id": self.config.application_id,
                "redirect_uri": self.config.redirect_uri,
                "code": code,
                "code_verifier": transaction.code_verifier,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": self._client_assertion(token_endpoint),
            },
        )
        if response.is_error:
            raise HTTPException(status_code=502, detail="Microsoft Entra rejected the Studio login exchange.")
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str):
            raise HTTPException(status_code=502, detail="Microsoft Entra did not return an ID token.")
        claims = await self._verify_id_token(id_token, transaction.nonce, str(document["jwks_uri"]))
        member = await self.store.upsert_login(claims, self.config)
        raw_session = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        await self.store.save_session(
            raw_session,
            _Session(user_id=member.user_id, csrf_token=csrf_token),
            self.config.session_ttl_seconds,
        )
        return raw_session, member, transaction.return_to

    async def _verify_id_token(self, token: str, nonce: str, jwks_uri: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            keys = (await self._jwks_document(jwks_uri))["keys"]
            key_data = next((item for item in keys if item.get("kid") == header.get("kid")), None)
            if key_data is None:
                keys = (await self._jwks_document(jwks_uri, force_refresh=True))["keys"]
                key_data = next(item for item in keys if item.get("kid") == header.get("kid"))
            key = jwt.PyJWK.from_dict(key_data).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.config.application_id,
                issuer=self.config.issuer,
                leeway=self.config.clock_skew_seconds,
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "tid", "oid", "nonce"]},
            )
        except (jwt.PyJWTError, StopIteration, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Microsoft Entra returned an invalid ID token.") from exc
        if claims.get("tid") != self.config.tenant_id or not claims.get("oid"):
            raise HTTPException(status_code=403, detail="The Entra identity does not belong to the configured tenant.")
        if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], nonce):
            raise HTTPException(status_code=400, detail="Microsoft Entra returned an invalid login nonce.")
        email = claims.get("email") or claims.get("preferred_username")
        if not isinstance(email, str) or not email.strip():
            raise HTTPException(status_code=403, detail="The Entra identity has no usable email address.")
        return {**claims, "email": email}

    async def session_context(self, request: Request) -> tuple[_Session, StudioMember] | None:
        raw_token = request.cookies.get(SESSION_COOKIE)
        if not raw_token:
            return None
        session = await self.store.get_session(raw_token)
        if session is None:
            return None
        member = await self.store.get_member(session.user_id)
        return (session, member) if member else None

    async def require_session(self, request: Request) -> tuple[_Session, StudioMember]:
        context = await self.session_context(request)
        if context is None:
            raise HTTPException(status_code=401, detail="Studio authentication is required.")
        return context


class StudioAuthMiddleware:
    def __init__(self, app: ASGIApp, *, service: StudioAuthService) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        path = request.url.path
        method = request.method.upper()
        public = method == "OPTIONS" or path in {
            "/studio/auth/config",
            "/studio/auth/login",
            "/studio/auth/callback",
            "/studio/gateway/services",
            "/studio/ingest/events",
            "/metrics",
            "/healthz",
            "/readyz",
            "/livez",
        }
        session_limited = path in {"/studio/auth/session", "/studio/auth/logout"}
        if public:
            await self.app(scope, receive, send)
            return
        context = await self.service.session_context(request)
        if context is None:
            await JSONResponse({"detail": "Studio authentication is required."}, status_code=401)(scope, receive, send)
            return
        session, member = context
        scope.setdefault("state", {})["studio_member"] = member
        if session_limited:
            if method not in {"GET", "HEAD", "POST"}:
                await JSONResponse({"detail": "Method not allowed."}, status_code=405)(scope, receive, send)
                return
        elif member.status is not StudioMemberStatus.ACTIVE:
            response = JSONResponse({"detail": f"Studio account is {member.status.value}."}, status_code=403)
            await response(scope, receive, send)
            return
        elif path.startswith("/studio/admin/") and member.role is not StudioRole.ADMIN:
            response = JSONResponse({"detail": "Studio administrator access is required."}, status_code=403)
            await response(scope, receive, send)
            return
        elif method not in {"GET", "HEAD", "OPTIONS"} and member.role is not StudioRole.ADMIN:
            response = JSONResponse({"detail": "Studio administrator access is required."}, status_code=403)
            await response(scope, receive, send)
            return
        if method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get(CSRF_HEADER, "")
            if not supplied or not secrets.compare_digest(supplied, session.csrf_token):
                await JSONResponse({"detail": "The Studio CSRF token is missing or invalid."}, status_code=403)(
                    scope, receive, send
                )
                return
        actor_token = set_actor_user_id(member.user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_actor_user_id(actor_token)


def _set_session_cookie(response: Response, config: StudioEntraConfig, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=config.session_ttl_seconds,
        httponly=True,
        secure=config.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def create_studio_auth_router(service: StudioAuthService, *, prefix: str = "/studio") -> APIRouter:
    router = APIRouter()

    @router.get(f"{prefix}/auth/config")
    async def auth_config():
        return {"login_path": f"{prefix}/auth/login", "session_ttl_seconds": service.config.session_ttl_seconds}

    @router.get(f"{prefix}/auth/login")
    async def auth_login(return_to: str | None = Query(default="/")):
        location, raw_login = await service.start_login(return_to)
        response = RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            LOGIN_COOKIE,
            raw_login,
            max_age=service.config.login_ttl_seconds,
            httponly=True,
            secure=service.config.session_cookie_secure,
            samesite="lax",
            path=f"{prefix}/auth/callback",
        )
        return response

    @router.get(f"{prefix}/auth/callback")
    async def auth_callback(
        request: Request,
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ):
        raw_login = request.cookies.get(LOGIN_COOKIE)
        if not raw_login:
            raise HTTPException(status_code=400, detail="The login transaction cookie is missing.")
        if error:
            transaction = await service.store.consume_login(raw_login)
            if transaction is None or state is None or not secrets.compare_digest(transaction.state, state):
                raise HTTPException(status_code=400, detail="The login transaction is invalid or expired.")
            message = quote("Microsoft Entra did not complete the Studio sign-in.")
            response = RedirectResponse(
                f"/?auth_error={message}", status_code=302, headers={"Cache-Control": "no-store"}
            )
            response.delete_cookie(LOGIN_COOKIE, path=f"{prefix}/auth/callback")
            return response
        if code is None or state is None:
            raise HTTPException(status_code=400, detail="The authorization response is incomplete.")
        raw_session, _, return_to = await service.finish_login(raw_login=raw_login, state=state, code=code)
        response = RedirectResponse(return_to, status_code=302, headers={"Cache-Control": "no-store"})
        response.delete_cookie(LOGIN_COOKIE, path=f"{prefix}/auth/callback")
        _set_session_cookie(response, service.config, raw_session)
        return response

    @router.get(f"{prefix}/auth/session", response_model=StudioSessionResponse)
    async def auth_session(request: Request, response: Response):
        session, member = await service.require_session(request)
        response.headers["Cache-Control"] = "no-store"
        return StudioSessionResponse(user=member, csrf_token=session.csrf_token)

    @router.post(f"{prefix}/auth/logout", status_code=204)
    async def auth_logout(request: Request):
        raw_token = request.cookies.get(SESSION_COOKIE)
        if raw_token:
            await service.store.delete_session(raw_token)
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.get(f"{prefix}/admin/users", response_model=StudioUserListResponse)
    async def admin_users(response: Response):
        users = await service.store.list_members()
        response.headers["Cache-Control"] = "no-store"
        return StudioUserListResponse(count=len(users), users=users)

    @router.patch(f"{prefix}/admin/users/{{user_id}}", response_model=StudioMember)
    async def admin_update_user(request: Request, response: Response, user_id: str, update: StudioUserUpdate):
        actor = getattr(request.state, "studio_member", None)
        if not isinstance(actor, StudioMember):
            raise HTTPException(status_code=401, detail="Studio authentication is required.")
        try:
            member = await service.store.update_member(user_id, update, actor_user_id=actor.user_id)
            response.headers["Cache-Control"] = "no-store"
            return member
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Studio user '{user_id}' was not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router


__all__ = [
    "CSRF_HEADER",
    "LOGIN_COOKIE",
    "SESSION_COOKIE",
    "StudioAuthMiddleware",
    "StudioAuthService",
    "StudioAuthStore",
    "StudioEntraConfig",
    "StudioMember",
    "StudioMemberStatus",
    "StudioRole",
    "StudioSessionResponse",
    "StudioUserListResponse",
    "StudioUserUpdate",
    "create_studio_auth_router",
]
