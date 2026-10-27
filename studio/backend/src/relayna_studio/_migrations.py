from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

_logger = logging.getLogger(__name__)
_MIGRATION_LOCK_ID = 0x52454C41594E41  # "RELAYNA", shared with the Alembic CLI.


def _upgrade_connection(connection: Connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")).replace("%", "%%"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def _upgrade_database(engine: AsyncEngine) -> None:
    _logger.info("Waiting to apply Studio PostgreSQL migrations.")
    async with engine.begin() as connection:
        # Acquire before entering Alembic: its context proxies are process-global.
        # Waiting replicas must not inspect revisions until the holder commits.
        await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK_ID})
        await connection.run_sync(_upgrade_connection)
    _logger.info("Studio PostgreSQL migrations are up to date.")
