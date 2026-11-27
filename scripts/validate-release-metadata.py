#!/usr/bin/env python3
"""Validate release version metadata across Relayna packages."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SEMVER = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
TAG_RE = re.compile(rf"(?:(?P<component>sdk|studio)-)?v(?P<version>{SEMVER})")


def read_project(path: str) -> dict:
    with (REPO_ROOT / path).open("rb") as file:
        return tomllib.load(file)["project"]


def validate_semver(name: str, version: str) -> str:
    if not isinstance(version, str) or not re.fullmatch(SEMVER, version):
        raise ValueError(f"{name} version {version!r} is not strict MAJOR.MINOR.PATCH SemVer")
    return version


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(map(int, version.split(".")))


def validate_versions(component: str) -> str:
    sdk = validate_semver("sdk", read_project("pyproject.toml")["version"])
    if component == "sdk":
        return sdk

    backend = read_project("studio/backend/pyproject.toml")
    versions = {
        "studio_backend": validate_semver("studio_backend", backend["version"]),
        "studio_frontend": validate_semver(
            "studio_frontend", json.loads((REPO_ROOT / "apps/studio/package.json").read_text())["version"]
        ),
    }
    if component == "combined":
        versions["sdk"] = sdk
    if len(set(versions.values())) != 1:
        formatted = ", ".join(f"{name}={version}" for name, version in versions.items())
        raise ValueError(f"release versions must match: {formatted}")

    floors = [
        match["version"]
        for dependency in backend["dependencies"]
        if (match := re.fullmatch(rf"relayna\s*>=\s*(?P<version>{SEMVER})", dependency, re.IGNORECASE))
    ]
    if len(floors) != 1:
        raise ValueError("Studio requires an explicit 'relayna>=MAJOR.MINOR.PATCH' dependency")
    if version_tuple(sdk) < version_tuple(floors[0]):
        raise ValueError(f"Studio requires SDK {floors[0]} or newer, but the bundled SDK is {sdk}")
    return versions["studio_backend"]


def write_release_notes(component: str, version: str, destination: Path) -> None:
    prefix = {"combined": "", "sdk": "SDK ", "studio": "Studio "}[component]
    heading = f"## {prefix}{version} -"
    lines: list[str] = []
    capture = False
    for line in (REPO_ROOT / "CHANGELOG.md").read_text().splitlines():
        if line.startswith("## "):
            if capture:
                break
            capture = line.startswith(heading)
        elif capture:
            lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        raise ValueError(f"Unable to find CHANGELOG section for {prefix}{version}")
    destination.write_text(text + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?", help="vX.Y.Z, sdk-vX.Y.Z, or studio-vX.Y.Z")
    parser.add_argument("--component", choices=["combined", "sdk", "studio"])
    parser.add_argument("--notes-out", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    component = args.component or "combined"
    expected_version = None
    if args.tag is not None:
        tag = TAG_RE.fullmatch(args.tag)
        if tag is None:
            raise ValueError(f"invalid release tag {args.tag!r}; expected vX.Y.Z, sdk-vX.Y.Z, or studio-vX.Y.Z")
        expected_version = tag["version"]
        component = tag["component"] or "combined"
        if args.component and args.component != component:
            raise ValueError(f"release tag {args.tag!r} selects {component}, not {args.component}")
    version = validate_versions(component)
    if expected_version is not None and expected_version != version:
        raise ValueError(f"release tag {args.tag!r} does not match package version {version!r}")

    if args.notes_out:
        write_release_notes(component, version, args.notes_out)
    if args.github_output:
        with args.github_output.open("a") as output:
            print(f"version={version}", file=output)
            print(f"sdk={'false' if component == 'studio' else 'true'}", file=output)
            print(f"studio={'false' if component == 'sdk' else 'true'}", file=output)
    print(f"release metadata ok: {'v' if component == 'combined' else f'{component}-v'}{version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
