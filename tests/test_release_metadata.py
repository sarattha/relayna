from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_validator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/validate-release-metadata.py", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_metadata_versions_are_aligned_and_semver() -> None:
    result = run_validator()

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("release metadata ok: v")


def test_release_metadata_rejects_mismatched_tag() -> None:
    result = run_validator("v0.0.0")

    assert result.returncode != 0
    assert "does not match package version" in result.stderr
