from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from relayna_studio import _migrations
from relayna_studio import app as app_module
from relayna_studio.app import create_studio_app
from relayna_studio.database import EXPECTED_SCHEMA_REVISION, metadata
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


@pytest.mark.asyncio
@pytest.mark.parametrize("migration_fails", [False, True])
async def test_lifespan_migrates_before_initialization_and_cleans_up_on_failure(monkeypatch, migration_fails):
    runtimes = []
    ensure_runtime = app_module._StudioLifespan.ensure_runtime

    def capture_runtime(lifespan):
        runtime = ensure_runtime(lifespan)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(app_module._StudioLifespan, "ensure_runtime", capture_runtime)
    app = create_studio_app(
        redis_url="redis://unused/0",
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
    )
    runtime = runtimes[0]
    calls: list[str] = []

    async def record(name):
        calls.append(name)

    async def migrate(engine):
        assert engine is database.engine
        await record("migrate")
        if migration_fails:
            raise RuntimeError("migration failed")

    database = SimpleNamespace(
        engine=object(),
        check_ready=AsyncMock(side_effect=lambda: calls.append("database")),
        check_schema=AsyncMock(side_effect=lambda: calls.append("schema")),
        dispose=AsyncMock(),
    )
    runtime.database = database
    runtime.redis = AsyncMock()
    runtime.redis.ping.side_effect = lambda: calls.append("redis")
    runtime.auth_service = SimpleNamespace(initialize=AsyncMock(side_effect=lambda: calls.append("auth")))
    runtime.http_client = AsyncMock()
    monkeypatch.setattr(app_module, "_upgrade_database", migrate)

    async def run():
        async with app.router.lifespan_context(app):
            calls.append("serving")

    if migration_fails:
        with pytest.raises(RuntimeError, match="migration failed"):
            await run()
        assert calls == ["database", "migrate"]
    else:
        await run()
        assert calls == ["database", "migrate", "schema", "redis", "auth", "serving"]
    database.dispose.assert_awaited_once()
    runtime.redis.aclose.assert_awaited_once()
    runtime.http_client.aclose.assert_awaited_once()


def test_bundled_migration_head_matches_schema_readiness():
    config = Config()
    config.set_main_option("script_location", str(Path(_migrations.__file__).with_name("migrations")))
    assert ScriptDirectory.from_config(config).get_heads() == [EXPECTED_SCHEMA_REVISION]


@pytest_asyncio.fixture
async def migration_engine() -> AsyncIterator[AsyncEngine]:
    database_url = os.getenv("RELAYNA_STUDIO_TEST_DATABASE_URL", "")
    if not database_url:
        pytest.skip("Set RELAYNA_STUDIO_TEST_DATABASE_URL for PostgreSQL migration tests.")
    url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    admin = create_async_engine(url)
    schema = f"migration_test_{uuid4().hex}"
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_concurrent_fresh_startups_and_restarts_are_idempotent(migration_engine, monkeypatch, tmp_path):
    # Startup uses the engine and installed scripts, not cwd, alembic.ini, or an
    # ambient database URL that might point to a different database.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELAYNA_STUDIO_DATABASE_URL", "postgresql://invalid/unused")
    await asyncio.gather(*(_migrations._upgrade_database(migration_engine) for _ in range(3)))
    await _migrations._upgrade_database(migration_engine)
    async with migration_engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == EXPECTED_SCHEMA_REVISION
        tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        assert set(metadata.tables) <= set(tables)


@pytest.mark.asyncio
async def test_upgrade_preserves_existing_records(migration_engine):
    def initial_revision(connection):
        config = Config()
        config.set_main_option("script_location", str(Path(_migrations.__file__).with_name("migrations")))
        config.attributes["connection"] = connection
        command.upgrade(config, "0001_studio_postgres")

    async with migration_engine.begin() as connection:
        await connection.run_sync(initial_revision)
        await connection.execute(
            text(
                "INSERT INTO studio_operator_settings (setting_key, value) "
                "VALUES ('migration-test', CAST(:value AS jsonb))"
            ),
            {"value": '{"keep":true}'},
        )
    await _migrations._upgrade_database(migration_engine)
    async with migration_engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == EXPECTED_SCHEMA_REVISION
        assert await connection.scalar(
            text("SELECT value FROM studio_operator_settings WHERE setting_key = 'migration-test'")
        ) == {"keep": True}


@pytest.mark.asyncio
async def test_failed_upgrade_rolls_back_and_releases_lock_for_retry(migration_engine, monkeypatch):
    upgrade = _migrations._upgrade_connection

    def fail_after_upgrade(connection):
        upgrade(connection)
        raise RuntimeError("synthetic upgrade failure")

    with monkeypatch.context() as patch:
        patch.setattr(_migrations, "_upgrade_connection", fail_after_upgrade)
        with pytest.raises(RuntimeError, match="synthetic upgrade failure"):
            await _migrations._upgrade_database(migration_engine)
    async with migration_engine.connect() as connection:
        assert await connection.run_sync(lambda conn: inspect(conn).get_table_names()) == []
    # A leaked session advisory lock would block a different connection.
    retry_engine = create_async_engine(
        migration_engine.url,
        connect_args={"server_settings": {"search_path": await _schema_name(migration_engine)}},
    )
    try:
        await asyncio.wait_for(_migrations._upgrade_database(retry_engine), timeout=10)
    finally:
        await retry_engine.dispose()


async def _schema_name(engine):
    async with engine.connect() as connection:
        return await connection.scalar(text("SELECT current_schema()"))


@pytest.mark.asyncio
async def test_unknown_revision_fails_without_changing_history(migration_engine):
    await _migrations._upgrade_database(migration_engine)
    async with migration_engine.begin() as connection:
        await connection.execute(text("UPDATE alembic_version SET version_num = 'future_revision'"))
    with pytest.raises(CommandError, match="future_revision"):
        await _migrations._upgrade_database(migration_engine)
    async with migration_engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "future_revision"
