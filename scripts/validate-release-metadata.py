#!/usr/bin/env python3
"""Validate release version metadata across Relayna packages."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def read_pyproject_version(path: Path) -> str:
    with path.open("rb") as file:
        data = tomllib.load(file)
    version = data["project"]["version"]
    if not isinstance(version, str):
        raise TypeError(f"{path.relative_to(REPO_ROOT)} project.version must be a string")
    return version


def read_package_json_version(path: Path) -> str:
    data = json.loads(path.read_text())
    version = data["version"]
    if not isinstance(version, str):
        raise TypeError(f"{path.relative_to(REPO_ROOT)} version must be a string")
    return version


def validate_semver(name: str, version: str) -> None:
    if not SEMVER_RE.fullmatch(version):
        raise ValueError(f"{name} version {version!r} is not strict MAJOR.MINOR.PATCH SemVer")


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        raise SystemExit("usage: validate-release-metadata.py [vMAJOR.MINOR.PATCH]")

    expected_tag = argv[1] if len(argv) == 2 else None
    versions = {
        "sdk": read_pyproject_version(REPO_ROOT / "pyproject.toml"),
        "studio_backend": read_pyproject_version(
            REPO_ROOT / "studio/backend/pyproject.toml"
        ),
        "studio_frontend": read_package_json_version(
            REPO_ROOT / "apps/studio/package.json"
        ),
    }

    for name, version in versions.items():
        validate_semver(name, version)

    unique_versions = set(versions.values())
    if len(unique_versions) != 1:
        formatted = ", ".join(f"{name}={version}" for name, version in versions.items())
        raise ValueError(f"release versions must match: {formatted}")

    version = unique_versions.pop()
    if expected_tag is not None:
        expected_version = expected_tag.removeprefix("v")
        validate_semver("release tag", expected_version)
        if expected_version != version:
            raise ValueError(
                f"release tag {expected_tag!r} does not match package version {version!r}"
            )

    print(f"release metadata ok: v{version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except (KeyError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
