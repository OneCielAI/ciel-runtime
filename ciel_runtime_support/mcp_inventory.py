"""Conflict-safe merging for MCP inventories owned by different CLI adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class McpInventoryMerge:
    servers: dict[str, dict[str, Any]]
    added: tuple[str, ...]
    duplicates: tuple[str, ...]


class McpInventoryService:
    """Merge server maps while preserving the target CLI's native definitions."""

    @staticmethod
    def names(*inventories: dict[str, Any]) -> set[str]:
        return {
            name.casefold()
            for inventory in inventories
            for raw_name in inventory
            if (name := str(raw_name or "").strip())
        }

    @classmethod
    def merge(
        cls,
        primary: dict[str, dict[str, Any]],
        recovered: dict[str, dict[str, Any]],
        *,
        reserved: set[str] | None = None,
    ) -> McpInventoryMerge:
        merged = dict(primary)
        occupied = cls.names(primary)
        occupied.update(
            str(name).strip().casefold()
            for name in (reserved or ())
            if str(name).strip()
        )
        added: list[str] = []
        duplicates: list[str] = []
        for raw_name, server in recovered.items():
            name = str(raw_name or "").strip()
            if not name or not isinstance(server, dict):
                continue
            identity = name.casefold()
            if identity in occupied:
                duplicates.append(name)
                continue
            merged[name] = dict(server)
            occupied.add(identity)
            added.append(name)
        return McpInventoryMerge(merged, tuple(added), tuple(duplicates))


__all__ = ["McpInventoryMerge", "McpInventoryService"]
