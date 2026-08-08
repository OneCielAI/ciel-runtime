"""Compatibility repairs for replayed OpenAI Responses input items."""

from __future__ import annotations

from typing import Any


OPENAI_RESPONSES_ITEM_ID_PREFIXES = {
    "message": "msg_",
    "reasoning": "rs_",
    "function_call": "fc_",
    "function_call_output": "fco_",
    "custom_tool_call": "ctc_",
    "custom_tool_call_output": "ctco_",
}


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
    Validate IDs by Responses item type and remove only a mismatched item ID;
    keep ``call_id``, the tool name, arguments, output, and message content so
    the transcript remains usable.
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
        expected_prefix = OPENAI_RESPONSES_ITEM_ID_PREFIXES.get(str(item_type or ""))
        invalid_item_id = bool(
            item_id and expected_prefix and not item_id.startswith(expected_prefix)
        )
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
