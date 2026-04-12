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
