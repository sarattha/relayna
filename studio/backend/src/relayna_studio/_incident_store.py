"""Transactional incident state and manual grouping corrections."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ._incident_groups import _resolve
from .audit_context import current_actor_user_id
from .database import StudioDatabase, _append_audit, _incident_history, _incident_overrides, _incidents


def _state(row: Any) -> dict[str, Any]:
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in dict(row).items()}


class _IncidentStore:
    def __init__(self, database: StudioDatabase) -> None:
        self.database = database

    async def get(self, incident_id: str) -> dict[str, Any]:
        async with self.database.sessions() as session:
            row = (
                (await session.execute(select(_incidents).where(_incidents.c.incident_id == incident_id)))
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(404, "Incident not found.")
        return _state(row)

    async def snapshot(
        self, discoveries: dict[str, dict[str, str]], *, environment: str | None, service_id: str | None
    ) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, str], str]]:
        async with self.database.transaction() as session:
            # Keep identities and overrides consistent with one another under
            # READ COMMITTED, including a concurrent split inserting a new ID.
            await session.execute(text("SELECT pg_advisory_xact_lock(734291801)"))
            discoveries_in_order = sorted(discoveries.items())
            for start in range(0, len(discoveries_in_order), 500):
                await session.execute(
                    pg_insert(_incidents)
                    .values(
                        [
                            {
                                "incident_id": key,
                                "service_id": dims["service_id"],
                                "environment": dims["environment"],
                                "dimensions": dims,
                            }
                            for key, dims in discoveries_in_order[start : start + 500]
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=[_incidents.c.incident_id])
                )
            filters = []
            override_filters = []
            if environment:
                filters.append(_incidents.c.environment == environment)
                override_filters.append(_incident_overrides.c.environment == environment)
            if service_id:
                filters.append(_incidents.c.service_id == service_id)
                override_filters.append(_incident_overrides.c.service_id == service_id)
            states = (await session.execute(select(_incidents).where(*filters))).mappings().all()
            overrides = (await session.execute(select(_incident_overrides).where(*override_filters))).mappings().all()
        return (
            {row["incident_id"]: _state(row) for row in states},
            {(row["environment"], row["service_id"], row["failure_id"]): row["incident_id"] for row in overrides},
        )

    async def history(self, incident_id: str) -> list[dict[str, Any]]:
        async with self.database.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(_incident_history)
                        .where(_incident_history.c.incident_id == incident_id)
                        .order_by(_incident_history.c.id.desc())
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
        return [_state(row) for row in rows]

    async def _locked(self, session: AsyncSession, incident_id: str, revision: int) -> dict[str, Any]:
        # All correction/lifecycle writes share a short transaction lock. No
        # upstream I/O occurs under it; this also orders multi-row merges.
        await session.execute(text("SELECT pg_advisory_xact_lock(734291801)"))
        row = (
            (await session.execute(select(_incidents).where(_incidents.c.incident_id == incident_id)))
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "Incident not found.")
        if row["revision"] != revision or row["merged_into"]:
            raise HTTPException(409, "Incident changed. Refresh before applying this action.")
        return dict(row)

    async def _record(self, session: AsyncSession, incident_id: str, action: str, details: dict[str, Any]) -> None:
        await session.execute(
            insert(_incident_history).values(
                incident_id=incident_id,
                actor_user_id=current_actor_user_id(),
                action=action,
                details=details,
            )
        )
        await _append_audit(
            session, action=f"incident.{action}", target_type="incident", target_id=incident_id, details=details
        )

    async def change(self, incident_id: str, revision: int, changes: dict[str, Any], note: str) -> dict[str, Any]:
        async with self.database.transaction() as session:
            row = await self._locked(session, incident_id, revision)
            if not changes and not note.strip():
                raise HTTPException(422, "Change an owner or status, or supply a note.")
            before = {key: row[key] for key in changes}
            await session.execute(
                update(_incidents)
                .where(_incidents.c.incident_id == incident_id)
                .values(
                    **changes,
                    revision=revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            await self._record(
                session,
                incident_id,
                "updated",
                {"before": before, "after": changes, "note": note, "revision": revision + 1},
            )
        return {"incident_id": incident_id, "revision": revision + 1}

    async def merge(
        self, source_id: str, revision: int, target_id: str, target_revision: int, note: str
    ) -> dict[str, Any]:
        if source_id == target_id:
            raise HTTPException(422, "Choose a different incident to merge into.")
        async with self.database.transaction() as session:
            source = await self._locked(session, source_id, revision)
            target = await self._locked(session, target_id, target_revision)
            if (source["environment"], source["service_id"]) != (target["environment"], target["service_id"]):
                raise HTTPException(422, "Merge requires the same environment and service.")
            await session.execute(
                update(_incidents)
                .where(_incidents.c.incident_id == source_id)
                .values(
                    merged_into=target_id,
                    revision=revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.execute(
                update(_incidents)
                .where(_incidents.c.incident_id == target_id)
                .values(
                    revision=target_revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            details = {"source_id": source_id, "target_id": target_id, "note": note}
            await self._record(session, source_id, "merged", details)
            await self._record(session, target_id, "merged_from", details)
        return {"incident_id": target_id, "revision": target_revision + 1}

    async def split(
        self, incident_id: str, revision: int, failure_ids: list[str], observed: dict[str, str], note: str
    ) -> dict[str, Any]:
        new_id = uuid.uuid4().hex
        async with self.database.transaction() as session:
            source = await self._locked(session, incident_id, revision)
            scope = (_incidents.c.service_id == source["service_id"], _incidents.c.environment == source["environment"])
            rows = (await session.execute(select(_incidents).where(*scope))).mappings().all()
            states = {row["incident_id"]: dict(row) for row in rows}
            overrides = (
                (
                    await session.execute(
                        select(_incident_overrides).where(
                            _incident_overrides.c.service_id == source["service_id"],
                            _incident_overrides.c.environment == source["environment"],
                        )
                    )
                )
                .mappings()
                .all()
            )
            assignments = {row["failure_id"]: row["incident_id"] for row in overrides}
            members = {
                fid
                for fid, natural_id in observed.items()
                if _resolve(assignments.get(fid, natural_id), states) == incident_id
            }
            selected = set(failure_ids)
            if not selected or not selected < members:
                raise HTTPException(
                    409, "Select a proper subset of the currently observed failures; refresh and try again."
                )
            await session.execute(
                insert(_incidents).values(
                    incident_id=new_id,
                    service_id=source["service_id"],
                    environment=source["environment"],
                    dimensions=source["dimensions"],
                )
            )
            for fid in sorted(selected):
                statement = pg_insert(_incident_overrides).values(
                    service_id=source["service_id"],
                    environment=source["environment"],
                    failure_id=fid,
                    incident_id=new_id,
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            _incident_overrides.c.service_id,
                            _incident_overrides.c.environment,
                            _incident_overrides.c.failure_id,
                        ],
                        set_={"incident_id": new_id},
                    )
                )
            await session.execute(
                update(_incidents)
                .where(_incidents.c.incident_id == incident_id)
                .values(
                    revision=revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            details = {"source_id": incident_id, "target_id": new_id, "failure_ids": sorted(selected), "note": note}
            await self._record(session, incident_id, "split", details)
            await self._record(session, new_id, "split_from", details)
        return {"incident_id": new_id, "revision": 0}
