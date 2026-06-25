from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


EVENT_LEVELS = {"trace": 10, "debug": 20, "info": 30, "warn": 40, "error": 50, "fatal": 60}
DEFAULT_EVENT_LEVEL = "info"
DEFAULT_EVENT_BUFFER = 1000


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "off", "no", ""}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(str(os.environ.get(name, default)).strip()))
    except Exception:
        return default


def _level_value(level: str) -> int:
    return EVENT_LEVELS.get(str(level or "").strip().lower(), EVENT_LEVELS[DEFAULT_EVENT_LEVEL])


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            secret_key = lowered in {"authorization", "password", "secret", "token", "api_key", "apikey", "access_token", "refresh_token"} or lowered.endswith(("_key", "_token", "_secret", "_password"))
            if secret_key:
                out[key] = "[redacted]"
            else:
                out[key] = _redact_value(item)
        return out
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


@dataclass(frozen=True)
class EventConfig:
    enabled: bool
    level: str
    buffer_size: int

    @classmethod
    def from_env(cls) -> "EventConfig":
        return cls(
            enabled=_env_bool("CIEL_RUNTIME_EVENT_LOG", True),
            level=os.environ.get("CIEL_RUNTIME_EVENT_LEVEL", DEFAULT_EVENT_LEVEL).strip().lower() or DEFAULT_EVENT_LEVEL,
            buffer_size=_env_int("CIEL_RUNTIME_EVENT_BUFFER", DEFAULT_EVENT_BUFFER),
        )


class EventBus:
    def __init__(self, config: EventConfig | None = None) -> None:
        self.config = config or EventConfig.from_env()
        self._events: deque[dict[str, Any]] = deque(maxlen=self.config.buffer_size)
        self._condition = threading.Condition()
        self._next_id = 1

    def publish(
        self,
        *,
        level: str,
        category: str,
        message: str,
        source: str = "router",
        session_id: str | None = None,
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.config.enabled:
            return None
        level_name = str(level or DEFAULT_EVENT_LEVEL).strip().lower()
        if _level_value(level_name) < _level_value(self.config.level):
            return None
        with self._condition:
            event = {
                "id": self._next_id,
                "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "ts": time.time(),
                "level": level_name,
                "source": source,
                "category": str(category or "router"),
                "session_id": session_id or "",
                "request_id": request_id or "",
                "provider": provider or "",
                "model": model or "",
                "message": str(message or ""),
                "data": _redact_value(data or {}),
            }
            self._next_id += 1
            self._events.append(event)
            self._condition.notify_all()
            return event

    def recent(self, *, limit: int = 200, min_id: int | None = None, level: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        with self._condition:
            events = list(self._events)
        if min_id is not None:
            events = [event for event in events if int(event.get("id") or 0) > min_id]
        if level:
            threshold = _level_value(level)
            events = [event for event in events if _level_value(str(event.get("level") or "")) >= threshold]
        if category:
            events = [event for event in events if str(event.get("category") or "").startswith(category)]
        if limit <= 0:
            return []
        return events[-limit:]

    def wait_after(self, last_id: int, timeout: float = 15.0) -> list[dict[str, Any]]:
        deadline = time.time() + max(0.1, timeout)
        with self._condition:
            while not any(int(event.get("id") or 0) > last_id for event in self._events):
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._condition.wait(min(remaining, 1.0))
            return [event for event in self._events if int(event.get("id") or 0) > last_id]


def render_events_html(events_path: str = "/ca/events/stream", recent_path: str = "/ca/events/recent") -> str:
    events_path_js = json.dumps(events_path)
    recent_path_js = json.dumps(recent_path)
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Ciel Runtime Router Events</title>
  <style>
    :root {{ color-scheme: dark; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; }}
    body {{ margin: 0; background: #090b0f; color: #e8edf4; }}
    header {{ position: sticky; top: 0; display: flex; gap: 12px; align-items: center; padding: 12px 16px; background: #111722; border-bottom: 1px solid #263244; }}
    h1 {{ margin: 0; font-size: 16px; }}
    select, input {{ background: #0d121b; color: #e8edf4; border: 1px solid #334155; border-radius: 6px; padding: 6px 8px; }}
    main {{ display: grid; grid-template-columns: minmax(0, 1fr) 420px; min-height: calc(100vh - 54px); }}
    #events {{ padding: 12px; overflow: auto; }}
    .event {{ border-left: 3px solid #64748b; padding: 8px 10px; margin: 0 0 8px; background: #0d121b; border-radius: 6px; cursor: pointer; }}
    .event.warn {{ border-color: #fbbf24; }}
    .event.error, .event.fatal {{ border-color: #fb7185; }}
    .event.debug {{ border-color: #60a5fa; }}
    .event.trace {{ border-color: #a78bfa; }}
    .meta {{ color: #94a3b8; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }}
    .message {{ margin-top: 4px; }}
    .preview {{ margin-top: 4px; color: #cbd5e1; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; white-space: pre-wrap; word-break: break-word; }}
    aside {{ border-left: 1px solid #263244; padding: 12px; background: #0b1018; overflow: auto; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #05070a; border: 1px solid #1f2937; border-radius: 6px; padding: 10px; }}
  </style>
</head>
<body>
  <header>
    <h1>Ciel Runtime Router Events</h1>
    <label>Level <select id=\"level\"><option value=\"trace\">trace</option><option value=\"debug\">debug</option><option value=\"info\" selected>info</option><option value=\"warn\">warn</option><option value=\"error\">error</option></select></label>
    <label>Category <input id=\"category\" placeholder=\"upstream, advisor, plan_mode\"></label>
    <span class=\"meta\" id=\"count\">0 events</span>
  </header>
  <main>
    <section id=\"events\"></section>
    <aside><h2>Event Detail</h2><pre id=\"detail\">Select an event.</pre></aside>
  </main>
  <script>
    const eventsEl = document.getElementById('events');
    const detailEl = document.getElementById('detail');
    const countEl = document.getElementById('count');
    const levelEl = document.getElementById('level');
    const categoryEl = document.getElementById('category');
    let allEvents = [];
    const levelRank = {{trace:10, debug:20, info:30, warn:40, error:50, fatal:60}};
    function esc(s) {{ return String(s ?? '').replace(/[&<>\"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}}[c])); }}
    function visible(e) {{
      const level = levelEl.value || 'info';
      const cat = categoryEl.value.trim();
      return (levelRank[e.level] || 30) >= (levelRank[level] || 30) && (!cat || String(e.category || '').startsWith(cat));
    }}
    function render() {{
      const rows = allEvents.filter(visible).slice(-500);
      countEl.textContent = rows.length + ' events';
      eventsEl.innerHTML = rows.map(e => {{
        const preview = e.data && e.data.message_preview ? `<div class=\"preview\">${{esc(e.data.message_preview)}}${{e.data.message_preview_truncated ? '…' : ''}}</div>` : '';
        return `<div class=\"event ${{esc(e.level)}}\" data-id=\"${{e.id}}\"><div class=\"meta\">#${{e.id}} ${{esc(e.time)}} · ${{esc(e.level)}} · ${{esc(e.category)}} · ${{esc(e.provider)}} ${{esc(e.model)}}</div><div class=\"message\">${{esc(e.message)}}</div>${{preview}}</div>`;
      }}).join('');
    }}
    eventsEl.addEventListener('click', ev => {{
      const row = ev.target.closest('.event');
      if (!row) return;
      const item = allEvents.find(e => String(e.id) === row.dataset.id);
      detailEl.textContent = JSON.stringify(item, null, 2);
    }});
    levelEl.addEventListener('change', render);
    categoryEl.addEventListener('input', render);
    fetch({recent_path_js}).then(r => r.json()).then(j => {{ allEvents = j.events || []; render(); }}).catch(() => {{}});
    const es = new EventSource({events_path_js});
    es.addEventListener('event', ev => {{
      try {{ allEvents.push(JSON.parse(ev.data)); if (allEvents.length > 2000) allEvents = allEvents.slice(-2000); render(); }} catch (_) {{}}
    }});
  </script>
</body>
</html>"""
