from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "relayna"


def _iter_package_inits() -> list[Path]:
    return sorted(ROOT.glob("*/__init__.py"))


def test_package_all_names_exist_and_are_unique() -> None:
    for init_path in _iter_package_inits():
        package_name = f"relayna.{init_path.parent.name}"
        package = importlib.import_module(package_name)
        exported = getattr(package, "__all__", None)
        if exported is None:
            continue
        assert len(exported) == len(set(exported)), f"{package_name}.__all__ contains duplicates"
        for name in exported:
            assert hasattr(package, name), f"{package_name}.__all__ exports missing name {name!r}"


def test_init_reexports_match_module_all_when_present() -> None:
    for init_path in _iter_package_inits():
        package_dir = init_path.parent
        package_name = package_dir.name
        tree = ast.parse(init_path.read_text())
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level != 1 or node.module is None:
                continue
            module_path = package_dir / f"{node.module}.py"
            if not module_path.exists():
                continue

            module = importlib.import_module(f"relayna.{package_name}.{node.module}")
            module_all = getattr(module, "__all__", None)
            if module_all is None:
                continue

            for alias in node.names:
                if alias.name == "*":
                    continue
                assert alias.name in module_all, (
                    f"relayna.{package_name} re-exports {alias.name!r} from {node.module!r}, "
                    f"but relayna.{package_name}.{node.module}.__all__ does not include it"
                )
