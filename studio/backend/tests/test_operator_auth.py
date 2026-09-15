from __future__ import annotations

from types import SimpleNamespace

import fakeredis.aioredis
import httpx
import pytest
from fastapi import FastAPI
from relayna_studio._operator_auth import _install_operator_auth
from relayna_studio.audit_context import current_actor_user_id
from relayna_studio.config import StudioBackendSettings

TOKEN = "op_live_" + "test-operator-token-" * 3


@pytest.fixture
def operator(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("relayna_studio.app.get_studio_runtime", lambda app: SimpleNamespace(redis=redis))

    def make(token=TOKEN):
        app = FastAPI()

        @app.get("/studio/services")
        @app.post("/studio/services")
        async def services():
            return {"actor": current_actor_user_id()}

        _install_operator_auth(
            app,
            StudioBackendSettings(
                redis_url="redis://test", auth_mode="operator", operator_token=token, session_cookie_secure=False
            ),
        )
        return app

    return make, redis


@pytest.mark.asyncio
async def test_token_login_csrf_logout_and_bearer(operator):
    make, redis = operator
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make()), base_url="http://studio.test") as client:
        denied = await client.get("/studio/auth/session")
        assert denied.status_code == 401 and denied.json()["auth_mode"] == "operator"
        assert (await client.get("/studio/auth/config")).json()["auth_mode"] == "operator"
        assert (await client.get("/studio/auth/login")).status_code == 302
        assert (await client.post("/studio/auth/login", json={"token": TOKEN})).status_code == 403
        assert (
            await client.post(
                "/studio/auth/login",
                headers={"x-csrf-token": "operator-login", "sec-fetch-site": "cross-site"},
                json={"token": TOKEN},
            )
        ).status_code == 403
        headers = {"x-csrf-token": "operator-login"}
        assert (await client.post("/studio/auth/login", headers=headers, json={"token": "wrong"})).status_code == 401
        result = await client.post("/studio/auth/login", headers=headers, json={"token": TOKEN})
        assert result.status_code == 200
        assert "HttpOnly" in result.headers["set-cookie"] and "SameSite=lax" in result.headers["set-cookie"]
        csrf = result.json()["csrf_token"]
        assert TOKEN not in result.text and TOKEN not in result.headers["set-cookie"]
        assert (await client.get("/studio/auth/session")).json()["user"]["role"] == "admin"
        assert (await client.get("/studio/services")).json()["actor"] == "shared-operator"
        assert (await client.post("/studio/services")).status_code == 403
        assert (await client.post("/studio/services", headers={"x-csrf-token": csrf})).status_code == 200
        assert (await client.get("/studio/admin/users")).json()["count"] == 1
        assert (
            await client.patch("/studio/admin/users/shared-operator", headers={"x-csrf-token": csrf}, json={})
        ).status_code == 409
        assert (await client.post("/studio/auth/logout", headers={"x-csrf-token": csrf})).status_code == 204
        assert (await client.get("/studio/services")).status_code == 401
        assert (await client.post("/studio/services", headers={"authorization": "Bearer " + TOKEN})).status_code == 200
        assert (await client.get("/studio/services", headers={"authorization": "Bearer wrong"})).status_code == 401
        for key in await redis.keys("*"):
            assert TOKEN not in key and TOKEN not in str(await redis.get(key))


@pytest.mark.asyncio
async def test_rotation_expiry_and_login_throttle(operator):
    make, redis = operator
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make()), base_url="http://studio.test") as client:
        login = await client.post(
            "/studio/auth/login", headers={"x-csrf-token": "operator-login"}, json={"token": TOKEN}
        )
        assert login.status_code == 200
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=make(TOKEN + "rotated")),
            base_url="http://studio.test",
            cookies=client.cookies,
        ) as rotated:
            assert (await rotated.get("/studio/services")).status_code == 401
        keys = await redis.keys("*:session:*")
        assert 0 < await redis.ttl(keys[0]) <= 28800
        await redis.delete(*keys)
        assert (await client.get("/studio/services")).status_code == 401
        for _ in range(9):
            assert (
                await client.post(
                    "/studio/auth/login", headers={"x-csrf-token": "operator-login"}, json={"token": "bad"}
                )
            ).status_code == 401
        assert (
            await client.post("/studio/auth/login", headers={"x-csrf-token": "operator-login"}, json={"token": TOKEN})
        ).status_code == 429


def test_operator_settings_do_not_require_entra(monkeypatch):
    monkeypatch.setenv("RELAYNA_STUDIO_REDIS_URL", "redis://test")
    monkeypatch.setenv("RELAYNA_STUDIO_DATABASE_URL", "postgresql+asyncpg://test/test")
    monkeypatch.setenv("RELAYNA_STUDIO_AUTH_MODE", "operator")
    monkeypatch.setenv("RELAYNA_STUDIO_OPERATOR_TOKEN", TOKEN)
    settings = StudioBackendSettings.from_env()
    assert settings.to_app_kwargs()["entra_config"] is None
    from relayna_studio.factory import create_app

    app = create_app(settings=settings)
    assert "/studio/auth/login" in {r.path for r in app.routes}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"auth_mode": "none"},
        {"auth_mode": "operator", "operator_token": "weak"},
        {"auth_mode": "operator", "operator_token": TOKEN, "session_ttl_seconds": 0},
    ],
)
def test_invalid_auth_settings_fail_closed(kwargs):
    with pytest.raises(RuntimeError):
        StudioBackendSettings(redis_url="redis://test", **kwargs)
