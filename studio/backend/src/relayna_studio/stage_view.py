from __future__ import annotations

from relayna.observability.stage_metrics import StageHealthSnapshot


def build_stage_view(snapshot: StageHealthSnapshot) -> dict[str, object]:
    return {
        "stage": snapshot.stage,
        "received": snapshot.received,
        "published": snapshot.published,
        "failed": snapshot.failed,
        "healthy": snapshot.failed == 0,
    }


__all__ = ["build_stage_view"]
