"""Compatibility repairs for replayed OpenAI Responses input items."""

from __future__ import annotations

import re
from typing import Any


# Every OpenAI Responses item ID observed in recorded sessions uses a short
# lowercase prefix followed by an underscore (``msg_``, ``rs_``, ``fc_``,
# ``fco_``, ``ctc_``, ``ctco_``, ``tso_``).  Identifiers minted by other
# providers are opaque base64 blobs with no such prefix.
NATIVE_RESPONSES_ITEM_ID = re.compile(r"^[a-z]+_")

OPENAI_RESPONSES_ITEM_ID_PREFIXES = {
    "message": "msg_",
    "reasoning": "rs_",
    "function_call": "fc_",
    "function_call_output": "fco_",
    "custom_tool_call": "ctc_",
    "custom_tool_call_output": "ctco_",
}


def _has_foreign_item_id(item_type: Any, item_id: str) -> bool:
    """Report whether ``item_id`` was minted by a provider other than OpenAI."""

    if not item_id:
        return False
    expected_prefix = OPENAI_RESPONSES_ITEM_ID_PREFIXES.get(str(item_type or ""))
    if expected_prefix:
        return not item_id.startswith(expected_prefix)
    # Item types this map does not name -- ``web_search_call``,
    # ``tool_search_call``, and whatever the Responses API adds next -- must
    # still not replay another provider's identifier.  Enumerating them one at
    # a time only surfaces the next unlisted type as the next failed turn, so
    # fall back to the shape shared by every native ID.
    return not NATIVE_RESPONSES_ITEM_ID.match(item_id)


def repair_replayed_response_items(body: dict[str, Any]) -> dict[str, Any]:
    """Repair provider response records that cannot be replayed by OpenAI.

    Some Responses-compatible providers emit reasoning items with message IDs
    (``msg_...``). Codex persists those output items and later replays them as
    input. The OpenAI Codex backend requires reasoning IDs to start with
    ``rs_`` and rejects the whole turn otherwise.

    A reasoning item carrying a foreign ID is dropped outright. Its
    ``encrypted_content`` is sealed to whichever provider issued it, so
    forwarding it only trades an invalid-ID rejection for an "encrypted content
    could not be verified" one, and a foreign summary is not authoritative
    conversation content either. Neither is needed to replay the visible
    conversation.

    Some providers also emit tool items with message IDs (``msg_``) while
    preserving the independent ``call_id`` used to pair calls with outputs.
    Validate IDs by Responses item type, falling back to the shape shared by
    every native ID for item types this module does not name, and remove only a
    foreign item ID; keep ``call_id``, the tool name, arguments, output, and
    message content so the transcript remains usable.
    """

    value = body.get("input")
    if not isinstance(value, list):
        return body

    changed = False
    repaired: list[Any] = []
    for value_item in value:
        if not isinstance(value_item, dict):
            repaired.append(value_item)
            continue
        item = value_item
        item_type = item.get("type")
        item_id = str(item.get("id") or "")
        invalid_item_id = _has_foreign_item_id(item_type, item_id)
        if item_type != "reasoning" and invalid_item_id:
            retained = dict(item)
            retained.pop("id", None)
            repaired.append(retained)
            changed = True
            continue
        if item_type != "reasoning":
            repaired.append(item)
            continue
        if not invalid_item_id:
            repaired.append(item)
            continue

        # Only the issuing provider can verify its own sealed reasoning, and a
        # foreign ID is the evidence that this item came from somewhere else.
        # Stripping the ID while keeping the ciphertext just moves the failure
        # from the ID check to the decryption step, so drop the whole item.
        changed = True

    if not changed:
        return body
    projected = dict(body)
    projected["input"] = repaired
    return projected


repair_replayed_reasoning_items = repair_replayed_response_items


__all__ = [
    "OPENAI_RESPONSES_ITEM_ID_PREFIXES",
    "repair_replayed_reasoning_items",
    "repair_replayed_response_items",
]
