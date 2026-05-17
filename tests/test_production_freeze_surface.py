from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

FREEZE_DIR = Path(__file__).resolve().parent / "freeze"


def _load_manifest(name: str) -> dict[str, object]:
    return json.loads((FREEZE_DIR / name).read_text())


def _stable_signature(obj: object) -> str:
    return str(inspect.signature(obj)).replace("typing.Any", "Any")


def test_sdk_public_exports_match_production_freeze_manifest() -> None:
    manifest = _load_manifest("public_surface.json")

    assert manifest["freeze_version"] == "v1.4.11"
    assert manifest["strict"] is True

    for module_name, expected_exports in manifest["modules"].items():
        module = importlib.import_module(module_name)
        assert list(getattr(module, "__all__", [])) == expected_exports, module_name


def test_selected_sdk_public_signatures_match_production_freeze_manifest() -> None:
    manifest = _load_manifest("public_surface.json")

    for qualified_name, expected_signature in manifest["signatures"].items():
        module_name, object_name = qualified_name.rsplit(".", 1)
        module = importlib.import_module(module_name)
        obj = getattr(module, object_name)
        assert _stable_signature(obj) == expected_signature.replace("typing.Any", "Any"), qualified_name


def test_feature_families_have_freeze_perimeter_tests() -> None:
    manifest = _load_manifest("feature_perimeter.json")
    root = Path(__file__).resolve().parents[1]

    for feature_name, test_paths in manifest["features"].items():
        missing = [path for path in test_paths if not (root / path).is_file()]
        assert not missing, f"{feature_name} perimeter references missing tests: {missing}"
