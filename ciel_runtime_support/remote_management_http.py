"""Management REST API: push provider, model, API keys and OAuth tokens to a router.

Routes (JSON), behind the router's normal access check (loopback, the admin
bearer token, or a signed-in web session; from another host the router's
external access must be on and the admin token presented):

- ``GET /ca/manage/state`` - current provider and model, every provider's
  stored API keys as mask + fingerprint, the workspace's OAuth tokens.
  Secret values are never returned.
- ``POST /ca/manage/apply`` - one change set::

      {"provider": "cielairouter",
       "model": "ciel-sol",
       "model_scope": "running",
       "api_keys": {"cielairouter": {"mode": "replace", "keys": ["k1", "k2"]}},
       "oauth_tokens": [
         {"action": "add", "provider": "claude", "label": "team",
          "credential": {"access_token": "...", "refresh_token": "...", "expires_at": 1790000000}},
         {"action": "add", "provider": "codex", "content": "<auth.json text>"},
         {"action": "update", "token_id": "claude-1de5f7", "enabled": false},
         {"action": "remove", "token_id": "codex-0a1b2c"}]}

  ``api_keys`` modes: ``replace``, ``append``, ``remove`` (by value or
  fingerprint) and ``clear``; several keys rotate round-robin.  Everything is
  validated before anything is written, and the configuration is restored if
  a step fails, so a change set applies whole or not at all.

  ``model_scope``: ``running`` (default when a model is pushed) also switches
  sessions that are already running - they keep naming their launch model in
  every request, so the router sends the pushed model instead
  (model_override); ``launch`` sets the model for new launches only and
  releases a forced one.  A model chosen locally releases it too.

The router reads the configuration and the token store on every request, so
API keys, OAuth tokens and a ``running`` model apply to the next routed
request of running sessions (the CLI may still display its launch model).  Every change is
appended to ``management-audit.jsonl`` with fingerprints only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Callable

from ciel_runtime_support.model_override import FORCED_MODEL_KEY
from ciel_runtime_support.oauth_token_endpoints import import_claude_credentials, import_codex_auth
from ciel_runtime_support.oauth_token_refresh import OAuthTokenRefresher
from ciel_runtime_support.oauth_token_store import (
    PROVIDERS as OAUTH_PROVIDERS,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    OAuthCredential,
    OAuthTokenStore,
)

STATE_PATH = "/ca/manage/state"
APPLY_PATH = "/ca/manage/apply"
AUDIT_FILE = "management-audit.jsonl"
KEY_MODES = frozenset({"replace", "append", "remove", "clear"})
TOKEN_ACTIONS = frozenset({"add", "update", "remove", "refresh"})
MODEL_SCOPES = frozenset({"running", "launch"})
APPLIES = (
    "Routed requests read this on every request: API keys, OAuth tokens and a model pushed with "
    "model_scope running apply to running sessions from their next request (the CLI may still show "
    "its launch model); model_scope launch applies to new launches."
)


class ManagementError(ValueError):
    """A change set that cannot be applied; nothing was changed."""


@dataclass(frozen=True, slots=True)
class ManagementPorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    normalize_choice: Callable[[str], str | None]
    select_provider: Callable[[str], list[str]]
    select_model: Callable[[str], list[str]]
    store_keys: Callable[[str, list[str]], list[str]]
    clear_keys: Callable[[str], list[str]]
    configured_keys: Callable[[str, dict[str, Any]], list[str]]
    mask: Callable[[str], str]
    fingerprint: Callable[[str], str]
    workspace_state_dir: Callable[[], Path]
    write_json: Callable[..., Any]
    clear_model_cache: Callable[[], None] = lambda: None
    version: str = ""
    clock: Callable[[], float] = time.time


@dataclass(frozen=True, slots=True)
class _TokenStep:
    action: str
    provider: str = ""
    token_id: str = ""
    label: str = ""
    credential: OAuthCredential | None = None
    email: str = ""
    enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class _ChangeSet:
    provider: str
    model: str
    model_scope: str
    keys: dict[str, tuple[str, list[str]]]
    tokens: list[_TokenStep]


class RemoteManagementController:
    def __init__(self, ports: ManagementPorts) -> None:
        self.ports = ports

    # -- routing -------------------------------------------------------------
    def handle_get(self, handler: Any, path: str) -> bool:
        if path != STATE_PATH:
            return False
        self.ports.write_json(handler, {"ok": True, **self.state()})
        return True

    def handle_post(self, handler: Any, path: str, body: dict[str, Any]) -> bool:
        if path != APPLY_PATH:
            return False
        try:
            results = self.apply(body, origin=_origin(handler))
        except (ManagementError, SystemExit) as exc:
            self.ports.write_json(handler, {"ok": False, "error": "rejected", "message": str(exc), "changed": False}, 400)
            return True
        self.ports.write_json(handler, {"ok": True, "results": results, "applies": APPLIES, **self.state()})
        return True

    # -- state ---------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        config = self.ports.load_config()
        provider, provider_config = self.ports.current_provider(config)
        providers: dict[str, Any] = {}
        for name, settings in sorted((config.get("providers") or {}).items()):
            if not isinstance(settings, dict):
                continue
            keys = self.ports.configured_keys(name, settings)
            if keys or name == provider:
                providers[name] = {
                    "current_model": str(settings.get("current_model") or ""),
                    "forced_model": str(settings.get(FORCED_MODEL_KEY) or ""),
                    "api_keys": [{"mask": self.ports.mask(key), "fingerprint": self.ports.fingerprint(key)} for key in keys],
                }
        now = self.ports.clock()
        tokens = OAuthTokenStore(self.ports.workspace_state_dir()).snapshot().tokens
        return {
            "version": self.ports.version,
            "provider": provider,
            "model": str(provider_config.get("current_model") or ""),
            "providers": providers,
            "oauth_tokens": [_token_row(token, now) for token in tokens],
        }

    # -- apply ---------------------------------------------------------------
    def apply(self, body: dict[str, Any], *, origin: str = "") -> list[str]:
        change = self._validate(body)
        store = OAuthTokenStore(self.ports.workspace_state_dir())
        snapshot = json.loads(json.dumps(self.ports.load_config()))
        results: list[str] = []
        try:
            if change.provider:
                results.extend(self.ports.select_provider(change.provider))
            for name, (mode, keys) in change.keys.items():
                results.extend(self._apply_keys(name, mode, keys))
            if change.model:
                lines = self.ports.select_model(change.model)
                rejected = [line for line in lines if line.startswith("Model selection rejected")]
                if rejected:
                    raise ManagementError(rejected[0])
                results.extend(lines)
            if change.model_scope:
                results.append(self._set_model_scope(change.model, change.model_scope))
            results.extend(self._apply_tokens(store, change.tokens))
        except BaseException:
            self.ports.save_config(snapshot)
            self.ports.clear_model_cache()
            raise
        self._audit(change, origin)
        return results

    def _validate(self, body: dict[str, Any]) -> _ChangeSet:
        if not isinstance(body, dict):
            raise ManagementError("The change set must be a JSON object.")
        unknown = sorted(set(body) - {"provider", "model", "model_scope", "api_keys", "oauth_tokens"})
        if unknown:
            raise ManagementError(f"Unknown fields: {', '.join(unknown)}.")
        config = self.ports.load_config()
        known = set((config.get("providers") or {}).keys())
        provider = str(body.get("provider") or "").strip()
        if provider and provider not in known and self.ports.normalize_choice(provider) is None:
            raise ManagementError(f"Unknown provider {provider!r}.")
        model = str(body.get("model") or "").strip()
        model_scope = str(body.get("model_scope") or ("running" if model else "")).strip()
        if model_scope and model_scope not in MODEL_SCOPES:
            raise ManagementError(f"model_scope must be one of {', '.join(sorted(MODEL_SCOPES))}.")
        if model_scope == "running" and not model:
            raise ManagementError("model_scope running needs a model.")
        keys: dict[str, tuple[str, list[str]]] = {}
        raw_keys = body.get("api_keys") or {}
        if not isinstance(raw_keys, dict):
            raise ManagementError("api_keys must map a provider to {mode, keys}.")
        for name, item in raw_keys.items():
            if name not in known:
                raise ManagementError(f"Unknown provider {name!r} in api_keys.")
            if not isinstance(item, dict):
                raise ManagementError(f"api_keys.{name} must be {{mode, keys}}.")
            mode = str(item.get("mode") or "replace")
            if mode not in KEY_MODES:
                raise ManagementError(f"api_keys.{name}.mode must be one of {', '.join(sorted(KEY_MODES))}.")
            values = item.get("keys") or []
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ManagementError(f"api_keys.{name}.keys must be a list of strings.")
            values = [value.strip() for value in values if value.strip()]
            if mode != "clear" and not values:
                raise ManagementError(f"api_keys.{name}: no keys given for {mode}.")
            keys[name] = (mode, values)
        raw_tokens = body.get("oauth_tokens") or []
        if not isinstance(raw_tokens, list):
            raise ManagementError("oauth_tokens must be a list.")
        existing = {token.token_id for token in OAuthTokenStore(self.ports.workspace_state_dir()).snapshot().tokens}
        tokens = [self._token_step(index, item, existing) for index, item in enumerate(raw_tokens)]
        if not (provider or model or model_scope or keys or tokens):
            raise ManagementError("Nothing to apply.")
        return _ChangeSet(provider, model, model_scope, keys, tokens)

    def _token_step(self, index: int, item: Any, existing: set[str]) -> _TokenStep:
        where = f"oauth_tokens[{index}]"
        if not isinstance(item, dict):
            raise ManagementError(f"{where} must be an object.")
        action = str(item.get("action") or "")
        if action not in TOKEN_ACTIONS:
            raise ManagementError(f"{where}.action must be one of {', '.join(sorted(TOKEN_ACTIONS))}.")
        if action != "add":
            token_id = str(item.get("token_id") or "")
            if token_id not in existing:
                raise ManagementError(f"{where}: no token {token_id!r} in this workspace.")
            enabled = item.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                raise ManagementError(f"{where}.enabled must be true or false.")
            return _TokenStep(action, token_id=token_id, label=str(item.get("label") or ""), enabled=enabled)
        provider = str(item.get("provider") or "")
        if provider not in OAUTH_PROVIDERS:
            raise ManagementError(f"{where}.provider must be one of {', '.join(OAUTH_PROVIDERS)}.")
        credential, email = self._credential(where, provider, item)
        return _TokenStep("add", provider=provider, label=str(item.get("label") or "").strip(), credential=credential, email=email)

    @staticmethod
    def _credential(where: str, provider: str, item: dict[str, Any]) -> tuple[OAuthCredential, str]:
        content = item.get("content")
        if content is not None:
            if not isinstance(content, str) or not content.strip():
                raise ManagementError(f"{where}.content must be the text of auth.json or .credentials.json.")
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "credentials.json"
                path.write_text(content, encoding="utf-8")
                reader = import_codex_auth if provider == "codex" else import_claude_credentials
                try:
                    return reader(path)
                except (OSError, ValueError) as exc:
                    raise ManagementError(f"{where}.content: {exc}") from None
        raw = item.get("credential")
        if not isinstance(raw, dict) or not str(raw.get("access_token") or "").strip():
            raise ManagementError(f"{where} needs credential.access_token or content.")
        try:
            expires_at = float(raw.get("expires_at") or 0)
        except (TypeError, ValueError):
            raise ManagementError(f"{where}.credential.expires_at must be epoch seconds.") from None
        scopes = raw.get("scopes") or ()
        credential = OAuthCredential(
            access_token=str(raw.get("access_token")).strip(),
            refresh_token=str(raw.get("refresh_token") or "").strip(),
            id_token=str(raw.get("id_token") or "").strip(),
            account_id=str(raw.get("account_id") or "").strip(),
            expires_at=expires_at / 1000.0 if expires_at > 1e12 else expires_at,
            scopes=tuple(str(scope) for scope in scopes) if isinstance(scopes, (list, tuple)) else (),
        )
        return credential, str(item.get("email") or "")

    def _set_model_scope(self, model: str, scope: str) -> str:
        config = self.ports.load_config()
        provider, provider_config = self.ports.current_provider(config)
        if scope == "running":
            provider_config[FORCED_MODEL_KEY] = str(provider_config.get("current_model") or model)
            message = f"Running sessions of {provider} now use {provider_config[FORCED_MODEL_KEY]} from their next request."
        else:
            provider_config.pop(FORCED_MODEL_KEY, None)
            message = f"Model for {provider} applies to new launches; running sessions keep their own."
        self.ports.save_config(config)
        return message

    def _apply_keys(self, provider: str, mode: str, keys: list[str]) -> list[str]:
        if mode == "clear":
            return self.ports.clear_keys(provider)
        config = self.ports.load_config()
        current = self.ports.configured_keys(provider, config["providers"][provider])
        if mode == "replace":
            wanted = list(dict.fromkeys(keys))
        elif mode == "append":
            wanted = list(dict.fromkeys([*current, *keys]))
        else:
            drop = set(keys)
            wanted = [key for key in current if key not in drop and self.ports.fingerprint(key) not in drop]
            if len(wanted) == len(current):
                raise ManagementError(f"api_keys.{provider}: none of the keys to remove is stored.")
            if not wanted:
                return self.ports.clear_keys(provider)
        return self.ports.store_keys(provider, wanted)

    def _apply_tokens(self, store: OAuthTokenStore, steps: list[_TokenStep]) -> list[str]:
        results: list[str] = []
        for step in steps:
            if step.action == "add" and step.credential is not None:
                token = store.add(
                    step.provider, step.credential,
                    label=step.label or step.email or step.provider, email=step.email, source="remote-management",
                )
                results.append(f"OAuth token added: {token.token_id} ({token.provider})")
            elif step.action == "update":
                with store.transaction() as snapshot:
                    token = snapshot.get(step.token_id)
                    if token is None:
                        results.append(f"OAuth token {step.token_id} was removed meanwhile")
                        continue
                    if step.label:
                        token.label = step.label
                    if step.enabled is not None:
                        token.status = STATUS_ACTIVE if step.enabled else STATUS_DISABLED
                        if step.enabled:
                            token.last_error = ""
                results.append(f"OAuth token updated: {step.token_id}")
            elif step.action == "remove":
                removed = store.remove(step.token_id)
                results.append(f"OAuth token {'removed' if removed else 'already gone'}: {step.token_id}")
            elif step.action == "refresh":
                outcome = OAuthTokenRefresher(store).refresh(step.token_id, force=True)
                results.append(f"OAuth token refresh {step.token_id}: {outcome.detail}")
        return results

    def _audit(self, change: _ChangeSet, origin: str) -> None:
        record = {
            "time": self.ports.clock(),
            "origin": origin,
            "provider": change.provider,
            "model": change.model,
            "model_scope": change.model_scope,
            "api_keys": {
                name: {"mode": mode, "fingerprints": [self.ports.fingerprint(key) for key in keys]}
                for name, (mode, keys) in change.keys.items()
            },
            "oauth_tokens": [
                {"action": step.action, "provider": step.provider, "token_id": step.token_id, "enabled": step.enabled}
                for step in change.tokens
            ],
        }
        path = self.ports.workspace_state_dir() / AUDIT_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            pass


def _token_row(token: Any, now: float) -> dict[str, Any]:
    row = asdict(token)
    state = token.status
    if token.status == STATUS_ACTIVE and token.exhausted_until > now:
        state = "limited"
    elif token.status == STATUS_ACTIVE and token.draining:
        state = "draining"
    row["state"] = state
    return row


def _origin(handler: Any) -> str:
    try:
        return str(handler.client_address[0])
    except Exception:
        return ""


__all__ = [
    "APPLY_PATH",
    "AUDIT_FILE",
    "ManagementError",
    "ManagementPorts",
    "RemoteManagementController",
    "STATE_PATH",
]
