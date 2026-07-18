"""Small typed registries used by runtime architecture extension points."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Generic, TypeVar


T = TypeVar("T")


class AdapterRegistry(Generic[T]):
    """Map stable names and aliases to adapter factories."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., T]] = {}

    def register(self, name: str, factory: Callable[..., T], *, aliases: Iterable[str] = ()) -> None:
        keys = tuple(dict.fromkeys(self._normalize(key) for key in (name, *aliases)))
        for normalized in keys:
            if not normalized:
                raise ValueError("adapter name cannot be empty")
            if normalized in self._factories:
                raise ValueError(f"adapter already registered: {normalized}")
            self._factories[normalized] = factory

    def create(self, name: str, /, **kwargs: Any) -> T:
        normalized = self._normalize(name)
        try:
            factory = self._factories[normalized]
        except KeyError as exc:
            raise KeyError(f"unknown adapter: {name}") from exc
        return factory(**kwargs)

    def contains(self, name: str) -> bool:
        return self._normalize(name) in self._factories

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    @staticmethod
    def _normalize(name: str) -> str:
        return str(name or "").strip().lower().replace("_", "-")


__all__ = ["AdapterRegistry"]
