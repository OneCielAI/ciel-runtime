"""Remote startup responses may carry this session's key; it never reaches disk.

Instructions: dedicated response headers.  Memory: one dedicated manifest
object.  Each case uses a random synthetic canary and asserts it is absent
from every file the synchronizers wrote and from every log line.
"""

import email.message
import io
import json
import secrets
import tempfile
import unittest
import urllib.error
from pathlib import Path

from ciel_runtime_support.remote_instructions import RemoteInstructionSynchronizer, SynchronizedLaunch
from ciel_runtime_support.remote_memory import RemoteMemorySynchronizer
from ciel_runtime_support.startup_session_key import (
    BINDING_HEADER,
    EXPIRES_AT_HEADER,
    EXPIRES_IN_HEADER,
    KEY_HEADER,
    LAUNCH_BINDING_HEADER,
    PROVIDER_HEADER,
    StartupSessionKeyHolder,
    transient_key_satisfies,
)

INSTRUCTIONS_URL = "https://prompts.example/claude.md"
MANIFEST_URL = "https://memory.example/v1/manifest.json"


def canary() -> str:
    return "sk-ciel-canary-" + secrets.token_hex(20)


def _headers(values: dict) -> email.message.Message:
    message = email.message.Message()
    for name, value in values.items():
        message[name] = value
    return message


class _Response:
    def __init__(self, body: bytes, url: str, headers: dict | None = None):
        self._body, self._url = io.BytesIO(body), url
        self.headers = _headers(headers or {})
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self._body.read(size)

    def geturl(self):
        return self._url


class _Server:
    """Answers by URL; ``reply(request)`` builds (body, final_url, headers) or raises."""

    def __init__(self, reply):
        self.reply, self.requests = reply, []

    def __call__(self, request, **_kwargs):
        self.requests.append(request)
        body, final, headers = self.reply(request)
        return _Response(body, final, headers)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.logs: list[str] = []
        self.holder = StartupSessionKeyHolder(log=lambda _level, message: self.logs.append(message))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def written(self) -> str:
        parts = ["\n".join(self.logs)]
        for path in self.root.rglob("*"):
            if path.is_file():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)

    def assert_nowhere(self, key: str) -> None:
        self.assertFalse(key in self.written(), "synthetic key found in a written file or log line")


class InstructionHeaderTests(Base):
    def sync(self, reply, *, reason="launch", url=INSTRUCTIONS_URL, authorization="Bearer prompt-token"):
        config = {"remote_instructions": {"enabled": True, "claude_url": url, "authorization": authorization}}
        server = _Server(reply)
        service = RemoteInstructionSynchronizer(
            load_config=lambda: config, workspace=lambda: self.workspace, state_dir=self.root / "state",
            log=lambda _level, message: self.logs.append(message), urlopen=server, session_keys=self.holder)
        result = service.sync("claude", reason=reason)
        return result, server

    def key_headers(self, key, binding, **extra):
        return {KEY_HEADER: key, PROVIDER_HEADER: "deepseek", EXPIRES_IN_HEADER: "3600", BINDING_HEADER: binding,
                **extra}

    def test_launch_receives_the_key_from_headers_only(self):
        key = canary()
        nonce = self.holder.begin_launch()
        result, server = self.sync(lambda request: (b"# Company prompt\n", request.full_url,
                                                    self.key_headers(key, nonce)))

        self.assertEqual("updated", result.status)
        self.assertEqual(nonce, server.requests[0].headers.get(LAUNCH_BINDING_HEADER.capitalize()))
        self.assertTrue(self.holder.available_for("deepseek"))
        self.assertEqual("# Company prompt\n", (self.workspace / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assert_nowhere(key)
        taken = self.holder.take()
        self.assertEqual((key, "remote-instructions"), (taken.secret.decode(), taken.source))
        taken.wipe()
        self.assertEqual(b"", bytes(taken.secret))

    def test_unchanged_304_answer_still_carries_the_key(self):
        key = canary()
        (self.workspace / "CLAUDE.md").write_text("# held\n", encoding="utf-8")
        nonce = self.holder.begin_launch()

        def reply(request):
            raise urllib.error.HTTPError(request.full_url, 304, "Not Modified",
                                         _headers(self.key_headers(key, nonce)), io.BytesIO(b""))

        result, _server = self.sync(reply)
        self.assertEqual("unchanged", result.status)
        self.assertTrue(self.holder.available_for("deepseek"))
        self.assert_nowhere(key)

    def test_rejections(self):
        cases = {
            "replayed binding": dict(binding="an-earlier-launch-binding", expect="binding_mismatch"),
            "other origin": dict(final="https://cdn.example/claude.md", expect="origin_changed"),
            "plain http": dict(url="http://prompts.example/claude.md", expect="not_https"),
            "no authorization": dict(authorization="", expect="unauthenticated_origin"),
            "expired": dict(extra={EXPIRES_IN_HEADER: "5"}, expect="expired"),
            "expiry too far": dict(extra={EXPIRES_IN_HEADER: str(10 * 24 * 3600)}, expect="expiry_too_far"),
            "malformed expiry": dict(extra={EXPIRES_IN_HEADER: "soon"}, expect="expiry_malformed"),
            "no expiry": dict(extra={EXPIRES_IN_HEADER: ""}, expect="expiry_missing"),
            "bad provider": dict(extra={PROVIDER_HEADER: "Not A Provider"}, expect="invalid_provider"),
            "short key": dict(key="short", expect="invalid_key"),
            "not a launch": dict(reason="restart", expect=None),
        }
        for name, case in cases.items():
            with self.subTest(name):
                self.holder.discard("next case")
                del self.logs[:]
                key = case.get("key") or canary()
                nonce = self.holder.begin_launch()
                url = case.get("url", INSTRUCTIONS_URL)
                headers = self.key_headers(key, case.get("binding", nonce))
                for header, value in case.get("extra", {}).items():
                    if value:
                        headers[header] = value
                    else:
                        headers.pop(header, None)
                self.sync(lambda _request: (b"# prompt\n", case.get("final", url), headers),
                          reason=case.get("reason", "launch"), url=url,
                          authorization=case.get("authorization", "Bearer prompt-token"))
                self.assertFalse(self.holder.available_for("deepseek"), name)
                self.assert_nowhere(key)
                rejected = [line for line in self.logs if "startup_session_key_rejected" in line]
                if case["expect"] is None:
                    self.assertEqual([], rejected, name)  # never even looked at
                else:
                    self.assertEqual([f"startup_session_key_rejected source=remote-instructions "
                                      f"reason={case['expect']}"], rejected, name)

    def test_plain_http_is_allowed_only_to_a_loopback_test_server(self):
        key = canary()
        nonce = self.holder.begin_launch()
        url = "http://127.0.0.1:18765/claude.md"
        self.sync(lambda _request: (b"# prompt\n", url, self.key_headers(key, nonce)), url=url)
        self.assertTrue(self.holder.available_for("deepseek"))
        self.assert_nowhere(key)

    def test_a_key_in_the_markdown_is_never_a_key(self):
        key = canary()
        self.holder.begin_launch()
        self.sync(lambda _request: (f"# prompt\nX-Ciel-Session-Api-Key: {key}\n".encode(), INSTRUCTIONS_URL, {}))
        self.assertFalse(self.holder.available_for("deepseek"))

    def test_absolute_expiry_header(self):
        key = canary()
        nonce = self.holder.begin_launch()
        headers = self.key_headers(key, nonce)
        del headers[EXPIRES_IN_HEADER]
        import time

        headers[EXPIRES_AT_HEADER] = str(int(time.time()) + 1800)
        self.sync(lambda _request: (b"# prompt\n", INSTRUCTIONS_URL, headers))
        self.assertTrue(self.holder.available_for("deepseek"))


class MemoryManifestTests(Base):
    def sync(self, manifest: dict, *, reason="launch"):
        config = {"remote_memory": {"enabled": True, "manifest_url": MANIFEST_URL,
                                    "authorization": "Bearer memory-token"}}
        files = {MANIFEST_URL: json.dumps(manifest).encode(),
                 "https://memory.example/v1/files/index.okf": b"root: memory\n"}
        server = _Server(lambda request: (files[request.full_url], request.full_url, {}))
        service = RemoteMemorySynchronizer(
            load_config=lambda: config, workspace=lambda: self.workspace, state_dir=self.root / "state",
            log=lambda _level, message: self.logs.append(message), urlopen=server, session_keys=self.holder)
        return service.sync("claude", reason=reason), server

    def manifest(self, key, binding):
        return {"version": 1, "index": "index.okf",
                "files": [{"path": "index.okf", "url": "files/index.okf", "format": "okf"}],
                "session_api_key": {"provider": "deepseek", "api_key": key, "expires_in": 3600, "binding": binding}}

    def test_launch_takes_the_key_object_out_of_the_manifest(self):
        key = canary()
        nonce = self.holder.begin_launch()
        result, server = self.sync(self.manifest(key, nonce))

        self.assertEqual("updated", result.status, result.detail)
        self.assertEqual(nonce, server.requests[0].headers.get(LAUNCH_BINDING_HEADER.capitalize()))
        self.assertTrue(self.holder.available_for("deepseek"))
        self.assertTrue((self.workspace / ".ciel" / "memory" / "index.okf").is_file())
        self.assert_nowhere(key)

    def test_other_syncs_drop_the_object_unused(self):
        for reason in ("manual", "restart"):
            with self.subTest(reason):
                key = canary()
                nonce = self.holder.begin_launch()
                result, _server = self.sync(self.manifest(key, nonce), reason=reason)
                self.assertEqual("updated", result.status, result.detail)
                self.assertFalse(self.holder.available_for("deepseek"))
                self.assert_nowhere(key)

    def test_malformed_and_replayed_objects_are_rejected(self):
        for name, value in (("not an object", "sk-just-a-string"), ("replayed", None)):
            with self.subTest(name):
                key = canary()
                self.holder.begin_launch()
                manifest = self.manifest(key, "an-earlier-launch-binding")
                if value is not None:
                    manifest["session_api_key"] = value
                result, _server = self.sync(manifest)
                self.assertEqual("updated", result.status, result.detail)
                self.assertFalse(self.holder.available_for("deepseek"))
                self.assert_nowhere(key)


class LaunchLifetimeTests(Base):
    def test_a_key_the_launch_did_not_hand_over_dies_with_it(self):
        key = canary()
        seen = []

        def synchronize(_runtime, *, reason):
            nonce = self.holder.launch_binding()
            self.holder.accept_headers(_headers({KEY_HEADER: key, PROVIDER_HEADER: "deepseek",
                                                 EXPIRES_IN_HEADER: "600", BINDING_HEADER: nonce}),
                                       configured_url=INSTRUCTIONS_URL, final_url=INSTRUCTIONS_URL, authorized=True)

        def delegate():
            seen.append(self.holder.available_for("deepseek"))
            return 0

        SynchronizedLaunch(delegate, synchronize, "claude", release=lambda: None, session_keys=self.holder)()
        self.assertEqual([True], seen)
        self.assertFalse(self.holder.available_for("deepseek"))

        other = []
        SynchronizedLaunch(lambda: other.append(self.holder.launch_binding()), lambda *_a, **_k: None, "codex",
                           release=lambda: None, session_keys=self.holder)()
        self.assertEqual([""], other)  # only a Claude launch asks for a key

    def test_readiness_counts_a_held_key_only_for_missing_key_blockers(self):
        self.holder.begin_launch()
        nonce = self.holder.launch_binding()
        self.holder.accept_headers(_headers({KEY_HEADER: canary(), PROVIDER_HEADER: "deepseek",
                                             EXPIRES_IN_HEADER: "600", BINDING_HEADER: nonce}),
                                   configured_url=INSTRUCTIONS_URL, final_url=INSTRUCTIONS_URL, authorized=True)
        missing = ["Launch blocked: DeepSeek requires an API key."]
        self.assertTrue(transient_key_satisfies(missing, "deepseek", holder=self.holder))
        self.assertFalse(transient_key_satisfies(missing, "zai", holder=self.holder))
        self.assertFalse(transient_key_satisfies(missing + ["Base URL is missing."], "deepseek", holder=self.holder))
        self.assertFalse(transient_key_satisfies([], "deepseek", holder=self.holder))


if __name__ == "__main__":
    unittest.main()
