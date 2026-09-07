"""Extract response URLs, never search arguments or result prose, from CLI records."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit


_SEARCH_NAMES = {"WebSearch", "web_search", "web.run", "web__run", "functions.web__run"}
_URL = re.compile(r"https?://[^\s<>\"\x00-\x1f]+")


def _is_search_call(value: dict[str, Any]) -> bool:
    name = value.get("name")
    if name not in _SEARCH_NAMES:
        return False
    if name in {"WebSearch", "web_search"}:
        return True
    arguments = value.get("arguments", value.get("input"))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return False
    return isinstance(arguments, dict) and bool(arguments.get("search_query"))


def _valid_url(value: Any) -> bool:
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _urls(value: Any, *, text: bool = False) -> list[str]:
    if isinstance(value, list):
        return [url for item in value for url in _urls(item, text=text)]
    if isinstance(value, dict):
        result = [value["url"]] if _valid_url(value.get("url")) else []
        for key in ("sources", "results", "content", "annotations", "url_citation"):
            result.extend(_urls(value.get(key), text=text))
        if text and value.get("type") == "text":
            result.extend(_urls(value.get("text"), text=True))
        return result
    if text and isinstance(value, str):
        # Claude Code supplies a JSON Links list embedded in a text result.
        match = re.search(r"(?:^|\n)Links:\s*", value)
        if match:
            try:
                links, _ = json.JSONDecoder().raw_decode(value[match.end():])
                return _urls(links)
            except ValueError:
                return []
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list)):
            return _urls(decoded, text=True)
        result = []
        for match in _URL.finditer(value):
            url = match.group().rstrip(".,;!?'\"]}")
            while url.endswith(")") and url.count(")") > url.count("("):
                url = url[:-1]
            if _valid_url(url):
                result.append(url)
        return result
    return []


def project_web_search_results(record: dict[str, Any], runtime: str, state: dict[str, Any]) -> list[dict[str, Any]]:
    """State is persisted with the transcript offset, bounded to 512 calls/results."""
    pending = state.setdefault("pending", {})
    seen = state.setdefault("seen", [])
    events = []

    def emit(call_id: str, name: str, urls: list[str]) -> None:
        urls = list(dict.fromkeys(urls))
        if not urls:
            return
        digest = hashlib.sha256(json.dumps([call_id, urls], ensure_ascii=False).encode()).hexdigest()
        if digest in seen:
            return
        seen.append(digest)
        events.append({"call_id": call_id, "name": name, "runtime": runtime, "phase": "result", "urls": urls})

    message = record.get("message") or {}
    blocks = message.get("content", []) if isinstance(message, dict) else []
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind in {"tool_use", "server_tool_use"} and (record.get("type") == "assistant" or message.get("role") == "assistant") and _is_search_call(block):
                if block.get("id"):
                    pending[str(block["id"])] = str(block["name"])
            elif kind in {"tool_result", "web_search_tool_result"} and not block.get("is_error"):
                call_id = str(block.get("tool_use_id") or "")
                name = pending.get(call_id)
                if name or kind == "web_search_tool_result":
                    emit(call_id, name or "web_search", _urls(block.get("content"), text=True))

    payload = record.get("payload") or {}
    if record.get("type") == "response_item" and isinstance(payload, dict):
        kind = payload.get("type")
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        if kind in {"function_call", "custom_tool_call"} and _is_search_call(payload) and call_id:
            pending[call_id] = str(payload["name"])
        elif kind in {"function_call_output", "custom_tool_call_output"} and call_id in pending:
            emit(call_id, pending[call_id], _urls(payload.get("output"), text=True))
        elif kind == "web_search_call" and payload.get("status") == "completed":
            # Do not treat action.query or open_page input URLs as search results.
            action = payload.get("action") or {}
            sources = action.get("sources") if isinstance(action, dict) else None
            emit(call_id, "web_search", _urls([sources, payload.get("results")]))
        elif kind == "message" and payload.get("role") == "assistant":
            # API-backed transcripts can retain structured URL citations.
            for block in payload.get("content") or []:
                if isinstance(block, dict):
                    annotations = [a for a in block.get("annotations", []) if isinstance(a, dict) and a.get("type") == "url_citation"]
                    emit(call_id, "web_search", _urls(annotations))
    while len(pending) > 512:
        del pending[next(iter(pending))]
    del seen[:-512]
    return events
