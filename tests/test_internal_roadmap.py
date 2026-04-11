from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROADMAP = ROOT / "internal" / "studio-control-plane-roadmap.md"
MKDOCS = ROOT / "mkdocs.yml"
ALLOWED_STATUSES = {
    "proposed",
    "planned",
    "in_progress",
    "partially_implemented",
    "implemented",
    "deferred",
}
FEATURE_HEADINGS = [
    "## 1. Service Registry",
    "## 2. Capability Discovery And Version Handshake",
    "## 3. Federated API Aggregation Layer",
    "## 4. Cross-Service Identity Model",
    "## 5. Aggregated Event And Observation Ingestion",
    "## 6. Log Pipeline",
    "## 7. Control-Plane UI Expansion",
    "## 8. Auth, Trust, And Operator Controls",
    "## 9. Health And Liveness Model",
    "## 10. Search And Retention",
    "## 11. Studio Deployment Packaging",
]


def test_internal_roadmap_exists_at_private_path() -> None:
    assert ROADMAP.exists()
    assert ROADMAP.parent.name == "internal"
    assert "docs" not in ROADMAP.parts


def test_mkdocs_does_not_reference_internal_roadmap() -> None:
    text = MKDOCS.read_text()
    assert "internal/" not in text
    assert "studio-control-plane-roadmap.md" not in text


def test_internal_roadmap_has_required_sections_and_feature_headings() -> None:
    text = ROADMAP.read_text()
    assert text.startswith("# Relayna Studio Control-Plane Roadmap")
    assert "## Status Summary" in text
    assert "## Defaults And Assumptions" in text
    assert "## Update Policy" in text
    assert "## Change Log" in text
    for heading in FEATURE_HEADINGS:
        assert heading in text


def test_internal_roadmap_uses_only_allowed_status_values() -> None:
    text = ROADMAP.read_text()
    feature_statuses = re.findall(r"^- Status: ([a-z_]+)$", text, flags=re.MULTILINE)
    assert len(feature_statuses) == 11
    assert set(feature_statuses) <= ALLOWED_STATUSES


def test_internal_roadmap_has_status_summary_table_for_all_features() -> None:
    text = ROADMAP.read_text()
    table_rows = re.findall(
        r"^\| (\d+) \| (.+?) \| ([a-z_]+) \| (\d{4}-\d{2}-\d{2}) \|$",
        text,
        flags=re.MULTILINE,
    )
    assert len(table_rows) == 11
    assert {status for _, _, status, _ in table_rows} <= ALLOWED_STATUSES
