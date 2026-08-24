"""Z.AI CLI OAuth and Coding Plan API-key resolution.

The wire contract mirrors the cross-platform init/poll flow exposed by the
ZCode runtime.  Ciel stores only the final Coding Plan API key; transient OAuth
access tokens and ZCode JWTs are deliberately kept in memory.
"""

from __future__ import annotations

import json
import hmac
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


ZCODE_OAUTH_BASE_URL = "https://zcode.z.ai/api/v1"
ZAI_AUTHORIZE_ENDPOINT = "https://chat.z.ai/api/oauth/authorize"
ZAI_OAUTH_CLIENT_ID = "client_P8X5CMWmlaRO9gyO-KSqtg"
ZAI_OAUTH_REDIRECT_URI = "zcode://zai-auth/callback"
ZAI_BUSINESS_BASE_URL = "https://api.z.ai"
ZAI_OAUTH_PROVIDER = "zai"
ZAI_CODING_PLAN_KEY_NAME = "zcode-api-key"
ZAI_OAUTH_TIMEOUT_SECONDS = 300.0


class ZaiOAuthError(RuntimeError):
    """A bounded, secret-free OAuth diagnostic."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class ZaiOAuthInit:
    flow_id: str
    authorize_url: str
    expires_at: float
    poll_interval_seconds: float


@dataclass(frozen=True, slots=True)
class ZaiOAuthResult:
    api_key: str
    user_id: str


class ZaiOAuthHttp:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        payload = (
            json.dumps(body, separators=(",", ":")).encode("utf-8")
            if body is not None
            else None
        )
        request = urllib.request.Request(
            url,
            data=payload,
            headers=dict(headers or {}),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(1_048_577)
        except urllib.error.HTTPError as exc:
            raise ZaiOAuthError(
                f"Z.AI OAuth HTTP {exc.code} at {url}", http_status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            reason = type(exc.reason).__name__ if exc.reason is not None else "network_error"
            raise ZaiOAuthError(f"Z.AI OAuth network error ({reason}) at {url}") from exc
        if len(raw) > 1_048_576:
            raise ZaiOAuthError("Z.AI OAuth response exceeded 1 MiB.")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ZaiOAuthError("Z.AI OAuth returned invalid JSON.") from exc


@dataclass(frozen=True, slots=True)
class ZaiOAuthClient:
    http: Any
    oauth_base_url: str = ZCODE_OAUTH_BASE_URL
    business_base_url: str = ZAI_BUSINESS_BASE_URL

    def initialize(self, poll_token: str) -> ZaiOAuthInit:
        data = self._oauth_data(
            "POST",
            f"{self.oauth_base_url.rstrip('/')}/oauth/cli/init",
            poll_token,
            body={"provider": ZAI_OAUTH_PROVIDER},
        )
        flow_id = self._required_string(data, "flow_id", "OAuth init")
        authorize_url = self._required_string(data, "authorize_url", "OAuth init")
        parsed = urllib.parse.urlparse(authorize_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ZaiOAuthError("Z.AI OAuth init returned an unsafe authorization URL.")
        try:
            expires_at = float(data["expires_at"])
            interval = max(1.0, float(data["poll_interval_sec"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ZaiOAuthError("Z.AI OAuth init returned invalid timing fields.") from exc
        return ZaiOAuthInit(flow_id, authorize_url, expires_at, interval)

    def poll(self, flow_id: str, poll_token: str) -> Mapping[str, Any]:
        safe_flow_id = urllib.parse.quote(flow_id, safe="")
        data = self._oauth_data(
            "GET",
            f"{self.oauth_base_url.rstrip('/')}/oauth/cli/poll/{safe_flow_id}",
            poll_token,
        )
        status = str(data.get("status") or "").strip().lower()
        if status not in {"pending", "failed", "ready"}:
            raise ZaiOAuthError("Z.AI OAuth poll returned an invalid status.")
        return data

    @staticmethod
    def authorize_url(state: str) -> str:
        query = urllib.parse.urlencode(
            {
                "client_id": ZAI_OAUTH_CLIENT_ID,
                "redirect_uri": ZAI_OAUTH_REDIRECT_URI,
                "response_type": "code",
                "state": state,
            }
        )
        return f"{ZAI_AUTHORIZE_ENDPOINT}?{query}"

    def exchange_callback(self, callback_url: str, expected_state: str) -> Mapping[str, Any]:
        try:
            parsed = urllib.parse.urlparse(callback_url.strip())
        except ValueError as exc:
            raise ZaiOAuthError("Z.AI returned an invalid OAuth callback URL.") from exc
        path = f"/{parsed.path.strip('/')}"
        if parsed.scheme != "zcode" or parsed.netloc != "zai-auth" or path != "/callback":
            raise ZaiOAuthError("Z.AI returned an unexpected OAuth callback target.")
        query = urllib.parse.parse_qs(parsed.query)
        state = str((query.get("state") or [""])[0])
        if not state or not hmac.compare_digest(state, expected_state):
            raise ZaiOAuthError("Z.AI OAuth state did not match. Please retry login.")
        error = str((query.get("error_description") or query.get("error") or [""])[0])
        if error:
            safe_error = " ".join(error.split())[:300]
            raise ZaiOAuthError(f"Z.AI authorization failed: {safe_error}")
        code = str((query.get("code") or query.get("authCode") or [""])[0])
        if not code:
            raise ZaiOAuthError("Z.AI OAuth callback did not include an authorization code.")
        response = self.http.request(
            "POST",
            f"{self.oauth_base_url.rstrip('/')}/oauth/token",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body={
                "provider": ZAI_OAUTH_PROVIDER,
                "code": code,
                "redirect_uri": ZAI_OAUTH_REDIRECT_URI,
                "state": state,
            },
        )
        return self._envelope_data(response, "token exchange")

    def resolve_coding_plan_api_key(self, oauth_access_token: str) -> str:
        login = self._business_data(
            "POST",
            "/api/auth/z/login",
            body={"token": oauth_access_token},
        )
        biz_token = self._required_string(login, "access_token", "business login")
        auth = {"Authorization": f"Bearer {biz_token}", "Content-Type": "application/json"}
        customer = self._business_data(
            "GET", "/api/biz/customer/getCustomerInfo", headers=auth
        )
        organization_id, project_id = self._organization_project(customer)
        key_path = (
            f"/api/biz/v1/organization/{urllib.parse.quote(organization_id, safe='')}"
            f"/projects/{urllib.parse.quote(project_id, safe='')}/api_keys"
        )
        keys = self._business_data("GET", key_path, headers=auth)
        key_id = ""
        if isinstance(keys, list):
            for item in keys:
                if isinstance(item, Mapping) and item.get("name") == ZAI_CODING_PLAN_KEY_NAME:
                    key_id = str(item.get("apiKey") or "").strip()
                    if key_id:
                        break
        if not key_id:
            created = self._business_data(
                "POST", key_path, headers=auth, body={"name": ZAI_CODING_PLAN_KEY_NAME}
            )
            key_id = self._required_string(created, "apiKey", "API-key creation")
        copied = self._business_data(
            "GET",
            f"{key_path}/copy/{urllib.parse.quote(key_id, safe='')}",
            headers=auth,
        )
        secret = self._required_string(copied, "secretKey", "API-key copy")
        return f"{key_id}.{secret}"

    def _oauth_data(
        self,
        method: str,
        url: str,
        poll_token: str,
        *,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        response = self.http.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {poll_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body=body,
        )
        return self._envelope_data(response, "OAuth")

    def _business_data(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        response = self.http.request(
            method,
            f"{self.business_base_url.rstrip('/')}{path}",
            headers=headers or {"Content-Type": "application/json"},
            body=body,
        )
        return self._envelope_data(response, "business API")

    @staticmethod
    def _envelope_data(response: Any, label: str) -> Any:
        if not isinstance(response, Mapping):
            raise ZaiOAuthError(f"Z.AI {label} returned an invalid envelope.")
        code = response.get("code")
        if code not in {None, 0, 200, "0", "200"}:
            raise ZaiOAuthError(f"Z.AI {label} rejected the request (code {code}).")
        return response.get("data")

    @staticmethod
    def _required_string(data: Any, key: str, label: str) -> str:
        value = data.get(key) if isinstance(data, Mapping) else None
        text = str(value or "").strip()
        if not text:
            raise ZaiOAuthError(f"Z.AI {label} response is missing {key}.")
        return text

    @staticmethod
    def _organization_project(data: Any) -> tuple[str, str]:
        organizations = data.get("organizations") if isinstance(data, Mapping) else None
        if not isinstance(organizations, list) or not organizations:
            raise ZaiOAuthError("Z.AI account has no organization available for Coding Plan.")
        organization = next(
            (
                item
                for item in organizations
                if isinstance(item, Mapping) and "默认机构" in str(item.get("organizationName") or "")
            ),
            organizations[0],
        )
        if not isinstance(organization, Mapping):
            raise ZaiOAuthError("Z.AI organization response is invalid.")
        projects = organization.get("projects")
        if not isinstance(projects, list) or not projects:
            raise ZaiOAuthError("Z.AI account has no project available for Coding Plan.")
        project = next(
            (
                item
                for item in projects
                if isinstance(item, Mapping) and "默认项目" in str(item.get("projectName") or "")
            ),
            projects[0],
        )
        organization_id = str(organization.get("organizationId") or "").strip()
        project_id = str(project.get("projectId") or "").strip() if isinstance(project, Mapping) else ""
        if not organization_id or not project_id:
            raise ZaiOAuthError("Z.AI organization/project identifiers are missing.")
        return organization_id, project_id


@dataclass(frozen=True, slots=True)
class ZaiOAuthService:
    client: ZaiOAuthClient
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    open_url: Callable[[str], bool] = webbrowser.open
    read_callback: Callable[[str], str] = input
    random_token: Callable[[], str] = lambda: secrets.token_hex(32)
    timeout_seconds: float = ZAI_OAUTH_TIMEOUT_SECONDS

    def login(self, *, no_browser: bool = False, on_authorize_url: Callable[[str], None]) -> ZaiOAuthResult:
        poll_token = self.random_token()
        try:
            initialized = self.client.initialize(poll_token)
        except ZaiOAuthError as exc:
            if exc.http_status != 404:
                raise
            return self._authorization_code_login(
                state=poll_token,
                no_browser=no_browser,
                on_authorize_url=on_authorize_url,
            )
        on_authorize_url(initialized.authorize_url)
        if not no_browser:
            self.open_url(initialized.authorize_url)
        deadline = min(self.now() + self.timeout_seconds, initialized.expires_at)
        while self.now() < deadline:
            result = self.client.poll(initialized.flow_id, poll_token)
            status = str(result.get("status") or "")
            if status == "failed":
                raise ZaiOAuthError("Z.AI OAuth authorization was denied or failed.")
            if status == "ready":
                return self._resolve_result(result)
            self.sleep(min(initialized.poll_interval_seconds, max(0.0, deadline - self.now())))
        raise ZaiOAuthError("Z.AI OAuth authorization timed out.")

    def _authorization_code_login(
        self,
        *,
        state: str,
        no_browser: bool,
        on_authorize_url: Callable[[str], None],
    ) -> ZaiOAuthResult:
        authorize_url = self.client.authorize_url(state)
        on_authorize_url(authorize_url)
        if not no_browser:
            self.open_url(authorize_url)
        try:
            callback_url = self.read_callback(
                "Paste the complete zcode://zai-auth/callback URL here: "
            ).strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise ZaiOAuthError(
                "Z.AI OAuth callback URL was not provided; login was not changed."
            ) from exc
        if not callback_url:
            raise ZaiOAuthError(
                "Z.AI OAuth callback URL was not provided; login was not changed."
            )
        return self._resolve_result(self.client.exchange_callback(callback_url, state))

    def _resolve_result(self, result: Mapping[str, Any]) -> ZaiOAuthResult:
        zai = result.get("zai")
        access_token = (
            str(zai.get("access_token") or "").strip()
            if isinstance(zai, Mapping)
            else ""
        )
        user = result.get("user")
        user_id = (
            str(user.get("user_id") or "").strip()
            if isinstance(user, Mapping)
            else ""
        )
        if not access_token or not user_id:
            raise ZaiOAuthError(
                "Z.AI OAuth ready response is missing credentials or user identity."
            )
        api_key = self.client.resolve_coding_plan_api_key(access_token)
        return ZaiOAuthResult(api_key=api_key, user_id=user_id)


@dataclass(frozen=True, slots=True)
class ZaiOAuthRuntimePorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    clear_model_cache: Callable[[], None]
    mask: Callable[[str], str]
    fingerprint: Callable[[str], str]
    output: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ZaiOAuthRuntime:
    service: ZaiOAuthService
    ports: ZaiOAuthRuntimePorts

    def token(self) -> str:
        config = self.ports.load_config()
        provider = config.get("providers", {}).get("zai", {})
        if provider.get("credential_source") != "zai-oauth":
            return ""
        return str(provider.get("api_key") or "").strip()

    def import_api_key(self, api_key: str, *, source: str = "zcode") -> list[str]:
        key = str(api_key or "").strip()
        if not key:
            return ["Z.AI OAuth import skipped: no Coding Plan API key was found."]
        config = self.ports.load_config()
        provider = config.setdefault("providers", {}).setdefault("zai", {})
        if (
            str(provider.get("api_key") or "").strip() == key
            and provider.get("credential_source") == "zai-oauth"
        ):
            return ["Z.AI OAuth credential is already shared by all runtimes."]
        provider["api_key"] = key
        provider.pop("api_keys", None)
        provider["credential_source"] = "zai-oauth"
        provider["oauth_authenticated_at"] = datetime.now(timezone.utc).isoformat()
        provider["oauth_import_source"] = str(source or "zcode")
        config["current_provider"] = "zai"
        self.ports.save_config(config)
        self.ports.clear_model_cache()
        return [
            "Z.AI OAuth Coding Plan credential imported into Ciel Runtime for all clients.",
            f"Credential: {self.ports.mask(key)}; fp {self.ports.fingerprint(key)}",
        ]

    def action(self, action: str, *, no_browser: bool = False) -> list[str]:
        if action == "status":
            token = self.token()
            if not token:
                return ["Z.AI OAuth: not connected."]
            return [
                "Z.AI OAuth: connected (Coding Plan API key).",
                f"Credential: {self.ports.mask(token)}; fp {self.ports.fingerprint(token)}",
            ]
        if action == "logout":
            config = self.ports.load_config()
            provider = config.get("providers", {}).get("zai", {})
            if provider.get("credential_source") != "zai-oauth":
                return ["Z.AI OAuth: no OAuth-derived local credential to clear."]
            provider.pop("api_key", None)
            provider.pop("api_keys", None)
            provider.pop("credential_source", None)
            provider.pop("oauth_authenticated_at", None)
            provider.pop("oauth_user_id", None)
            self.ports.save_config(config)
            self.ports.clear_model_cache()
            return ["Z.AI OAuth-derived local credential cleared. Remote authorization was not revoked."]
        if action != "login":
            return [f"Unsupported Z.AI OAuth action: {action}"]
        result = self.service.login(
            no_browser=no_browser,
            on_authorize_url=lambda url: self.ports.output(
                f"Open this URL to authorize Z.AI:\n{url}", flush=True
            ),
        )
        config = self.ports.load_config()
        provider = config.setdefault("providers", {}).setdefault("zai", {})
        provider["api_key"] = result.api_key
        provider.pop("api_keys", None)
        provider["credential_source"] = "zai-oauth"
        provider["oauth_authenticated_at"] = datetime.now(timezone.utc).isoformat()
        provider["oauth_user_id"] = result.user_id
        config["current_provider"] = "zai"
        self.ports.save_config(config)
        self.ports.clear_model_cache()
        return [
            "Z.AI OAuth login completed; the resolved Coding Plan API key is active.",
            f"Credential: {self.ports.mask(result.api_key)}; fp {self.ports.fingerprint(result.api_key)}",
        ]


__all__ = [
    "ZaiOAuthClient",
    "ZaiOAuthError",
    "ZaiOAuthHttp",
    "ZaiOAuthInit",
    "ZaiOAuthResult",
    "ZaiOAuthRuntime",
    "ZaiOAuthRuntimePorts",
    "ZaiOAuthService",
]
