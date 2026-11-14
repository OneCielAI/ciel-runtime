"""Wiring for session API keys, kept out of the composition root (``ciel_runtime.py``).

The composition root passes ports in; this module never imports it.  One
object owns the router-side routing, the handoff scope, the local-client
credential, the client used by ctl/menu/launcher, the launcher's startup-key
handoff, and the ``session-api-key`` command.
"""

from __future__ import annotations

import getpass
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .claude_socket_paths import canonical_socket_path
from .router_access import RouterExternalTokenRepository
from .session_api_key_cli import SessionApiKeyCli, SessionApiKeyCliIO, stdin_hidden_prompt_available
from .session_api_key_client import SessionApiKeyClient, SessionApiKeyClientError
from .session_api_key_http import SessionApiKeyHttpController, SessionApiKeyHttpPorts
from .session_api_key_routing import SessionApiKeyRouting
from .session_api_key_vault import MIN_TTL_SECONDS, ROUTER_SESSION_API_KEYS, SessionApiKeyVault, binding_digest
from .startup_session_key import STARTUP_SESSION_KEYS, StartupSessionKeyHolder
from .workspace_router_selection import _process_identity

LOCAL_CLIENT_FILE = "session-api-key-local-client"


@dataclass(slots=True)
class SessionApiKeyComposition:
    # runtime_compaction_binding.RuntimeCompactionBinding of this process.
    compaction: Any
    instance_dir: Path
    config_dir: Path
    router_base: Callable[[], str]
    admin_token: RouterExternalTokenRepository
    workspace: str
    workspace_identity: Callable[[str], str]
    log: Callable[[str, str], None]
    vault: SessionApiKeyVault = ROUTER_SESSION_API_KEYS
    holder: StartupSessionKeyHolder = STARTUP_SESSION_KEYS
    # pid -> creation token ("" dead, None unknown).
    process_identity: Callable[[int], str | None] = _process_identity
    clock: Callable[[], float] = time.time
    routing: SessionApiKeyRouting = field(init=False)
    # The ctl/menu local-client credential: a second 0600 file in the router
    # instance dir, never sent by the web page or REST clients, so the router
    # can label those sources.  It labels the audit only; any process of this
    # OS user can read it (session_api_key_http._local_client).
    local_client: RouterExternalTokenRepository = field(init=False)

    def __post_init__(self) -> None:
        self.vault.set_log(self.log)
        self.holder.set_log(self.log)
        self.local_client = RouterExternalTokenRepository(
            path=self.instance_dir / LOCAL_CLIENT_FILE, config_dir=self.config_dir, environ={},
            env_name="CIEL_RUNTIME_UNUSED_SESSION_KEY_LOCAL_CLIENT")
        self.routing = SessionApiKeyRouting(
            vault=self.vault,
            launch_binding=lambda headers, body: self.compaction.launch_binding_for_request("claude", headers, body),
            log=self.log, session_owner=self.owner,
            scope_session=lambda scope: self.compaction.published_session(scope))
        self.holder.set_stager(self.stage)

    # -- router process -------------------------------------------------------
    def attach_markers(self) -> None:
        """Router start only: restore what an earlier router lost, then keep the secret-free markers current."""

        from .session_api_key_markers import MARKER_FILE, SessionKeyMarkerStore

        self.vault.attach_markers(SessionKeyMarkerStore(self.instance_dir / MARKER_FILE, self.log))
        # A launch that lost the workspace scope abandons its staged key (vault.refresh_abandoned).
        self.vault.set_scope_probe(lambda: str(self.compaction.current_launch_scope().get("epoch") or ""))

    def launch_scope(self, binding: str) -> dict[str, Any] | None:
        """The router's own launch scope when ``binding`` is its epoch and socket (handoff staging)."""

        scope = self.compaction.current_launch_scope()
        socket, epoch = str(scope.get("messaging_socket_path") or ""), str(scope.get("epoch") or "")
        if not socket or not epoch or binding_digest(canonical_socket_path(socket), epoch) != str(binding or ""):
            return None
        return scope

    def http_controller(self, write_json: Callable[..., Any], session_allowed: Callable[[Any], bool],
                        write_text: Callable[..., Any], workspace_state_dir: Callable[[], Path],
                        current_provider: Callable[[], str]) -> SessionApiKeyHttpController:
        """REST and the page at ``session_api_key_ui.UI_PATH`` for this router."""

        return SessionApiKeyHttpController(SessionApiKeyHttpPorts(
            write_json, self.admin_token.get, session_allowed, workspace_state_dir, current_provider,
            vault=self.vault, session_owner=self.owner,
            write_html=lambda handler, page: write_text(handler, page, 200, "text/html; charset=utf-8"),
            local_client_token=self.local_client.get, launch_scope=self.launch_scope))

    @staticmethod
    def owner(session: str) -> tuple[int, str] | None:
        """The live process of ``session`` from Claude Code's own session record."""

        from .session_api_key_liveness import session_owner

        return session_owner(session)

    # -- clients ----------------------------------------------------------------
    def client(self) -> SessionApiKeyClient:
        return SessionApiKeyClient(self.router_base(), self.admin_token.ensure, self.workspace, self.workspace_identity,
                                   local_client_token=self.local_client.ensure)

    def stage(self, provider: str, socket_path: str, routed: bool) -> str:
        """Hand this launch's startup key to its router; the launcher keeps no copy.

        The router holds it for the launch epoch and random messaging socket
        just bound, and moves it to the session once Claude Code publishes
        that socket's session record.  Without both, the key is dropped
        (fail closed).  The router derives the launch scope itself.
        """

        key = self.holder.take()
        if key is None:
            return "none"
        try:
            epoch = self.compaction.bound_epoch
            ttl = key.ttl_seconds(self.clock())
            refusal = ("direct_or_native_launch" if not routed else "no_messaging_socket" if not socket_path
                       else "provider_mismatch" if key.provider != provider else "no_launch_epoch" if not epoch
                       else "expired" if ttl < MIN_TTL_SECONDS else "")
            if refusal:
                return f"refused:{refusal}"
            self.client().handoff({
                "binding": binding_digest(canonical_socket_path(socket_path), epoch), "provider": key.provider,
                "api_key": key.secret.decode("ascii"), "ttl_seconds": ttl, "source": key.source,
                "fallback": key.fallback, "launcher_pid": os.getpid(),
                "launcher_identity": self.process_identity(os.getpid()) or "",
            })
            return "staged"
        except SessionApiKeyClientError as exc:
            return f"refused:{exc.code}"
        finally:
            key.wipe()

    def command(self, args: Any) -> int:
        """``ciel-runtimectl session-api-key``: see session_api_key_cli (never takes the key as an argument)."""

        return SessionApiKeyCli(self.client, SessionApiKeyCliIO(
            # The Windows NUL device claims it is a tty; require a real console input handle.
            write_line=lambda line: print(line, flush=True), stdin_isatty=stdin_hidden_prompt_available,
            prompt_secret=getpass.getpass, read_stdin=lambda limit: sys.stdin.read(limit),
        )).run(args)


__all__ = ["LOCAL_CLIENT_FILE", "ROUTER_SESSION_API_KEYS", "STARTUP_SESSION_KEYS", "SessionApiKeyComposition"]
