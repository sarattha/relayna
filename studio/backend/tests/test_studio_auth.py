from __future__ import annotations

import base64
import hashlib
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import fakeredis.aioredis
import httpx
import jwt
import pytest
import relayna_studio.app as studio_app
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from fastapi.testclient import TestClient
from relayna_studio.app import create_studio_app
from relayna_studio.auth import SESSION_COOKIE, StudioAuthService, StudioAuthStore, StudioEntraConfig


def _b64(value: int) -> str:
    size = max(1, (value.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


def _write_client_credentials(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Studio test client")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    key_path = tmp_path / "studio-private-key.pem"
    cert_path = tmp_path / "studio-certificate.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


def _config(tmp_path: Path, **changes: object) -> StudioEntraConfig:
    key_path, cert_path = _write_client_credentials(tmp_path)
    values: dict[str, object] = {
        "application_id": "studio-client",
        "tenant_id": "tenant-1",
        "issuer": "http://127.0.0.1:19091/tenant-1/v2.0",
        "discovery_url": "http://127.0.0.1:19091/.well-known/openid-configuration",
        "redirect_uri": "http://127.0.0.1:5173/studio/auth/callback",
        "private_key_path": str(key_path),
        "certificate_path": str(cert_path),
        "admin_emails": ("ADMIN@example.test",),
        "admin_object_ids": ("ADMIN-OID",),
        "session_cookie_secure": False,
    }
    values.update(changes)
    return StudioEntraConfig(**values)  # type: ignore[arg-type]


def _issuer_transport(signing_key: rsa.RSAPrivateKey):
    authorization_queries: list[dict[str, list[str]]] = []
    public_numbers = signing_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "issuer-key",
        "use": "sig",
        "alg": "RS256",
        "n": _b64(public_numbers.n),
        "e": _b64(public_numbers.e),
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "http://127.0.0.1:19091/tenant-1/v2.0",
                    "authorization_endpoint": "http://127.0.0.1:19091/authorize",
                    "token_endpoint": "http://127.0.0.1:19091/token",
                    "jwks_uri": "http://127.0.0.1:19091/jwks",
                },
            )
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            code = form["code"][0]
            query = authorization_queries[-1]
            verifier = form["code_verifier"][0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            assert challenge == query["code_challenge"][0]
            assertion = form["client_assertion"][0]
            assertion_header = jwt.get_unverified_header(assertion)
            assertion_claims = jwt.decode(assertion, options={"verify_signature": False})
            assert assertion_header["alg"] == "PS256"
            assert assertion_header["typ"] == "JWT"
            assert assertion_header["x5t#S256"]
            assert assertion_claims["aud"] == "http://127.0.0.1:19091/token"
            assert assertion_claims["iss"] == assertion_claims["sub"] == "studio-client"
            assert assertion_claims["exp"] - assertion_claims["iat"] == 300
            object_id = "admin-oid" if code == "admin" else "pending-oid"
            email = "admin@example.test" if code == "admin" else "reader@example.test"
            now = int(datetime.now(UTC).timestamp())
            token = jwt.encode(
                {
                    "iss": "http://127.0.0.1:19091/tenant-1/v2.0",
                    "aud": "studio-client",
                    "iat": now,
                    "nbf": now,
                    "exp": now + 300,
                    "tid": "tenant-1",
                    "sub": object_id,
                    "oid": object_id,
                    "email": email,
                    "name": "Studio Admin" if code == "admin" else "Read Only User",
                    "nonce": query["nonce"][0],
                },
                signing_key,
                algorithm="RS256",
                headers={"kid": "issuer-key"},
            )
            return httpx.Response(200, json={"id_token": token})
        raise AssertionError(f"Unexpected issuer request: {request.method} {request.url}")

    return httpx.MockTransport(handler), authorization_queries


def _id_token(
    signing_key: rsa.RSAPrivateKey,
    *,
    kid: str = "issuer-key",
    omit: tuple[str, ...] = (),
    **changes: object,
) -> str:
    now = int(datetime.now(UTC).timestamp())
    claims: dict[str, object] = {
        "iss": "http://127.0.0.1:19091/tenant-1/v2.0",
        "aud": "studio-client",
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "tid": "tenant-1",
        "sub": "user-oid",
        "oid": "user-oid",
        "email": "user@example.test",
        "nonce": "nonce-1",
    }
    claims.update(changes)
    for name in omit:
        claims.pop(name, None)
    return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": kid})


def _login(client: TestClient, code: str, queries: list[dict[str, list[str]]]) -> dict[str, object]:
    start = client.get("/studio/auth/login?return_to=%2Fservices", follow_redirects=False)
    assert start.status_code == 302
    query = parse_qs(urlparse(start.headers["location"]).query)
    queries.append(query)
    callback = client.get(
        "/studio/auth/callback",
        params={"code": code, "state": query["state"][0]},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/services"
    session = client.get("/studio/auth/session")
    assert session.status_code == 200
    return session.json()


def test_entra_config_validation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.admin_emails == ("admin@example.test",)
    assert config.admin_object_ids == ("admin-oid",)

    with pytest.raises(RuntimeError, match="allowlists"):
        _config(tmp_path, admin_object_ids=())
    with pytest.raises(RuntimeError, match="positive"):
        _config(tmp_path, session_ttl_seconds=0)
    with pytest.raises(RuntimeError, match="absolute"):
        _config(tmp_path, issuer="not-a-url")
    with pytest.raises(RuntimeError, match="HTTPS"):
        _config(tmp_path, issuer="http://entra.example.test/tenant/v2.0")


def test_certificate_and_private_key_must_match(tmp_path: Path) -> None:
    config = _config(tmp_path)
    other_key, _ = _write_client_credentials(tmp_path / "other")
    mismatched = StudioEntraConfig(**{**asdict(config), "private_key_path": str(other_key)})
    with pytest.raises(RuntimeError, match="does not match"):
        StudioAuthService._load_credentials(mismatched)


def test_full_login_rbac_and_local_logout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(studio_app.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    transport, authorization_queries = _issuer_transport(signing_key)

    def client_factory(_timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    app = create_studio_app(
        redis_url="redis://studio-auth-test/0",
        federation_client_factory=client_factory,
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
        entra_config=_config(tmp_path),
    )

    with TestClient(app) as admin_client, TestClient(app) as reader_client:
        assert admin_client.get("/studio/services").status_code == 401
        assert admin_client.get("/studio/gateway/services").status_code == 200
        ingest = admin_client.post("/studio/ingest/events", json={"events": []})
        assert ingest.json()["detail"] != "Studio authentication is required."

        admin_session = _login(admin_client, "admin", authorization_queries)
        assert admin_session["user"]["role"] == "admin"  # type: ignore[index]
        assert admin_session["user"]["status"] == "active"  # type: ignore[index]
        admin_csrf = str(admin_session["csrf_token"])
        raw_session = admin_client.cookies.get(SESSION_COOKIE)
        assert raw_session is not None
        assert admin_client.portal is not None
        session_keys_raw = admin_client.portal.call(fake_redis.keys, "studio:auth:session:*")
        session_keys = [item.decode() if isinstance(item, bytes) else item for item in session_keys_raw]
        assert session_keys == [f"studio:auth:session:{hashlib.sha256(raw_session.encode()).hexdigest()}"]
        assert raw_session not in session_keys[0]
        assert 0 < admin_client.portal.call(fake_redis.ttl, session_keys[0]) <= 28_800
        assert admin_client.get("/studio/admin/users").json()["count"] == 1
        assert admin_client.get("/studio/auth/config").json()["login_path"] == "/studio/auth/login"
        assert admin_client.put("/studio/auth/session", headers={"X-CSRF-Token": admin_csrf}).status_code == 405
        missing_user = admin_client.patch(
            "/studio/admin/users/tenant-1:missing",
            headers={"X-CSRF-Token": admin_csrf},
            json={"status": "blocked"},
        )
        assert missing_user.status_code == 404
        assert admin_client.post("/studio/services", json={}).status_code == 403

        repeated_session = _login(admin_client, "admin", authorization_queries)
        assert repeated_session["user"]["user_id"] == "tenant-1:admin-oid"  # type: ignore[index]
        admin_csrf = str(repeated_session["csrf_token"])

        pending_session = _login(reader_client, "pending", authorization_queries)
        assert pending_session["user"]["status"] == "pending"  # type: ignore[index]
        reader_csrf = str(pending_session["csrf_token"])
        assert reader_client.get("/studio/services").status_code == 403

        activated = admin_client.patch(
            "/studio/admin/users/tenant-1:pending-oid",
            headers={"X-CSRF-Token": admin_csrf},
            json={"role": "readonly", "status": "active"},
        )
        assert activated.status_code == 200
        assert reader_client.get("/studio/services").status_code == 200
        assert reader_client.get("/studio/admin/users").status_code == 403
        assert (
            reader_client.post(
                "/studio/services",
                headers={"X-CSRF-Token": reader_csrf},
                json={},
            ).status_code
            == 403
        )

        self_demote = admin_client.patch(
            "/studio/admin/users/tenant-1:admin-oid",
            headers={"X-CSRF-Token": admin_csrf},
            json={"role": "readonly"},
        )
        assert self_demote.status_code == 409
        blocked = admin_client.patch(
            "/studio/admin/users/tenant-1:pending-oid",
            headers={"X-CSRF-Token": admin_csrf},
            json={"status": "blocked"},
        )
        assert blocked.status_code == 200
        assert reader_client.get("/studio/services").status_code == 403
        assert reader_client.post("/studio/auth/logout", headers={"X-CSRF-Token": reader_csrf}).status_code == 204
        assert reader_client.get("/studio/auth/session").status_code == 401

        replay = admin_client.get(
            "/studio/auth/callback",
            params={"code": "admin", "state": authorization_queries[0]["state"][0]},
            follow_redirects=False,
        )
        assert replay.status_code == 400

        missing_cookie = TestClient(app).get(
            "/studio/auth/callback",
            params={"code": "admin", "state": "missing"},
            follow_redirects=False,
        )
        assert missing_cookie.status_code == 400

        with TestClient(app) as error_client:
            error_start = error_client.get("/studio/auth/login", follow_redirects=False)
            error_state = parse_qs(urlparse(error_start.headers["location"]).query)["state"][0]
            cancelled = error_client.get(
                "/studio/auth/callback",
                params={"error": "access_denied", "state": error_state},
                follow_redirects=False,
            )
            assert cancelled.status_code == 302
            assert cancelled.headers["location"].startswith("/?auth_error=")


def test_fresh_install_requires_bootstrap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(studio_app.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)
    app = create_studio_app(
        redis_url="redis://studio-auth-test/0",
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
        entra_config=_config(tmp_path, admin_emails=(), admin_object_ids=()),
    )
    with pytest.raises(RuntimeError, match="no active administrator"):
        with TestClient(app):
            pass


@pytest.mark.asyncio
async def test_id_token_validation_and_jwks_refresh(tmp_path: Path) -> None:
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = signing_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "issuer-key",
        "use": "sig",
        "alg": "RS256",
        "n": _b64(public_numbers.n),
        "e": _b64(public_numbers.e),
    }
    jwks_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_calls
        assert request.url.path == "/jwks"
        jwks_calls += 1
        return httpx.Response(200, json={"keys": [jwk]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = StudioAuthService(
        config=_config(tmp_path),
        store=StudioAuthStore(fakeredis.aioredis.FakeRedis(decode_responses=True), prefix="studio:test-auth"),
        http_client=client,
    )
    try:
        preferred = await service._verify_id_token(
            _id_token(signing_key, omit=("email",), preferred_username="Fallback@Example.Test"),
            "nonce-1",
            "http://127.0.0.1:19091/jwks",
        )
        assert preferred["email"] == "Fallback@Example.Test"

        cases = [
            (_id_token(signing_key, iss="wrong"), "nonce-1", 400),
            (_id_token(signing_key, tid="tenant-2"), "nonce-1", 403),
            (_id_token(signing_key), "wrong-nonce", 400),
            (_id_token(signing_key, omit=("email",)), "nonce-1", 403),
            (_id_token(signing_key, omit=("exp",)), "nonce-1", 400),
            (_id_token(signing_key, kid="rotated-key"), "nonce-1", 400),
        ]
        for token, nonce, status_code in cases:
            with pytest.raises(HTTPException) as exc_info:
                await service._verify_id_token(token, nonce, "http://127.0.0.1:19091/jwks")
            assert exc_info.value.status_code == status_code
        assert jwks_calls == 2
    finally:
        await client.aclose()


def test_entra_config_requires_every_value(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="application_id"):
        _config(tmp_path, application_id="")
