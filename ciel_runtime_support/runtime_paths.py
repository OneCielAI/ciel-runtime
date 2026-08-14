"""Platform-aware filesystem and local router endpoint configuration."""
from __future__ import annotations

import getpass
import hashlib
import os
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

from .web_endpoints import apply_startup_web_options, load_saved_web_backend
from .workspace_router_selection import (
    select_workspace_router_port,
    workspace_digest,
    workspace_identity,
)
from .workspace_state import migrate_workspace_state


# Web startup flags affect the process-wide router endpoint constants below.
sys.argv[:] = apply_startup_web_options(sys.argv, os.environ)


HOME = Path.home()


def platform_path(value: str | os.PathLike[str]) -> Any:
    if os.name == "nt" and sys.platform != "win32":
        return PureWindowsPath(value)
    return Path(value)


def windows_appdata_root() -> Path:
    for env_name in ("APPDATA", "LOCALAPPDATA"):
        raw = os.environ.get(env_name)
        if raw:
            return platform_path(raw)
    return HOME / "AppData" / "Roaming"


def windows_local_appdata_root() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return platform_path(raw)
    return HOME / "AppData" / "Local"


def platform_config_dir(app_name: str) -> Path:
    if os.name == "nt":
        return windows_appdata_root() / app_name
    return HOME / ".config" / app_name


def ciel_runtime_user_bin_dir() -> Path:
    if os.name == "nt":
        return windows_local_appdata_root() / "ciel-runtime" / "bin"
    return HOME / ".local" / "bin"


def agy_user_bin_dir() -> Path:
    if os.name == "nt":
        return windows_local_appdata_root() / "agy" / "bin"
    return HOME / ".local" / "bin"


def path_with_ciel_runtime_user_dirs(env: dict[str, str]) -> str:
    dirs = [ciel_runtime_user_bin_dir(), agy_user_bin_dir()]
    if os.name == "nt":
        appdata = env.get("APPDATA") or os.environ.get("APPDATA")
        if appdata:
            dirs.append(platform_path(appdata) / "npm")
        local_appdata = env.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA")
        if local_appdata:
            dirs.append(platform_path(local_appdata) / "Programs" / "nodejs")
    existing = env.get("PATH", "")
    prefix = os.pathsep.join(str(path) for path in dirs if str(path))
    return prefix + (os.pathsep + existing if existing else "")


def default_router_port(saved_port: int = 0) -> int:
    configured = str(os.environ.get("CIEL_RUNTIME_ROUTER_PORT") or "").strip()
    if configured:
        try:
            port = int(configured)
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
    if 1 <= saved_port <= 65535:
        return saved_port
    base = 8799
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        try:
            return base + (int(getuid()) % 1000)
        except Exception:
            pass
    seed = f"{getpass.getuser()}|{HOME}"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()
    return base + (int(digest[:8], 16) % 1000)


CONFIG_DIR = Path(os.environ.get("CIEL_RUNTIME_CONFIG_DIR") or platform_config_dir("ciel-runtime"))
LEGACY_CONFIG_PATH = CONFIG_DIR / "config.json"
ROUTER_WORKSPACE = workspace_identity(
    os.environ.get("CIEL_RUNTIME_LAUNCH_CWD") or Path.cwd()
)
_WORKSPACE_DIGEST = workspace_digest(ROUTER_WORKSPACE)
ROUTER_WORKSPACE_ID = _WORKSPACE_DIGEST
_TEST_STATE_ISOLATED = str(os.environ.get("CIEL_RUNTIME_TEST_ISOLATED") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
WORKSPACE_STATE_DIR = (
    CONFIG_DIR
    if _TEST_STATE_ISOLATED
    else CONFIG_DIR / "workspaces" / _WORKSPACE_DIGEST
)
WORKSPACE_CONFIG_PATH = WORKSPACE_STATE_DIR / "config.json"
CONFIG_PATH = WORKSPACE_CONFIG_PATH
_SAVED_WEB_BACKEND = load_saved_web_backend(
    CONFIG_PATH if CONFIG_PATH.exists() else LEGACY_CONFIG_PATH
)
ROUTER_HOST = (
    os.environ.get("CIEL_RUNTIME_ROUTER_CLIENT_HOST", "").strip()
    or _SAVED_WEB_BACKEND.client_host
)
_BASE_ROUTER_PORT = default_router_port(_SAVED_WEB_BACKEND.port)
WORKSPACE_ROUTER_REGISTRY_PATH = CONFIG_DIR / "workspace-router-ports.json"
ROUTER_PORT = select_workspace_router_port(
    _BASE_ROUTER_PORT,
    Path.cwd(),
    os.environ,
    registry_path=WORKSPACE_ROUTER_REGISTRY_PATH,
)
ROUTER_BASE = f"http://{ROUTER_HOST}:{ROUTER_PORT}"
ROUTER_INSTANCE_ID = f"{ROUTER_PORT}-{_WORKSPACE_DIGEST}"
_STATE_DIR_OVERRIDE = str(os.environ.get("CIEL_RUNTIME_STATE_DIR") or "").strip()
ROUTER_INSTANCE_DIR = (
    Path(_STATE_DIR_OVERRIDE)
    if _STATE_DIR_OVERRIDE
    else CONFIG_DIR
    if _TEST_STATE_ISOLATED
    else CONFIG_DIR / "router-instances" / ROUTER_INSTANCE_ID
)
MIGRATED_LEGACY_INSTANCE_ID = (
    ""
    if _TEST_STATE_ISOLATED
    else migrate_workspace_state(CONFIG_DIR, _WORKSPACE_DIGEST, WORKSPACE_STATE_DIR)
)
LOG_PATH = ROUTER_INSTANCE_DIR / "router.log"
LOG_LEVEL_PATH = CONFIG_DIR / "log-level"
REQUEST_DUMP_PATH = ROUTER_INSTANCE_DIR / "requests.jsonl"
RESPONSE_DUMP_PATH = ROUTER_INSTANCE_DIR / "responses.jsonl"
USAGE_EVENTS_PATH = ROUTER_INSTANCE_DIR / "usage-events.jsonl"
SSE_TRACE_PATH = ROUTER_INSTANCE_DIR / "router-sse-trace.jsonl"
SSE_LAST_PATH = ROUTER_INSTANCE_DIR / "router-last-sse.json"
TOOL_CALL_LOG_PATH = ROUTER_INSTANCE_DIR / "tool-calls.jsonl"
RATE_LIMIT_STATE_PATH = ROUTER_INSTANCE_DIR / "rate-limit-state.json"
ROUTER_ACTIVITY_PATH = ROUTER_INSTANCE_DIR / "router-activity.json"
CONTEXT_COMPACT_ACTIVITY_PATH = ROUTER_INSTANCE_DIR / "context-compact-activity.json"
CONTEXT_USAGE_PATH = ROUTER_INSTANCE_DIR / "context-usage.json"
OLLAMA_MODEL_CATALOG_PATH = CONFIG_DIR / "ollama-model-catalog.json"
CHAT_MESSAGES_PATH = WORKSPACE_STATE_DIR / "chat-messages.jsonl"
RUNTIME_INPUTS_PATH = WORKSPACE_STATE_DIR / "runtime-inputs.jsonl"
CHAT_FILES_DIR = WORKSPACE_STATE_DIR / "chat-files"
MENU_KEY_DEBUG_PATH = ROUTER_INSTANCE_DIR / "ca-key-debug.log"
PLAN_ARTIFACTS_DIR = WORKSPACE_STATE_DIR / "plan-artifacts"
PID_PATH = ROUTER_INSTANCE_DIR / "router.pid"
ROUTER_EXTERNAL_TOKEN_PATH = ROUTER_INSTANCE_DIR / "router-external-token"
ROUTER_CLIENTS_DIR = ROUTER_INSTANCE_DIR / "router-clients"
MODEL_LIST_CACHE_PATH = CONFIG_DIR / "model-list-cache.json"
MODEL_REGISTRY_PATH = CONFIG_DIR / "model-registry.json"
LAUNCH_STATE_PATH = CONFIG_DIR / "launch-state.json"
WEB_TOOLS_MCP_CONFIG = ROUTER_INSTANCE_DIR / "web-tools-mcp.json"
DUCKDUCKGO_MCP_CONFIG = ROUTER_INSTANCE_DIR / "duckduckgo-mcp.json"
ZAI_MCP_CONFIG = ROUTER_INSTANCE_DIR / "zai-mcp.json"
CHANNEL_MCP_CONFIG = ROUTER_INSTANCE_DIR / "channel-mcp.json"
CODEX_PROCESS_DIR = ROUTER_INSTANCE_DIR / "codex-processes"
CODEX_PROMPTS_DIR_NAME = "prompts"
CHANNEL_LLM_CURSOR_PATH = WORKSPACE_STATE_DIR / "channel-llm-cursor.json"
CHANNEL_LLM_CLEAR_FLOOR_PATH = WORKSPACE_STATE_DIR / "channel-llm-clear-floor.json"
CHANNEL_LLM_LAUNCH_GUARD_PATH = WORKSPACE_STATE_DIR / "channel-llm-launch-guard.json"
CHANNEL_COMPACT_REQUEST_PATH = ROUTER_INSTANCE_DIR / "channel-compact-request.json"
CHANNEL_STDIN_WAKE_CLAIMS_PATH = WORKSPACE_STATE_DIR / "channel-stdin-wake-claims.json"
CLAUDE_GATEWAY_CACHE = HOME / ".claude" / "cache" / "gateway-models.json"
CLAUDE_SETTINGS_PATH = HOME / ".claude" / "settings.json"
CLAUDE_COMMANDS_DIR = HOME / ".claude" / "commands"
CIEL_RUNTIME_STATUSLINE_PATH = ciel_runtime_user_bin_dir() / "ciel-runtime-statusline.py"
NCP_ENV = platform_config_dir("nvd-claude-proxy") / ".env"
NCP_LOG = platform_config_dir("nvd-claude-proxy") / "proxy.log"


__all__ = [name for name in globals() if name.isupper() or name in {
    "agy_user_bin_dir", "ciel_runtime_user_bin_dir", "default_router_port",
    "path_with_ciel_runtime_user_dirs", "platform_config_dir", "platform_path",
    "windows_appdata_root", "windows_local_appdata_root",
}]
