"""Deterministic provider/model fallback planning policies.

The policy decides *what may be tried*; transports remain responsible for
executing requests.  This separation prevents retry mechanics, credentials,
and provider wire formats from becoming one stateful conditional block.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


FallbackReason = Literal["rate_limit", "unavailable", "timeout", "model_error"]
DEFAULT_FALLBACK_REASONS: frozenset[FallbackReason] = frozenset(
    {"rate_limit", "unavailable", "timeout"}
)


@dataclass(frozen=True, slots=True)
class RouteTarget:
    provider: str
    model: str

    @property
    def identity(self) -> tuple[str, str]:
        return (self.provider.strip().lower().replace("_", "-"), self.model.strip())


@dataclass(frozen=True, slots=True)
class FallbackRule:
    target: RouteTarget
    reasons: frozenset[FallbackReason] = DEFAULT_FALLBACK_REASONS


@dataclass(frozen=True, slots=True)
class FallbackPlan:
    primary: RouteTarget
    rules: tuple[FallbackRule, ...] = ()

    def candidates(self, reason: FallbackReason) -> tuple[RouteTarget, ...]:
        seen: set[tuple[str, str]] = set()
        selected: list[RouteTarget] = []
        for target in (self.primary, *(rule.target for rule in self.rules if reason in rule.reasons)):
            if not target.provider.strip() or not target.model.strip() or target.identity in seen:
                continue
            seen.add(target.identity)
            selected.append(target)
        return tuple(selected)


def build_fallback_plan(
    provider: str,
    model: str,
    configured: Iterable[dict[str, object]] = (),
) -> FallbackPlan:
    """Parse a small configuration surface into an immutable routing plan."""

    rules: list[FallbackRule] = []
    valid_reasons = {"rate_limit", "unavailable", "timeout", "model_error"}
    for item in configured:
        target_provider = str(item.get("provider") or provider).strip()
        target_model = str(item.get("model") or "").strip()
        raw_reasons = item.get("reasons")
        values = raw_reasons if isinstance(raw_reasons, (list, tuple, set, frozenset)) else ()
        reasons = frozenset(str(value) for value in values if str(value) in valid_reasons)
        rules.append(
            FallbackRule(
                RouteTarget(target_provider, target_model),
                reasons=reasons or DEFAULT_FALLBACK_REASONS,
            )
        )
    return FallbackPlan(RouteTarget(provider, model), tuple(rules))
