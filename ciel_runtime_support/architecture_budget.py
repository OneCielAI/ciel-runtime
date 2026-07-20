"""Enforce the bounded-context migration's monotonically decreasing budget."""

from __future__ import annotations

from pathlib import Path


FINAL_FILE_LINE_BUDGET = 4_999
MAIN_FILE_LINE_BUDGET = 18_888


def production_python_files(root: Path) -> tuple[Path, ...]:
    support = tuple(sorted((root / "ciel_runtime_support").rglob("*.py")))
    entrypoints = (
        root / "ciel_runtime.py",
        root / "ciel-runtime-menu.py",
        root / "ciel-runtime-tool-guard.py",
    )
    return tuple(path for path in (*entrypoints, *support) if path.is_file())


def physical_line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def architecture_budget_violations(root: Path) -> tuple[str, ...]:
    violations: list[str] = []
    main = root / "ciel_runtime.py"
    main_lines = physical_line_count(main)
    if main_lines > MAIN_FILE_LINE_BUDGET:
        violations.append(
            f"ciel_runtime.py has {main_lines} lines; ratchet budget is {MAIN_FILE_LINE_BUDGET}"
        )
    for path in production_python_files(root):
        if path == main:
            continue
        lines = physical_line_count(path)
        if lines > FINAL_FILE_LINE_BUDGET:
            violations.append(
                f"{path.relative_to(root)} has {lines} lines; file budget is {FINAL_FILE_LINE_BUDGET}"
            )
    return tuple(violations)
