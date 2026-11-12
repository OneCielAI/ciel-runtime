"""Exact source-substring inventory for host-injected main rows (team-lead ruling 2026-11-11).

Every exact source substring of at least MIN_EXCERPT characters is recorded with
source id, source offset, destination field, destination offset, length and sha256.

Bounded verbatim excerpts are allowed only in these fields (product bounds, JS
UTF-16 code units after the product clip):
- wake.summary              1000  (side-intake.js wakeText; register.js handoffView)
- wake.time_limit.text      1000
- wake.time_limit.evidence  2000
- review.summary             300  (register.js sideReviewText)
- review.reply              1200

The inventory fails on:
- a whole source message, the raw CloudEvent or its data payload anywhere in a
  host-injected block, allowed fields included;
- an excerpt of MIN_EXCERPT or more characters in any field not on the list
  (title, question, waits, queue, source, note, the text around the JSON payload,
  request-only context, or anything else);
- an allowed field longer than its product bound;
- a host-injected row that is not identified as a wake or review row.

Main's own tool reads (tool results, its own assistant text) are not host
injection. They are reported as observations and never fail the inventory.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

MIN_EXCERPT = 24
FIELD_BOUNDS = {
    "wake.summary": 1000,
    "wake.time_limit.text": 1000,
    "wake.time_limit.evidence": 2000,
    "review.summary": 300,
    "review.reply": 1200,
}
ALLOWED_FIELDS = frozenset(FIELD_BOUNDS)
WAKE_MARKER = "Ciel side event requires main action: "
REVIEW_MARKER = "Review side answers at this safe boundary"
STOP_WAKE_MARKERS = (
    "Side high-priority advice requires coordinator acknowledgment",
    "Side event delivery to main is uncertain",
    "Your previous work_side_handoff conflicted with a Work change",
)
ADVICE_DATA = "Advice data: "
STOP_FEEDBACK = "Stop hook feedback:"
# Exact product expressions that define the bounds above. A product tree that no
# longer contains every one of them changes the bound, and the run must not start.
BOUND_EXPRESSIONS = {
    "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js": (
        "summary:line(wake.summary,1000)",
        "text:line(wake.time_limit.text,1000)",
        "evidence:line(wake.time_limit.evidence,2000)",
        "return text.length <= limit ? text : text.slice(0, limit - 1) + '…'",
    ),
    "ciel_runtime_support/claude_mods/ciel-work/hooks/register.js": (
        "summary:clipped(h.summary, 1000)",
        "text:clipped(h.time_limit.text, 1000), evidence:clipped(h.time_limit.evidence, 2000)",
        "summary:clipped(r.summary,300),reply:clipped(r.reply,1200)",
        "return text.length <= limit ? text : text.slice(0, limit - 1) + '…'",
    ),
}
_INSTANCE_SUFFIX = re.compile(r"#\d+$")
_SPACE = re.compile(r"\s+")


def policy_field(field: str) -> str:
    """Remove only the multi-view suffix (`#N`) that the inventory adds."""
    return _INSTANCE_SUFFIX.sub("", str(field))


def is_allowed_destination(field: str) -> bool:
    return policy_field(field) in ALLOWED_FIELDS


def js_length(text: str) -> int:
    """String length as JavaScript counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def verify_product_bounds(product_root: Path) -> dict[str, Any]:
    missing = []
    for relative, expressions in BOUND_EXPRESSIONS.items():
        path = Path(product_root) / relative
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        missing.extend({"file": relative, "expression": expr} for expr in expressions if expr not in text)
    return {"product_root": str(product_root), "missing": missing, "passed": not missing}


def longest_exact_matches(source: str, destination: str, minimum: int = MIN_EXCERPT) -> list[tuple[int, int, int]]:
    """Maximal exact matches as (source offset, destination offset, length)."""
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


def event_payloads(raw_events: Iterable[str]) -> tuple[dict[str, str], list[dict[str, str]]]:
    """(event text sources, forbidden whole payloads) for each raw CloudEvent string."""
    sources: dict[str, str] = {}
    forbidden: list[dict[str, str]] = []
    for raw in raw_events or ():
        if not raw:
            continue
        label = "event:" + hashlib.sha256(raw.encode()).hexdigest()[:12]
        forbidden.append({"id": label + ":raw", "text": raw})
        try:
            body = json.loads(raw)
        except ValueError:
            continue
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            forbidden.append({"id": label + ":data", "text": json.dumps(data, ensure_ascii=False, separators=(",", ":"))})
            if isinstance(data.get("text"), str) and data["text"]:
                sources[label + ":text"] = data["text"]
    return sources, forbidden


def _norm(text: str) -> str:
    return _SPACE.sub(" ", str(text)).strip()


def inventory_fields(
    source_messages: dict[str, str],
    destinations: dict[str, str],
    *,
    raw_event: str = "",
    raw_events: Iterable[str] = (),
    minimum: int = MIN_EXCERPT,
) -> dict[str, Any]:
    events = [e for e in ([raw_event] if raw_event else []) + list(raw_events or ()) if e]
    event_sources, forbidden = event_payloads(events)
    sources = {**{str(k): v for k, v in source_messages.items()}, **event_sources}
    rows: list[dict[str, Any]] = []
    for field, value in destinations.items():
        text = str(value or "")
        for message_id, source in sources.items():
            for source_offset, destination_offset, length in longest_exact_matches(source, text, minimum):
                rows.append({
                    "source_message_id": message_id,
                    "source_offset": source_offset,
                    "destination_field": field,
                    "destination_offset": destination_offset,
                    "length": length,
                    "sha256": hashlib.sha256(source[source_offset:source_offset + length].encode()).hexdigest(),
                    "allowed": is_allowed_destination(field),
                })
    whole_messages = [
        {"source_message_id": message_id, "destination_field": field}
        for field, value in destinations.items()
        for message_id, source in sources.items()
        if source and (source in str(value or "") or _norm(source) in _norm(str(value or "")))
    ]
    raw_event_fields = [
        {"payload": item["id"], "destination_field": field}
        for field, value in destinations.items()
        for item in forbidden
        if item["text"] in str(value or "")
    ]
    disallowed = [row for row in rows if not row["allowed"]]
    bounds_exceeded = [
        {"destination_field": field, "length": js_length(str(value or "")), "bound": FIELD_BOUNDS[policy_field(field)]}
        for field, value in destinations.items()
        if policy_field(field) in FIELD_BOUNDS and js_length(str(value or "")) > FIELD_BOUNDS[policy_field(field)]
    ]
    return {
        "minimum_length": minimum,
        "allowed_fields": sorted(ALLOWED_FIELDS),
        "field_bounds": dict(FIELD_BOUNDS),
        "matches": rows,
        "disallowed_matches": disallowed,
        "whole_source_messages": whole_messages,
        "raw_event_fields": raw_event_fields,
        "bounds_exceeded": bounds_exceeded,
        "passed": not disallowed and not whole_messages and not raw_event_fields and not bounds_exceeded,
    }


def assess_recorded_matches(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Re-evaluate saved inventory rows under the current destination policy."""
    rows = [{**row, "allowed": is_allowed_destination(str(row.get("destination_field") or ""))} for row in matches]
    return {"matches": rows, "disallowed_matches": [row for row in rows if not row["allowed"]]}


def _scalar_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value) if isinstance(value, (int, float)) else json.dumps(value, ensure_ascii=False)


def flatten_fields(value: Any, prefix: str) -> dict[str, str]:
    """Every leaf becomes a field, so no unlisted payload field escapes the inventory."""
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            out.update(flatten_fields(child, prefix + "." + str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            out.update(flatten_fields(child, prefix + "[" + str(index) + "]"))
    else:
        out[prefix] = _scalar_text(value)
    return out


def _views_fields(views: list[Any], prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for n, view in enumerate(views):
        if not isinstance(view, dict):
            raise ValueError(prefix + " view is not an object")
        for key, value in flatten_fields(view, prefix).items():
            out[key if n == 0 else key + "#" + str(n)] = value
    return out


def wake_destinations(wake_text: str) -> dict[str, str]:
    start = wake_text.index(WAKE_MARKER) + len(WAKE_MARKER)
    value, _ = json.JSONDecoder().raw_decode(wake_text, start)
    if not isinstance(value, dict):
        raise ValueError("wake payload is not an object")
    return flatten_fields(value, "wake")


def review_destinations(review_row: dict[str, Any]) -> dict[str, str]:
    if not isinstance(review_row, dict):
        raise ValueError("review row is not an object")
    return flatten_fields(review_row, "review")


def structured_destinations(block: str, prose_field: str) -> tuple[dict[str, str], dict[str, int]]:
    """Every wake/review payload in one host block, plus all text around the payloads.

    Wake payloads: the wakeText object after WAKE_MARKER, and handoffView lists
    (objects with event_id) from the Stop advice data or the prompt.submit context.
    Review payloads: sideReviewText lists (objects with receipt_id). Everything
    else (instructions, the Work summary, headings) is `prose_field`, which is not
    an allowed destination. Raises ValueError when a wake marker has no object.
    """
    decoder = json.JSONDecoder()
    spans: list[tuple[int, int]] = []
    wake_views: list[Any] = []
    review_views: list[Any] = []
    if WAKE_MARKER in block:
        start = block.index(WAKE_MARKER) + len(WAKE_MARKER)
        value, end = decoder.raw_decode(block, start)
        if not isinstance(value, dict):
            raise ValueError("wake payload is not an object")
        wake_views.append(value)
        spans.append((start, end))
    index = 0
    while True:
        index = block.find("[{", index)
        if index < 0:
            break
        if any(a <= index < b for a, b in spans):
            index += 1
            continue
        try:
            value, end = decoder.raw_decode(block, index)
        except ValueError:
            index += 1
            continue
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            if all("receipt_id" in v for v in value):
                review_views.extend(value)
                spans.append((index, end))
                index = end
                continue
            if all("event_id" in v for v in value):
                wake_views.extend(value)
                spans.append((index, end))
                index = end
                continue
        index += 1
    fields = {**_views_fields(wake_views, "wake"), **_views_fields(review_views, "review")}
    prose, last = [], 0
    for start, end in sorted(spans):
        prose.append(block[last:start])
        last = end
    prose.append(block[last:])
    fields[prose_field] = "".join(prose)
    return fields, {"wake_views": len(wake_views), "review_views": len(review_views)}


def host_block_destinations(kind: str, block: str) -> dict[str, str]:
    """Fields of one identified host row; it must carry a payload of its kind."""
    if kind not in ("wake", "review"):
        raise ValueError("host-injected block is not an identified wake or review row")
    fields, counts = structured_destinations(block, kind + ".prose")
    if not counts[kind + "_views"]:
        raise ValueError(kind + " row has no " + kind + " payload")
    return fields


def host_injection_kind(row: dict[str, Any], text: str) -> str | None:
    """Classify one main user text block.

    None means not host-injected (a human prompt, or another Claude Code row with no
    host marker). A ciel-work plugin prompt, Stop-hook feedback, or any block that
    carries a side marker is host-injected and must be a wake or review row.
    """
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
    text = str(text)
    marked = WAKE_MARKER in text or REVIEW_MARKER in text or any(m in text for m in STOP_WAKE_MARKERS)
    plugin_row = bool(row.get("pluginPrompt")) or origin.get("kind") == "plugin"
    stop_row = bool(row.get("isMeta")) and text.startswith(STOP_FEEDBACK)
    if not (marked or plugin_row or stop_row):
        return None
    if WAKE_MARKER in text:
        return "wake"
    if REVIEW_MARKER in text:
        return "review"
    if any(m in text for m in STOP_WAKE_MARKERS):
        return "wake"
    return "unidentified"


def _blocks(message: Any) -> list[dict[str, Any]]:
    content = (message or {}).get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    return str(content or "")


def _source_hits(sources: dict[str, str], text: str) -> tuple[int, list[str]]:
    count = sum(len(longest_exact_matches(src, text)) for src in sources.values())
    whole = [sid for sid, src in sources.items() if src and src in text]
    return count, whole


def inventory_main_rows(rows: list[dict[str, Any]], source_messages: dict[str, str],
                        raw_events: Iterable[str] = ()) -> dict[str, Any]:
    """Gate every host-injected block of one main transcript; report main's own reads separately."""
    raw_events = [e for e in raw_events or () if e]
    event_sources, forbidden = event_payloads(raw_events)
    sources = {**{str(k): v for k, v in source_messages.items()}, **event_sources}
    tool_names: dict[str, str] = {}
    injected: list[dict[str, Any]] = []
    unidentified_source_rows: list[dict[str, Any]] = []
    request_context: list[dict[str, Any]] = []
    own_reads: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    assistant_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if row.get("isSidechain"):
            continue
        kind, message = row.get("type"), row.get("message") or {}
        if kind == "assistant":
            texts = []
            for block in _blocks(message):
                if block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    tool_names[str(block.get("id"))] = name
                    if name.startswith("mcp__walkie__"):
                        own_reads.append({"row": index, "tool": name, "input": block.get("input")})
                    texts.append(json.dumps(block.get("input"), ensure_ascii=False))
                elif block.get("type") == "text":
                    texts.append(str(block.get("text") or ""))
            count, whole = _source_hits(sources, "\n".join(texts))
            if count or whole:
                assistant_rows.append({"row": index, "matches": count, "whole_source_messages": whole})
        elif kind == "user":
            origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
            for block in _blocks(message):
                if block.get("type") == "tool_result":
                    count, whole = _source_hits(sources, _tool_result_text(block))
                    if count or whole:
                        tool_results.append({"row": index, "tool": tool_names.get(str(block.get("tool_use_id")), ""),
                                             "matches": count, "whole_source_messages": whole})
                    continue
                if block.get("type") != "text":
                    continue
                text = str(block.get("text") or "")
                host = host_injection_kind(row, text)
                if host is None:
                    if origin.get("kind") != "human":
                        inv = inventory_fields(sources, {"user.unclassified": text}, raw_events=raw_events)
                        if inv["matches"] or inv["whole_source_messages"] or inv["raw_event_fields"]:
                            unidentified_source_rows.append({"row": index, "inventory": inv})
                    continue
                parse_error, fields = None, {}
                try:
                    fields = host_block_destinations(host, text)
                except (ValueError, TypeError) as error:
                    parse_error = str(error)[:300]
                inv = inventory_fields(sources, fields, raw_events=raw_events)
                whole_block = inventory_fields(sources, {"block.whole": text}, raw_events=raw_events)
                injected.append({
                    "row": index, "kind": host, "parse_error": parse_error, "fields": inv,
                    "block_whole_messages": whole_block["whole_source_messages"],
                    "block_raw_event": whole_block["raw_event_fields"],
                    "passed": host in ("wake", "review") and not parse_error and inv["passed"]
                    and not whole_block["whole_source_messages"] and not whole_block["raw_event_fields"],
                })
        elif kind == "api-request-blob":
            # Request-only host context (prompt.submit additionalContext, system prompt).
            # Its handoff/review payloads are inventoried like wake/review rows; all other
            # text is request_context.prose, which is not an allowed destination.
            role = message.get("role")
            for n, block in enumerate(_blocks(message)):
                text = str(block.get("text") or "") if block.get("type") == "text" else ""
                if not text or not (role == "system" or text.lstrip().startswith("<system-reminder>")):
                    continue
                parse_error, counts = None, {}
                try:
                    fields, counts = structured_destinations(text, "request_context.prose")
                except (ValueError, TypeError) as error:
                    fields, parse_error = {"request_context.prose": text}, str(error)[:300]
                inv = inventory_fields(sources, fields, raw_events=raw_events)
                whole_block = inventory_fields(sources, {"block.whole": text}, raw_events=raw_events)
                if inv["matches"] or inv["whole_source_messages"] or inv["raw_event_fields"] or inv["bounds_exceeded"] \
                        or whole_block["whole_source_messages"] or whole_block["raw_event_fields"] or parse_error or any(counts.values()):
                    request_context.append({
                        "row": index, "block": n, "role": role, "views": counts, "parse_error": parse_error, "fields": inv,
                        "block_whole_messages": whole_block["whole_source_messages"], "block_raw_event": whole_block["raw_event_fields"],
                        "passed": not parse_error and inv["passed"] and not whole_block["whole_source_messages"]
                        and not whole_block["raw_event_fields"],
                    })
    failures = []
    for item in injected:
        if not item["passed"]:
            failures.append({"row": item["row"], "kind": item["kind"], "parse_error": item["parse_error"],
                             "disallowed": len(item["fields"]["disallowed_matches"]),
                             "whole": item["fields"]["whole_source_messages"] + item["block_whole_messages"],
                             "raw_event": item["fields"]["raw_event_fields"] + item["block_raw_event"],
                             "bounds_exceeded": item["fields"]["bounds_exceeded"]})
    failures.extend({"row": item["row"], "kind": "unidentified_with_source_text"} for item in unidentified_source_rows)
    for item in request_context:
        if not item["passed"]:
            failures.append({"row": item["row"], "kind": "request_context", "parse_error": item["parse_error"],
                             "disallowed": len(item["fields"]["disallowed_matches"]),
                             "whole": item["fields"]["whole_source_messages"] + item["block_whole_messages"],
                             "raw_event": item["fields"]["raw_event_fields"] + item["block_raw_event"],
                             "bounds_exceeded": item["fields"]["bounds_exceeded"]})
    return {
        "minimum_length": MIN_EXCERPT,
        "allowed_fields": sorted(ALLOWED_FIELDS),
        "field_bounds": dict(FIELD_BOUNDS),
        "source_ids": sorted(sources),
        "forbidden_payload_ids": [item["id"] for item in forbidden],
        "injected": injected,
        "unidentified_rows_with_source_text": unidentified_source_rows,
        "request_context": request_context,
        "failures": failures,
        "passed": not failures,
        "observations": {
            "main_own_source_reads": own_reads,
            "main_tool_results_with_source_text": tool_results,
            "main_assistant_rows_with_source_text": assistant_rows,
        },
    }
