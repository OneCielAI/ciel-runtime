"""Attach router authentication without replacing native OpenAI credentials."""
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from .router_access import is_loopback_address

CLIENT_TOKEN_ENV = "CIEL_RUNTIME_ROUTER_CLIENT_TOKEN"


def authenticated_codex_command(cmd: list[str], env: dict[str, str], router_base: str) -> list[str]:
    router = urlparse(router_base)
    targets = []
    builtin_base = None
    overrides = {}
    index = 1
    while index < len(cmd):
        argument = cmd[index]
        index += 1
        if argument == '--':
            break
        if argument in ('-c', '--config') and index < len(cmd):
            setting = cmd[index]
            index += 1
        elif argument.startswith('--config='):
            setting = argument.split('=', 1)[1]
        else:
            continue
        key, separator, raw = setting.partition('=')
        if separator:
            key = key.strip()
            # Do not attach credentials based on an overridden earlier URL.
            overrides = {old: value for old, value in overrides.items()
                         if not old.startswith(key + '.')}
            overrides[key] = raw
    for key, raw in overrides.items():
        if not raw:
            continue
        match = re.fullmatch(r"model_providers\.([\w-]+)\.base_url", key)
        if match:
            prefix = f"model_providers.{match.group(1)}"
        elif key == "openai_base_url":
            prefix = "model_providers.ciel-runtime-native-auth"
        elif key == "mcp_servers.ciel-runtime-router.url":
            prefix = "mcp_servers.ciel-runtime-router"
        else:
            continue
        try:
            url = urlparse(json.loads(raw))
        except (ValueError, TypeError):
            continue
        # These are Ciel's generated local-router routes, not arbitrary
        # upstream provider URLs. Only authorize this launch's exact origin.
        if (not is_loopback_address(url.hostname) and url.scheme == router.scheme
                and url.netloc == router.netloc
                and url.path.rstrip('/') in ('/v1', '/backend-api/codex', '/ca/mcp')):
            targets.append(prefix)
            if key == 'openai_base_url':
                builtin_base = raw
    if not targets:
        return cmd
    token = str(env.get("CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN") or "").strip()
    if not token:
        state = env.get("CIEL_RUNTIME_STATE_DIR")
        if state:
            try:
                token = (Path(state) / "router-external-token").read_text(encoding="utf-8").strip()
            except OSError:
                pass
    if not token:
        return cmd
    env[CLIENT_TOKEN_ENV] = token
    options = []
    if builtin_base is not None:
        # Modern Codex forbids overriding the built-in `openai` table.
        # Use a per-launch provider retaining native OpenAI authentication.
        provider = 'ciel-runtime-native-auth'
        cmd = [f'model_provider="{provider}"' if item == 'model_provider="openai"' else item for item in cmd]
        for setting in (f'model_provider="{provider}"',
                        f'model_providers.{provider}.name="Ciel Runtime Codex"',
                        f'model_providers.{provider}.base_url={builtin_base}',
                        f'model_providers.{provider}.wire_api="responses"',
                        f'model_providers.{provider}.requires_openai_auth=true'):
            options.extend(['-c', setting])
    for prefix in dict.fromkeys(targets):
        options.extend(['-c', f'{prefix}.env_http_headers.x-ciel-runtime-token="{CLIENT_TOKEN_ENV}"'])
    return [cmd[0], *options, *cmd[1:]]
