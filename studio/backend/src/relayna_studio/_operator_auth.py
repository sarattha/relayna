"""Opt-in shared operator authentication, independent of Microsoft Entra."""

from __future__ import annotations

import json
import secrets
from hashlib import sha256
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from starlette.types import ASGIApp, Receive, Scope, Send

from .audit_context import reset_actor_user_id, set_actor_user_id
from .auth import StudioMember, StudioMemberStatus, StudioRole

if TYPE_CHECKING:
    from .config import StudioBackendSettings

_COOKIE = "relayna_studio_operator"
_MEMBER = StudioMember(
    user_id="shared-operator",
    tenant_id="operator",
    object_id="shared-operator",
    email="",
    display_name="Operator",
    role=StudioRole.ADMIN,
    status=StudioMemberStatus.ACTIVE,
    created_at="1970-01-01T00:00:00Z",
    updated_at="1970-01-01T00:00:00Z",
)
_PUBLIC = {
    "/studio/auth/config",
    "/studio/auth/login",
    "/studio/gateway/services",
    "/studio/ingest/events",
    "/metrics",
    "/healthz",
    "/readyz",
    "/livez",
}


class _Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=4096)


class _OperatorAuth:
    def __init__(self, app: FastAPI, settings: StudioBackendSettings) -> None:
        self.app = app
        self.token = settings.operator_token
        self.ttl = settings.session_ttl_seconds
        self.secure = settings.session_cookie_secure
        # Rotation invalidates existing browser sessions without storing the
        # operator credential in Redis or sending it back to the browser.
        self.prefix = "studio:operator:" + sha256(self.token.encode()).hexdigest()

    @property
    def redis(self) -> Redis:
        from .app import get_studio_runtime

        return get_studio_runtime(self.app).redis

    def session_key(self, raw: str) -> str:
        return self.prefix + ":session:" + sha256(raw.encode()).hexdigest()

    def bearer(self, request: Request) -> bool:
        supplied = request.headers.get("authorization", "")
        return secrets.compare_digest(supplied.encode(), ("Bearer " + self.token).encode())

    async def session(self, request: Request) -> dict[str, str] | None:
        raw = request.cookies.get(_COOKIE)
        if not raw:
            return None
        data = await self.redis.get(self.session_key(raw))
        return json.loads(data) if data else None


class _OperatorMiddleware:
    def __init__(self, app: ASGIApp, *, auth: _OperatorAuth) -> None:
        self.app, self.auth = app, auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        if request.method == "OPTIONS" or request.url.path in _PUBLIC:
            await self.app(scope, receive, send)
            return
        bearer = self.auth.bearer(request)
        session = await self.auth.session(request) if not bearer else None
        if not bearer and session is None:
            await JSONResponse({"detail": "Operator sign-in is required.", "auth_mode": "operator"}, status_code=401)(
                scope, receive, send
            )
            return
        if not bearer and request.method not in {"GET", "HEAD", "OPTIONS"}:
            assert session is not None
            supplied = request.headers.get("x-csrf-token", "")
            if not supplied or not secrets.compare_digest(supplied.encode(), session["csrf_token"].encode()):
                await JSONResponse({"detail": "The Studio CSRF token is missing or invalid."}, status_code=403)(
                    scope, receive, send
                )
                return
        scope.setdefault("state", {})["studio_member"] = _MEMBER
        actor = set_actor_user_id(_MEMBER.user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_actor_user_id(actor)


def _install_operator_auth(app: FastAPI, settings: StudioBackendSettings) -> None:
    auth = _OperatorAuth(app, settings)
    router = APIRouter()

    @router.get("/studio/auth/config")
    async def config():
        return {"auth_mode": "operator", "login_path": "/studio/auth/login", "session_ttl_seconds": auth.ttl}

    @router.get("/studio/auth/login")
    async def login_page():
        return RedirectResponse("/", status_code=302, headers={"Cache-Control": "no-store"})

    @router.post("/studio/auth/login")
    async def login(request: Request, payload: _Login):
        if (
            request.headers.get("x-csrf-token") != "operator-login"
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            raise HTTPException(403, "Use the Studio operator sign-in form.")
        address = request.client.host if request.client else "unknown"
        key = auth.prefix + ":attempts:" + sha256(address.encode()).hexdigest()
        async with auth.redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, 60, nx=True)
            attempts, _ = await pipeline.execute()
        if attempts > 10:
            raise HTTPException(429, "Too many sign-in attempts. Try again in one minute.")
        if not secrets.compare_digest(payload.token.encode(), auth.token.encode()):
            raise HTTPException(401, "Invalid operator token.")
        old = request.cookies.get(_COOKIE)
        if old:
            await auth.redis.delete(auth.session_key(old))
        raw = secrets.token_urlsafe(32)
        session = {"csrf_token": secrets.token_urlsafe(32)}
        await auth.redis.set(auth.session_key(raw), json.dumps(session), ex=auth.ttl)
        response = JSONResponse(
            {"user": _MEMBER.model_dump(), **session, "auth_mode": "operator"}, headers={"Cache-Control": "no-store"}
        )
        response.set_cookie(_COOKIE, raw, max_age=auth.ttl, httponly=True, secure=auth.secure, samesite="lax", path="/")
        return response

    @router.get("/studio/auth/session")
    async def session(request: Request, response: Response):
        current = await auth.session(request)
        response.headers["Cache-Control"] = "no-store"
        return {
            "user": _MEMBER.model_dump(),
            "csrf_token": current["csrf_token"] if current else "",
            "auth_mode": "operator",
        }

    @router.post("/studio/auth/logout", status_code=204)
    async def logout(request: Request):
        raw = request.cookies.get(_COOKIE)
        if raw:
            await auth.redis.delete(auth.session_key(raw))
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.delete_cookie(_COOKIE, path="/", secure=auth.secure, httponly=True, samesite="lax")
        return response

    @router.get("/studio/admin/users")
    async def users(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"count": 1, "users": [_MEMBER.model_dump()]}

    @router.patch("/studio/admin/users/{user_id}")
    async def update_user(user_id: str):
        raise HTTPException(409, "Shared operator access is managed by the deployment token.")

    app.include_router(router)
    app.add_middleware(_OperatorMiddleware, auth=auth)
