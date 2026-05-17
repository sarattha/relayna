from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = Path(__file__).resolve().parent / "freeze"
ROUTE_SOURCE_FILES = [
    *sorted((ROOT / "src" / "relayna" / "api").glob("*.py")),
    ROOT / "src" / "relayna" / "metrics.py",
]


def _route_declarations(path: Path) -> list[dict[str, str]]:
    tree = ast.parse(path.read_text())
    declarations: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "router":
                continue
            if func.attr not in {"delete", "get", "patch", "post", "put"}:
                continue
            if not decorator.args:
                continue
            declarations.append(
                {
                    "file": str(path.relative_to(ROOT)),
                    "method": func.attr,
                    "path_expression": ast.unparse(decorator.args[0]),
                }
            )
    return declarations


def test_sdk_route_declarations_match_production_freeze_manifest() -> None:
    manifest = json.loads((FREEZE_DIR / "routes.json").read_text())
    expected = manifest["sdk_route_declarations"]

    actual: list[dict[str, str]] = []
    for path in ROUTE_SOURCE_FILES:
        actual.extend(_route_declarations(path))

    assert actual == expected
