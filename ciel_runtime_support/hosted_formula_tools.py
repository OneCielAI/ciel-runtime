"""Append-only orchestration for provider-hosted Formula tools."""

from __future__ import annotations

import copy
import json
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .architecture import HostedToolPolicy


@dataclass(frozen=True, slots=True)
class HostedFormulaState:
    formula_by_function: Mapping[str, str]
    headers: Mapping[str, str]
    policy: HostedToolPolicy

    @property
    def enabled(self) -> bool:
        return bool(self.formula_by_function)


class HostedFormulaToolService:
    """Load Formula schemas, execute calls, and append results to chat history."""

    def __init__(
        self,
        policy_for: Callable[[str, dict[str, Any]], HostedToolPolicy],
        log: Callable[[str, str], None],
        *,
        open_url: Callable[..., Any] = urllib.request.urlopen,
        max_rounds: int = 8,
    ) -> None:
        self._policy_for = policy_for
        self._log = log
        self._open_url = open_url
        self._max_rounds = max(1, max_rounds)
        self._cache: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}
        self._unavailable: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _auth_headers(headers: Mapping[str, str]) -> dict[str, str]:
        projected = {"content-type": "application/json"}
        authorization = headers.get("authorization") or headers.get("Authorization")
        if authorization:
            projected["authorization"] = str(authorization)
        return projected

    def _json_request(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        payload: Mapping[str, Any] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers=dict(headers),
            method="GET" if data is None else "POST",
        )
        with self._open_url(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8", errors="replace"))
        if not isinstance(decoded, dict):
            raise RuntimeError("hosted Formula API returned non-object JSON")
        return decoded

    @staticmethod
    def _formula_path(formula: str) -> str:
        return urllib.parse.quote(formula, safe="/:")

    def _tools_for_formula(
        self,
        policy: HostedToolPolicy,
        formula: str,
        headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[dict[str, Any], ...]:
        cache_key = (policy.base_url, formula)
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        payload = self._json_request(
            f"{policy.base_url}/formulas/{self._formula_path(formula)}/tools",
            headers,
            timeout=timeout,
        )
        tools = tuple(copy.deepcopy(tool) for tool in payload.get("tools", ()) if isinstance(tool, dict))
        with self._lock:
            self._cache[cache_key] = tools
        return tools

    def prepare(
        self,
        provider: str,
        config: dict[str, Any],
        request_body: dict[str, Any],
        upstream_headers: Mapping[str, str],
        timeout: float,
    ) -> tuple[dict[str, Any], HostedFormulaState]:
        policy = self._policy_for(provider, config)
        auth_headers = self._auth_headers(upstream_headers)
        empty = HostedFormulaState({}, auth_headers, policy)
        if not policy.base_url or not policy.formulas or "authorization" not in auth_headers:
            return request_body, empty
        with self._lock:
            if policy.base_url in self._unavailable:
                return request_body, empty
        timeout = min(max(1.0, timeout), 10.0)
        existing = list(request_body.get("tools") or [])
        used_names = {
            str(tool.get("function", {}).get("name") or "")
            for tool in existing
            if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
        }
        formula_by_function: dict[str, str] = {}
        try:
            for formula in policy.formulas:
                for tool in self._tools_for_formula(policy, formula, auth_headers, timeout):
                    function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
                    name = str(function.get("name") or "")
                    if not name or name in used_names:
                        continue
                    used_names.add(name)
                    formula_by_function[name] = formula
                    existing.append(tool)
        except Exception as exc:
            with self._lock:
                self._unavailable.add(policy.base_url)
            self._log("WARN", f"hosted Formula tools unavailable for {provider}: {type(exc).__name__}: {exc}")
            return request_body, empty
        if not formula_by_function:
            return request_body, empty
        prepared = {**request_body, "tools": existing, "stream": False}
        return prepared, HostedFormulaState(formula_by_function, auth_headers, policy)

    def _execute(
        self,
        state: HostedFormulaState,
        function: Mapping[str, Any],
        timeout: float,
    ) -> str:
        name = str(function.get("name") or "")
        formula = state.formula_by_function[name]
        payload = self._json_request(
            f"{state.policy.base_url}/formulas/{self._formula_path(formula)}/fibers",
            state.headers,
            payload={"function": {"name": name, "arguments": str(function.get("arguments") or "{}")}},
            timeout=timeout,
        )
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        output = context.get("output")
        if output is None:
            output = context.get("encrypted_output")
        if isinstance(output, str):
            return output
        return json.dumps(output if output is not None else payload, ensure_ascii=False)

    def resolve(
        self,
        state: HostedFormulaState,
        request_body: dict[str, Any],
        response: dict[str, Any],
        post_chat: Callable[[dict[str, Any]], dict[str, Any]],
        timeout: float,
    ) -> dict[str, Any]:
        if not state.enabled:
            return response
        body = copy.deepcopy(request_body)
        current = response
        for _round in range(self._max_rounds):
            choices = current.get("choices") if isinstance(current, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            calls = [call for call in message.get("tool_calls", ()) if isinstance(call, dict)]
            if not calls:
                return current
            functions = [call.get("function") if isinstance(call.get("function"), dict) else {} for call in calls]
            if any(str(function.get("name") or "") not in state.formula_by_function for function in functions):
                return current
            body.setdefault("messages", []).append(copy.deepcopy(message))
            for call, function in zip(calls, functions):
                body["messages"].append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or "hosted_tool"),
                        "content": self._execute(state, function, timeout),
                    }
                )
            body["tool_choice"] = "auto"
            current = post_chat(body)
        raise RuntimeError(f"hosted Formula tool loop exceeded {self._max_rounds} rounds")


__all__ = ["HostedFormulaState", "HostedFormulaToolService"]


_SHARED_SERVICE: HostedFormulaToolService | None = None


def shared_service(
    policy_for: Callable[[str, dict[str, Any]], HostedToolPolicy],
    log: Callable[[str, str], None],
) -> HostedFormulaToolService:
    global _SHARED_SERVICE
    if _SHARED_SERVICE is None:
        _SHARED_SERVICE = HostedFormulaToolService(policy_for, log)
    return _SHARED_SERVICE
