from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_frontend_nginx_template_preserves_single_origin_routing() -> None:
    template = (REPO_ROOT / "apps/studio/nginx/default.conf.template").read_text()

    assert "location /studio/" in template
    assert "proxy_pass http://${STUDIO_BACKEND_UPSTREAM};" in template
    assert "try_files $uri $uri/ /index.html;" in template


def test_dockerfiles_exist_for_backend_and_frontend_images() -> None:
    assert (REPO_ROOT / "studio/backend/Dockerfile").is_file()
    assert (REPO_ROOT / "apps/studio/Dockerfile").is_file()


def test_backend_image_and_compose_ship_migrations_and_hybrid_dependencies() -> None:
    dockerfile = (REPO_ROOT / "studio/backend/Dockerfile").read_text()
    compose = (REPO_ROOT / "studio/compose.yaml").read_text()
    assert "ALEMBIC_CONFIG=/app/studio-backend/alembic.ini" in dockerfile
    assert "COPY --from=build /app/studio/backend/src /app/studio-backend/src" in dockerfile
    assert "postgres:16-alpine" in compose
    assert "redis:7-alpine" in compose
    assert 'command: ["alembic", "upgrade", "head"]' in compose
    assert "condition: service_completed_successfully" in compose
    assert "http://127.0.0.1:8000/readyz" in compose


def test_postgresql_dependencies_remain_studio_backend_only() -> None:
    sdk_project = (REPO_ROOT / "pyproject.toml").read_text().lower()
    backend_project = (REPO_ROOT / "studio/backend/pyproject.toml").read_text().lower()
    for dependency in ("asyncpg", "sqlalchemy", "alembic"):
        assert dependency not in sdk_project
        assert dependency in backend_project


def test_backend_docker_documentation_mounts_local_oidc_credentials() -> None:
    documentation = (REPO_ROOT / "docs/studio-backend.md").read_text()

    assert "docker run --rm --network host" in documentation
    assert "dst=/run/secrets/relayna-studio-private-key.pem,readonly" in documentation
    assert "dst=/run/secrets/relayna-studio-certificate.pem,readonly" in documentation
    assert "RELAYNA_STUDIO_ENTRA_OIDC_PRIVATE_KEY_PATH=/run/secrets/relayna-studio-private-key.pem" in documentation
    assert "RELAYNA_STUDIO_ENTRA_OIDC_CERTIFICATE_PATH=/run/secrets/relayna-studio-certificate.pem" in documentation
