from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import requires
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_validator(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/validate-release-metadata.py", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def test_sdk_release_metadata_is_semver() -> None:
    result = run_validator("--component", "sdk")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("release metadata ok: sdk-v")


def test_release_metadata_rejects_mismatched_tag() -> None:
    result = run_validator("sdk-v0.0.0")

    assert result.returncode != 0
    assert "does not match package version" in result.stderr


@pytest.fixture
def release_checkout(tmp_path: Path) -> Path:
    script = tmp_path / "scripts/validate-release-metadata.py"
    script.parent.mkdir()
    script.write_bytes((REPO_ROOT / "scripts/validate-release-metadata.py").read_bytes())
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.4.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "## 2.4.0 - Run\nCombined changes\n\n## SDK 2.4.0 - Run\nSDK changes\n\n## Studio 3.0.1 - Run\nStudio changes\n"
    )
    return tmp_path


def set_studio_versions(root: Path, backend: str, frontend: str, sdk_floor: str = "2.3.0") -> None:
    for directory in ("studio/backend", "apps/studio"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "studio/backend/pyproject.toml").write_text(
        f'[project]\nversion = "{backend}"\ndependencies = ["relayna>={sdk_floor}"]\n'
    )
    (root / "apps/studio/package.json").write_text(f'{{"version":"{frontend}"}}')


@pytest.mark.parametrize(
    ("tag", "studio", "notes", "sdk_assets", "studio_assets"),
    [
        ("sdk-v2.4.0", None, "SDK changes", True, False),
        ("studio-v3.0.1", "3.0.1", "Studio changes", False, True),
        ("v2.4.0", "2.4.0", "Combined changes", True, True),
    ],
)
def test_release_tag_selects_metadata_notes_and_assets(release_checkout, tag, studio, notes, sdk_assets, studio_assets):
    if studio is not None:
        set_studio_versions(release_checkout, studio, studio)
    output = release_checkout / "metadata"
    changelog = release_checkout / "release-notes.md"
    result = run_validator(tag, "--notes-out", str(changelog), "--github-output", str(output), cwd=release_checkout)
    assert result.returncode == 0, result.stderr
    assert changelog.read_text() == f"{notes}\n"
    settings = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert settings == {
        "sdk": str(sdk_assets).lower(),
        "studio": str(studio_assets).lower(),
        "version": tag.split("v")[-1],
    }

    workflow = yaml.load((REPO_ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["release"]["steps"]
    asset_step = next(step for step in steps if step.get("id") == "assets")
    asset_output = release_checkout / "assets"
    subprocess.run(
        ["bash", "-e", "-c", asset_step["run"]],
        env={
            **os.environ,
            "GITHUB_OUTPUT": str(asset_output),
            "RELEASE_TAG": tag,
            "RELEASE_SDK": settings["sdk"],
            "RELEASE_STUDIO": settings["studio"],
        },
        check=True,
    )
    assets = asset_output.read_text().splitlines()[1:-1]
    assert ("dist/*.whl" in assets) == sdk_assets
    assert ("studio-dist/*.whl" in assets) == (not sdk_assets)
    assert (f"relayna-studio-backend-{tag}.spdx.json" in assets) == studio_assets
    assert (f"relayna-studio-frontend-{tag}.spdx.json" in assets) == studio_assets


def test_studio_and_combined_tags_enforce_their_compatibility_boundaries(release_checkout):
    set_studio_versions(release_checkout, "3.0.1", "3.0.1")
    assert "release versions must match" in run_validator("v2.4.0", cwd=release_checkout).stderr
    set_studio_versions(release_checkout, "3.0.1", "3.0.2")
    assert "release versions must match" in run_validator("studio-v3.0.1", cwd=release_checkout).stderr
    set_studio_versions(release_checkout, "3.0.1", "3.0.1", sdk_floor="2.5.0")
    assert "requires SDK 2.5.0" in run_validator("studio-v3.0.1", cwd=release_checkout).stderr
    assert run_validator("sdk-v2.4.0", cwd=release_checkout).returncode == 0
    assert "invalid release tag" in run_validator("sdk-v02.4.0", cwd=release_checkout).stderr
    set_studio_versions(release_checkout, "3.1.0", "3.1.0")
    result = run_validator("studio-v3.1.0", "--notes-out", "release-notes.md", cwd=release_checkout)
    assert "Unable to find CHANGELOG section" in result.stderr


@pytest.mark.parametrize(
    ("package", "vulnerable", "patched"),
    [
        ("certifi", "2026.2.25", "2026.10.19"),
        ("multidict", "6.7.1", "6.9.1"),
    ],
)
def test_distribution_metadata_requires_patched_dependencies(package: str, vulnerable: str, patched: str) -> None:
    dependencies = {requirement.name: requirement for requirement in map(Requirement, requires("relayna") or [])}
    dependency = dependencies[package]

    assert dependency.marker is None
    assert vulnerable not in dependency.specifier
    assert patched in dependency.specifier
