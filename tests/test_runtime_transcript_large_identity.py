"""Exact Codex identity from a rollout whose first line exceeds 64 KiB.

Codex 0.158 writes the whole native identity into the first ``session_meta``
record and appends ``base_instructions`` to the same physical line, so a large
instruction payload makes the line longer than any bounded ``readline``.  These
tests build real-shaped rollout fixtures (``sessions/YYYY/MM/DD/rollout-<iso>-
<uuid>.jsonl``, filename UUID equal to the payload ``id``/``session_id``, a
matching ``cwd`` and a following ``compacted`` record) and assert that the
identity reader resolves the exact id without trusting substrings or nested
values.  No live transcript, cursor, queue, process or remote host is touched.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.channel_transcript_repository import (
    ChannelTranscriptRepository,
    read_codex_session_meta,
)
from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding

HEADER_BYTES = 64 * 1024
SESSION_ID = "01a13917-0e0f-7072-97dd-962632a48c2a"
OTHER_ID = "01a13917-0e0f-7072-97dd-000000000002"


def session_meta(session_id, cwd, *, instructions="small", payload_order=None,
                 record_type="session_meta", sidechain=False, extra_payload=None):
    """The field set Codex 0.158 writes to the first rollout record."""

    payload = {
        "session_id": session_id,
        "id": session_id,
        "cwd": str(cwd),
        "timestamp": "2026-10-21T00:00:00Z",
        "originator": "codex_cli_rs",
        "cli_version": "0.158.0",
        "source": "cli",
        "thread_source": "user",
        "model_provider": "openai",
        "context_window": 272000,
        "base_instructions": instructions,
    }
    if extra_payload:
        payload.update(extra_payload)
    if payload_order:
        payload = {key: payload[key] for key in payload_order if key in payload}
    record = {
        "timestamp": "2026-10-21T00:00:00Z",
        "ordinal": 0,
        "type": record_type,
        "payload": payload,
    }
    if sidechain:
        record["isSidechain"] = True
    return record


def compacted(session_id):
    return {
        "type": "compacted",
        "timestamp": "2026-10-21T00:00:01Z",
        "payload": {
            "replacement_history": [{"type": "message", "role": "user", "content": "handoff"}],
            "latest_token_usage_record": {"thread_id": session_id, "input_tokens": 120},
        },
    }


def rollout_path(codex_home, session_id):
    return (
        Path(codex_home) / "sessions" / "2026" / "10" / "21"
        / f"rollout-2026-10-21T00-00-00-{session_id}.jsonl"
    )


def write_rollout(codex_home, session_id, *, instructions="small", cwd=None,
                  mtime=None, **record_options):
    path = rollout_path(codex_home, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [session_meta(session_id, cwd or codex_home, instructions=instructions,
                            **record_options), compacted(session_id)]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class LargeIdentityHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.codex = self.root / "codex"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.logs = []

    def binding(self, name, session_id=None, cwd=None, codex_home=None):
        budget = SimpleNamespace(
            context_limit=lambda *_: 20000, positive_int=lambda value: int(value or 0),
            configured_output=lambda *_: 4000, cap_output_ratio=lambda *_: 4000,
            reserve=lambda *_: 1000,
            compact_kind=lambda body: "native" if body.get("native") else None,
        )
        requests = SimpleNamespace(queue_automatic=lambda *_: None, clear=lambda *_: None)
        binding = RuntimeCompactionBinding(
            self.root / name, self.home, requests,
            lambda: SimpleNamespace(budget=budget), lambda body: body.get("tokens", 100),
            lambda: ("other", {}), lambda *args: self.logs.append(args),
        )
        binding.bind("codex", session_id=session_id, cwd=cwd or self.workspace,
                     codex_home=codex_home or self.codex)
        return binding

    def repository(self, **scope):
        state = {"runtime": "codex", "started_at": 100.0, "codex_home": self.codex}
        state.update(scope)
        return ChannelTranscriptRepository(self.home, {}, state, lambda: 1000.0)


class LargeSessionMetaIdentityTests(LargeIdentityHarness):
    def test_real_shaped_large_session_meta_resolves_exact_identity(self):
        instructions = "base instructions\n" + "z" * (96 * 1024)
        path = write_rollout(self.codex, SESSION_ID, instructions=instructions,
                             cwd=self.workspace)
        first = path.read_text(encoding="utf-8").splitlines()[0]
        self.assertGreater(len(first.encode("utf-8")), HEADER_BYTES)
        self.assertEqual(SESSION_ID, json.loads(first)["payload"]["id"])
        self.assertEqual(
            SESSION_ID,
            path.name.split("rollout-2026-10-21T00-00-00-")[1].removesuffix(".jsonl"))

        meta = read_codex_session_meta(path)
        self.assertIsNotNone(meta)
        self.assertEqual("session_meta", meta.record_type)
        self.assertFalse(meta.is_sidechain)
        self.assertEqual(str(self.workspace), meta.cwd)
        self.assertEqual(SESSION_ID, meta.identity)
        self.assertEqual(1792540800.0, meta.started_at)

        self.assertEqual(
            (1792540800.0, str(self.workspace), SESSION_ID),
            ChannelTranscriptRepository._codex_session_identity(path))
        self.assertEqual(
            SESSION_ID, RuntimeCompactionBinding._transcript_session(path, "codex"))

        binding = self.binding("large-resolved", session_id=SESSION_ID)
        self.assertEqual(path, binding.transcript_path())
        # The identity stage, not the control phase, is what this fix owns:
        # the resolved transcript is readable as proof.
        from ciel_runtime_support.runtime_compaction_control import (
            native_checkpoint_from_transcript,
        )
        self.assertIsNotNone(
            native_checkpoint_from_transcript(path, binding.scope()))

    def test_identity_does_not_depend_on_payload_field_order(self):
        # base_instructions before the id is the adversarial order for any
        # prefix- or substring-based reader.
        order = ["base_instructions", "session_id", "id", "cwd", "timestamp"]
        path = write_rollout(self.codex, SESSION_ID, instructions="q" * (70 * 1024),
                             cwd=self.workspace, payload_order=order)
        self.assertGreater(len(path.read_bytes().split(b"\n")[0]), HEADER_BYTES)
        self.assertEqual(SESSION_ID,
                         RuntimeCompactionBinding._transcript_session(path, "codex"))
        self.assertEqual(SESSION_ID, read_codex_session_meta(path).identity)

    def test_embedded_and_nested_ids_never_select_the_session(self):
        forged = SESSION_ID
        marker = "PRIVATE-BASE-INSTRUCTIONS"
        instructions = (
            'before "id": "%s" %s after ' % (forged, marker)) + "x" * (70 * 1024)
        nested = {"git": {"id": forged}, "source": {"id": forged}}
        order = ["base_instructions", "git", "source", "id", "session_id", "cwd"]
        path = rollout_path(self.codex, OTHER_ID)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = session_meta(OTHER_ID, self.workspace, instructions=instructions,
                             extra_payload=nested, payload_order=order)
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        meta = read_codex_session_meta(path)
        self.assertEqual(OTHER_ID, meta.identity)
        self.assertEqual(OTHER_ID,
                         RuntimeCompactionBinding._transcript_session(path, "codex"))
        # A scope bound to the forged value must not accept this file, and the
        # nested ``git``/``source`` ids must not be mistaken for the session.
        self.assertIsNone(self.binding("forged", session_id=forged).transcript_path())
        self.assertIsNone(self.repository(session_id=forged).latest(ttl_seconds=0))
        # Identity reads retain no payload text in logs.
        self.assertFalse(any(marker in str(entry) for entry in self.logs))


class ScanAndSelectionTests(LargeIdentityHarness):
    def test_large_rollout_is_selected_by_exact_id_and_workspace(self):
        target = write_rollout(self.codex, SESSION_ID, instructions="t" * (256 * 1024),
                               cwd=self.workspace, mtime=200)
        competitor = write_rollout(self.codex, OTHER_ID, cwd=self.workspace, mtime=300)
        stranger = write_rollout(self.codex, "01a13917-0e0f-7072-97dd-000000000003",
                                 instructions="s" * (90 * 1024),
                                 cwd=self.root / "other", mtime=400)

        started = time.monotonic()
        selected = self.repository(session_id=SESSION_ID).latest(ttl_seconds=0)
        elapsed = time.monotonic() - started
        self.assertEqual(target, selected)
        # The repository scan runs under a 2.5 s worker deadline; selecting the
        # large-header file proves the bounded read stayed inside it.
        self.assertLess(elapsed, 2.5)

        # Without an explicit id the same workspace decides, and the older
        # large-header file is still not selected over a newer sibling.
        self.assertEqual(competitor,
                         self.repository(cwd=self.workspace).latest(ttl_seconds=0))
        self.assertEqual(str(self.root / "other"),
                         read_codex_session_meta(stranger).cwd)
        self.assertEqual(target, self.binding("scan", session_id=SESSION_ID).transcript_path())


class LargeIdentityRejectionTests(LargeIdentityHarness):
    def test_conflicting_direct_identity_fields_fail_closed(self):
        for length in (1, 70 * 1024):
            with self.subTest(instruction_length=length):
                path = write_rollout(self.codex, SESSION_ID, instructions="x" * length,
                                     cwd=self.workspace)
                records = path.read_text(encoding="utf-8").splitlines()
                header = json.loads(records[0])
                header["payload"]["id"] = OTHER_ID
                records[0] = json.dumps(header)
                path.write_text("\n".join(records) + "\n", encoding="utf-8")
                self.assertIsNone(read_codex_session_meta(path))
                self.assertEqual((None, "", ""), ChannelTranscriptRepository._codex_session_identity(path))
                self.assertEqual("", RuntimeCompactionBinding._transcript_session(path, "codex"))

    def test_wrong_native_id_is_rejected_though_the_header_is_large(self):
        path = write_rollout(self.codex, OTHER_ID, instructions="w" * (70 * 1024),
                             cwd=self.workspace)
        self.assertGreater(len(path.read_bytes().split(b"\n")[0]), HEADER_BYTES)
        self.assertEqual(OTHER_ID,
                         RuntimeCompactionBinding._transcript_session(path, "codex"))
        self.assertIsNone(self.binding("wrong", session_id=SESSION_ID).transcript_path())
        self.assertIsNone(self.repository(session_id=SESSION_ID).latest(ttl_seconds=0))

    def test_malformed_or_non_session_meta_header_is_unresolved(self):
        path = write_rollout(self.codex, SESSION_ID, instructions="m" * (70 * 1024),
                             cwd=self.workspace)
        raw = path.read_bytes()
        truncated = raw[: len(raw) - 500]
        path.write_bytes(truncated)
        self.assertIsNone(read_codex_session_meta(path))
        self.assertEqual((None, "", ""),
                         ChannelTranscriptRepository._codex_session_identity(path))
        self.assertEqual("", RuntimeCompactionBinding._transcript_session(path, "codex"))
        self.assertIsNone(self.binding("truncated", session_id=SESSION_ID).transcript_path())

        non_meta = write_rollout(self.codex / "event", SESSION_ID,
                                 instructions="e" * (70 * 1024),
                                 cwd=self.workspace, record_type="event_msg")
        self.assertEqual("", RuntimeCompactionBinding._transcript_session(non_meta, "codex"))

        sidechain = write_rollout(self.codex / "side", SESSION_ID,
                                  instructions="c" * (70 * 1024),
                                  cwd=self.workspace, sidechain=True)
        self.assertEqual("", RuntimeCompactionBinding._transcript_session(sidechain, "codex"))

    def test_record_beyond_the_memory_bound_stays_unresolved(self):
        path = write_rollout(self.codex, SESSION_ID, instructions="b" * (70 * 1024),
                             cwd=self.workspace)
        self.assertIsNone(read_codex_session_meta(path, max_bytes=64 * 1024))
        self.assertIsNotNone(read_codex_session_meta(path, max_bytes=128 * 1024))
        # The bound applies to the streaming path too.
        self.assertIsNone(read_codex_session_meta(path, max_bytes=65 * 1024))

    def test_nesting_beyond_the_depth_bound_stays_unresolved(self):
        nested = json.loads("[" * 200 + "]" * 200)
        record = session_meta(SESSION_ID, self.workspace,
                              instructions="d" * (70 * 1024),
                              extra_payload={"deep": nested})
        path = rollout_path(self.codex, SESSION_ID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        self.assertIsNone(read_codex_session_meta(path))


class StreamingParserBoundaryTests(LargeIdentityHarness):
    @staticmethod
    def reference(raw):
        record = json.loads(raw)
        payload = record.get("payload") or {}
        return (
            record.get("type"),
            record.get("isSidechain") is True,
            str(payload.get("cwd") or ""),
            str(payload.get("id") or payload.get("session_id") or ""),
        )

    def test_streaming_identity_matches_json_loads_across_chunk_boundaries(self):
        # Chunk boundaries at 64 KiB intervals must not split an escape or a
        # multibyte character into a wrong identity.
        multibyte = "中\U0001F600"
        for base in (HEADER_BYTES, 2 * HEADER_BYTES):
            probe = session_meta(SESSION_ID, self.workspace, instructions=multibyte)
            offset = json.dumps(probe, ensure_ascii=False).encode("utf-8").index(
                multibyte.encode("utf-8"))
            for shift in (0, 1, 2, 3):
                pad = base - offset + shift
                record = session_meta(
                    SESSION_ID, self.workspace,
                    instructions="x" * pad + multibyte + "z" * 64)
                raw = json.dumps(record, ensure_ascii=False).encode("utf-8")
                self.assertGreater(len(raw), HEADER_BYTES)
                path = self.codex / f"boundary-{base}-{shift}.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw + b"\n{}\n")
                meta = read_codex_session_meta(path)
                self.assertIsNotNone(meta)
                self.assertEqual(
                    self.reference(raw),
                    (meta.record_type, meta.is_sidechain, meta.cwd, meta.identity),
                    (base, shift))


if __name__ == "__main__":
    unittest.main()
