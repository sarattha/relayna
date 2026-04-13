from __future__ import annotations

from .stage_metrics import compute_stage_health


def detect_stage_alerts(events: list[object], *, failure_threshold: int = 1) -> list[str]:
    alerts: list[str] = []
    for stage, snapshot in compute_stage_health(events).items():
        if snapshot.failed >= failure_threshold:
            alerts.append(f"stage '{stage}' has {snapshot.failed} failures")
    return alerts


__all__ = ["detect_stage_alerts"]
