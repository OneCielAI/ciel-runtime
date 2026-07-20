#!/usr/bin/env python3
"""Validate repository documentation and release metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def local_markdown_links(path: Path) -> list[Path]:
    targets: list[Path] = []
    for raw in LINK_RE.findall(path.read_text(encoding="utf-8")):
        target = raw.strip().split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        targets.append((path.parent / target).resolve())
    return targets


def main() -> int:
    errors: list[str] = []
    readme = ROOT / "README.md"
    if not readme.exists() or not readme.read_text(encoding="utf-8").strip():
        errors.append("README.md must exist and contain installation guidance")

    markdown_files = [readme, *sorted((ROOT / "docs").glob("*.md"))]
    for markdown in markdown_files:
        for target in local_markdown_links(markdown):
            if not target.exists():
                errors.append(f"broken local link in {markdown.relative_to(ROOT)}: {target}")

    package_version = str(json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"])
    runtime_version_path = ROOT / "ciel_runtime_support" / "runtime_constants.py"
    runtime_text = runtime_version_path.read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', runtime_text, re.MULTILINE)
    runtime_version = match.group(1) if match else ""
    if package_version != runtime_version:
        errors.append(
            f"version mismatch: package.json={package_version} "
            f"{runtime_version_path.relative_to(ROOT)}={runtime_version or '<missing>'}"
        )

    module_map = (ROOT / "docs" / "Module-Map.md").read_text(encoding="utf-8")
    support_modules = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "ciel_runtime_support").rglob("*.py")
        if path.name != "__init__.py"
    )
    for module in support_modules:
        if module not in module_map:
            errors.append(f"docs/Module-Map.md does not mention {module}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Documentation metadata checks passed ({len(markdown_files)} Markdown files, {len(support_modules)} modules).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
