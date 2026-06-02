from __future__ import annotations

import importlib
import json
from pathlib import Path

FREEZE_DIR = Path(__file__).resolve().parent / "freeze"


def _load_manifest(name: str) -> dict[str, object]:
    return json.loads((FREEZE_DIR / name).read_text())


def test_studio_backend_public_exports_match_production_freeze_manifest() -> None:
    manifest = _load_manifest("public_surface.json")
    module = importlib.import_module(str(manifest["module"]))

    assert manifest["freeze_version"] == "v1.4.19"
    assert manifest["strict"] is True
    assert list(getattr(module, "__all__", [])) == manifest["exports"]


def test_studio_backend_feature_families_have_freeze_perimeter_tests() -> None:
    manifest = _load_manifest("feature_perimeter.json")
    root = Path(__file__).resolve().parents[3]

    for feature_name, test_paths in manifest["features"].items():
        missing = [path for path in test_paths if not (root / path).is_file()]
        assert not missing, f"{feature_name} perimeter references missing tests: {missing}"
