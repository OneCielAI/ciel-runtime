"""A model pushed to override what running sessions request (remote management).

A running session names its model in every request (the launch alias
``ciel-runtime-<provider>-<model>``) and the router honours it, so changing
``current_model`` reaches new launches only.  ``forced_model`` in a provider's
configuration makes the router send that model for every request of that
provider instead; choosing a model locally (menu, CLI) releases it.
"""

from __future__ import annotations

from typing import Any

FORCED_MODEL_KEY = "forced_model"


def forced_model(provider_config: Any) -> str:
    if not isinstance(provider_config, dict):
        return ""
    return str(provider_config.get(FORCED_MODEL_KEY) or "").strip()


def with_forced_model(body: dict[str, Any], provider_config: Any) -> dict[str, Any]:
    """The request body with the forced model, or the body itself when none is set."""

    model = forced_model(provider_config)
    if not model or body.get("model") == model:
        return body
    return {**body, "model": model}


__all__ = ["FORCED_MODEL_KEY", "forced_model", "with_forced_model"]
