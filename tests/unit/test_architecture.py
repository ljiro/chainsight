"""Enforce package dependency rules (docs/architecture/system-design.md §3.1)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PACKAGES = {
    "core": ROOT / "core",
    "database": ROOT / "database",
    "integrations": ROOT / "integrations",
    "services": ROOT / "services",
    "ml": ROOT / "ml",
    "chainsight_api": ROOT / "apps" / "api" / "chainsight_api",
}

ALLOWED: dict[str, set[str]] = {
    "core": set(),
    "database": {"core"},
    "integrations": {"core"},
    "ml": {"core", "database"},
    "services": {"core", "database", "integrations", "ml"},
    "chainsight_api": {"core", "database", "services", "ml"},
}


def _internal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            top = name.split(".")[0]
            if top in PACKAGES:
                found.add(top)
    return found


@pytest.mark.parametrize("package", sorted(PACKAGES))
def test_package_respects_dependency_rules(package: str) -> None:
    violations: list[str] = []
    for file in PACKAGES[package].rglob("*.py"):
        for imported in _internal_imports(file) - {package}:
            if imported not in ALLOWED[package]:
                violations.append(f"{file.relative_to(ROOT)} imports {imported}")
    assert not violations, "\n".join(violations)
