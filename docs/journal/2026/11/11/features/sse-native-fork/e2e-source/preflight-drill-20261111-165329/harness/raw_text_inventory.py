"""Exact source-substring inventory for host-injected main rows.

This does not use whole-message needles. It inventories every exact source
substring at least MIN_EXCERPT characters long that appears in each host-injected
wake/review field, with source message id, offset, length and destination field.

Allowed verbatim destinations:
- `wake.time_limit.evidence` (bounded evidence by product contract);
- `review.reply` (the fork-composed sent reply shown for main review).

Everything else is rejected. Any whole source message and the raw CloudEvent JSON
are always rejected, whatever the destination.
"""
from __future__ import annotations

import json
from typing import Any

MIN_EXCERPT = 24
ALLOWED_FIELDS = {"wake.time_limit.evidence", "review.reply"}


def longest_exact_matches(source: str, destination: str, minimum: int = MIN_EXCERPT) -> list[tuple[int, int, int]]:
    """Maximal exact matches as (source offset, destination offset, length).

    A match is emitted only at a left boundary where the same match does not
    extend one character left; it is then extended maximally right. Contained
    duplicates are removed.
    """
    matches: list[tuple[int, int, int]] = []
    if len(source) < minimum or len(destination) < minimum:
        return matches
    starts: dict[str, list[int]] = {}
    for i in range(len(source) - minimum + 1):
        starts.setdefault(source[i:i + minimum], []).append(i)
    for j in range(len(destination) - minimum + 1):
        for i in starts.get(destination[j:j + minimum], []):
            if i and j and source[i - 1] == destination[j - 1]:
                continue
            length = minimum
            while i + length < len(source) and j + length < len(destination) and source[i + length] == destination[j + length]:
                length += 1
            matches.append((i, j, length))
    maximal = []
    for item in sorted(set(matches), key=lambda x: (-x[2], x[0], x[1])):
        i, j, length = item
        if any(i >= a and i + length <= a + n and j >= b and j + length <= b + n for a, b, n in maximal):
            continue
        maximal.append(item)
    return sorted(maximal, key=lambda x: (x[1], x[0]))


def inventory_fields(
    source_messages: dict[str, str],
    destinations: dict[str, str],
    *,
    raw_event: str = "",
    minimum: int = MIN_EXCERPT,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for field, text in destinations.items():
        for message_id, source in source_messages.items():
            for source_offset, destination_offset, length in longest_exact_matches(source, text, minimum):
                rows.append({
                    "source_message_id": str(message_id),
                    "source_offset": source_offset,
                    "destination_field": field,
                    "destination_offset": destination_offset,
                    "length": length,
                    "sha256": __import__("hashlib").sha256(source[source_offset:source_offset + length].encode()).hexdigest(),
                    "allowed": field in ALLOWED_FIELDS,
                })
    whole_messages = [
        {"source_message_id": str(message_id), "destination_field": field}
        for field, text in destinations.items()
        for message_id, source in source_messages.items()
        if source and source in text
    ]
    raw_event_fields = [field for field, text in destinations.items() if raw_event and raw_event in text]
    disallowed = [row for row in rows if not row["allowed"]]
    return {
        "minimum_length": minimum,
        "allowed_fields": sorted(ALLOWED_FIELDS),
        "matches": rows,
        "disallowed_matches": disallowed,
        "whole_source_messages": whole_messages,
        "raw_event_fields": raw_event_fields,
        "passed": not disallowed and not whole_messages and not raw_event_fields,
    }


def wake_destinations(wake_text: str) -> dict[str, str]:
    """Parse one `Ciel side event requires main action: {…}` string."""
    marker = "Ciel side event requires main action: "
    start = wake_text.index(marker) + len(marker)
    value, _ = json.JSONDecoder().raw_decode(wake_text, start)
    time_limit = value.get("time_limit") if isinstance(value, dict) else None
    return {
        "wake.summary": str(value.get("summary") or ""),
        "wake.title": str(value.get("title") or ""),
        "wake.time_limit.text": str((time_limit or {}).get("text") or ""),
        "wake.time_limit.evidence": str((time_limit or {}).get("evidence") or ""),
    }


def review_destinations(review_row: dict[str, Any]) -> dict[str, str]:
    return {
        "review.summary": str(review_row.get("summary") or ""),
        "review.reply": str(review_row.get("reply") or ""),
        "review.note": str(review_row.get("note") or ""),
    }
