import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.session_backup_cli import BackupContext, run_backup_command
from ciel_runtime_support.session_backup_collect import (
    CollectOptions,
    claude_project_key,
    default_roots,
)
from ciel_runtime_support.session_backup_ops import create_snapshot, restore_snapshot, verify_snapshot, destination_roots
from ciel_runtime_support.session_backup_secrets import open_sealed, seal, split_secret_fields, merge_secret_fields
from ciel_runtime_support.session_backup_store import CHUNK_SIZE, ChunkWriter, LocalTarget, find_snapshot, list_snapshots
from ciel_runtime_support.session_backup_targets import S3Target, build_target

KEY = b"backup-passphrase-for-tests"


class Fixture:
    """A home with a Claude project, a Codex thread, Ciel state and a work folder."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.home = base / "home"
        self.cwd = base / "work"
        self.config = base / "ciel"
        self.codex = self.home / ".codex"
        self.claude = self.home / ".claude"
        self.backups = base / "backups"
        self.cwd.mkdir(parents=True)
        (self.cwd / "notes.md").write_text("plan v1\n", encoding="utf-8")
        (self.cwd / "node_modules").mkdir()
        (self.cwd / "node_modules" / "big.js").write_text("x", encoding="utf-8")
        project = self.claude / "projects" / claude_project_key(str(self.cwd))
        project.mkdir(parents=True)
        self.transcript = project / "s1.jsonl"
        self.transcript.write_bytes(b'{"n":1}\n{"n":2}\n{"partial"')
        (project / "s1" / "subagents").mkdir(parents=True)
        (project / "s1" / "subagents" / "agent-a.jsonl").write_text('{"a":1}\n', encoding="utf-8")
        (self.claude / "settings.json").write_text('{"model":"opus"}', encoding="utf-8")
        (self.claude / ".credentials.json").write_text('{"claudeAiOauth":{"accessToken":"secret-claude"}}', encoding="utf-8")
        (self.claude / "shell-snapshots").mkdir()
        (self.claude / "shell-snapshots" / "x.sh").write_text("skip", encoding="utf-8")
        (self.home / ".claude.json").write_text(json.dumps({"projects": {}, "primaryApiKey": "sk-ant-secret"}), encoding="utf-8")
        self.codex.mkdir(parents=True)
        (self.codex / "config.toml").write_text('model = "gpt-6.1-sol"\n', encoding="utf-8")
        (self.codex / "auth.json").write_text('{"OPENAI_API_KEY":"sk-codex-secret"}', encoding="utf-8")
        rollout = self.codex / "sessions" / "2026" / "10" / "09" / "rollout-t1.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text('{"type":"session_meta"}\n', encoding="utf-8")
        other = rollout.with_name("rollout-other.jsonl")
        other.write_text('{"type":"session_meta"}\n', encoding="utf-8")
        db = sqlite3.connect(self.codex / "state_5.sqlite")
        db.execute("CREATE TABLE threads (id TEXT, cwd TEXT, title TEXT, rollout_path TEXT, updated_at INTEGER)")
        db.execute("INSERT INTO threads VALUES ('t1', ?, 'mine', ?, 2)", (str(self.cwd), str(rollout)))
        db.execute("INSERT INTO threads VALUES ('t2', ?, 'other', ?, 1)", (str(self.base / "elsewhere"), str(other)))
        db.commit()
        db.close()
        self.config.mkdir()
        (self.config / "config.json").write_text(
            json.dumps({"current_provider": "codex", "providers": {"zai": {"api_key": "zai-secret", "current_model": "glm"}}}),
            encoding="utf-8",
        )
        (self.config / "launch-state.json").write_text(json.dumps({"by_cwd": {str(self.cwd): {"mode": "codex-remote-router", "pid": 0}}}), encoding="utf-8")
        roots = self.roots()
        roots.ciel_ws.mkdir(parents=True)
        (roots.ciel_ws / "chat-messages.jsonl").write_text('{"id":1}\n', encoding="utf-8")
        (roots.ciel_ws / "oauth-tokens.vault.json").write_text('{"tokens":"enc"}', encoding="utf-8")
        (roots.ciel_ws / "oauth-tokens.vault.key").write_bytes(b"k" * 32)

    def roots(self):
        return default_roots(self.cwd, environ={}, home=self.home, asset_home=self.home, config_dir=self.config)

    def context(self, outputs, environ=None):
        return BackupContext(self.config, self.home, self.home, environ or {}, {"ciel_runtime": "test"},
                             cwd=lambda: self.cwd, output=outputs.append)


class SessionBackupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self._tmp.name))
        self.target = LocalTarget(self.fx.backups, "local")

    def tearDown(self):
        self._tmp.cleanup()

    def create(self, key=KEY, **options):
        return create_snapshot(self.fx.roots(), [self.target], CollectOptions(**options), key=key,
                               versions={"ciel_runtime": "test"}, host="host", user="user")

    def entries(self, manifest):
        return {(entry["root"], entry["path"]): entry for entry in manifest["entries"]}

    def test_snapshot_collects_the_session_and_leaves_out_noise(self):
        manifest = self.create().manifest
        entries = self.entries(manifest)
        slug = claude_project_key(str(self.fx.cwd))
        self.assertEqual("jsonl", entries[("claude", f"projects/{slug}/s1.jsonl")]["kind"])
        self.assertIn(("claude", f"projects/{slug}/s1/subagents/agent-a.jsonl"), entries)
        self.assertIn(("codex", "sessions/2026/10/09/rollout-t1.jsonl"), entries)
        self.assertNotIn(("codex", "sessions/2026/10/09/rollout-other.jsonl"), entries)
        self.assertEqual("sqlite", entries[("codex", "state_5.sqlite")]["kind"])
        self.assertIn(("cwd", "notes.md"), entries)
        self.assertNotIn(("cwd", "node_modules/big.js"), entries)
        self.assertNotIn(("claude", "shell-snapshots/x.sh"), entries)
        self.assertEqual("secret-file", entries[("claude", ".credentials.json")]["kind"])
        self.assertEqual("json-public", entries[("ciel", "config.json")]["kind"])
        self.assertEqual({"latest": "t1", "threads": [{"id": "t1", "title": "mine", "updated_at": 2}]}, manifest["sessions"]["codex"])
        self.assertEqual("s1", manifest["sessions"]["claude"]["latest"])
        self.assertEqual("codex-remote-router", manifest["sessions"]["launch"]["mode"])

    def test_partial_last_line_is_not_captured(self):
        manifest = self.create().manifest
        slug = claude_project_key(str(self.fx.cwd))
        self.assertEqual(len(b'{"n":1}\n{"n":2}\n'), self.entries(manifest)[("claude", f"projects/{slug}/s1.jsonl")]["size"])

    def test_no_secret_value_reaches_plain_chunks_or_manifest(self):
        result = self.create()
        stored = b"".join(path.read_bytes() for path in self.fx.backups.rglob("*") if path.is_file())
        import zlib

        plain = b""
        for path in (self.fx.backups / "blobs").rglob("*"):
            if path.is_file():
                plain += zlib.decompress(path.read_bytes())
        for secret in (b"secret-claude", b"sk-ant-secret", b"sk-codex-secret", b"zai-secret", b"k" * 32):
            self.assertNotIn(secret, plain)
            self.assertNotIn(secret, stored)
        self.assertTrue(result.manifest["secrets"]["included"])

    def test_without_a_key_secrets_are_listed_but_not_stored(self):
        manifest = self.create(key=None).manifest
        self.assertFalse(manifest["secrets"]["included"])
        self.assertIn("claude/.credentials.json", manifest["secrets"]["names"])
        self.assertNotIn("chunks", manifest["secrets"])

    def test_second_snapshot_uploads_only_what_changed(self):
        first = self.create()
        with self.fx.transcript.open("ab") as stream:
            stream.write(b'}\n{"n":4}\n')
        second = self.create()
        self.assertGreater(first.stats["uploaded_chunks"], 3)
        self.assertLessEqual(second.stats["uploaded_chunks"], 2)

    def test_restore_brings_back_files_and_secrets(self):
        manifest = self.create().manifest
        (self.fx.cwd / "notes.md").write_text("broken\n", encoding="utf-8")
        (self.fx.claude / ".credentials.json").unlink()
        (self.fx.home / ".claude.json").unlink()
        result = restore_snapshot(self.target, manifest, destination_roots(manifest, {}), key=KEY)
        self.assertTrue(result["secrets_restored"])
        self.assertEqual("plan v1\n", (self.fx.cwd / "notes.md").read_text(encoding="utf-8"))
        self.assertIn("secret-claude", (self.fx.claude / ".credentials.json").read_text(encoding="utf-8"))
        self.assertEqual("sk-ant-secret", json.loads((self.fx.home / ".claude.json").read_text(encoding="utf-8"))["primaryApiKey"])
        config = json.loads((self.fx.config / "config.json").read_text(encoding="utf-8"))
        self.assertEqual("zai-secret", config["providers"]["zai"]["api_key"])
        db = sqlite3.connect(self.fx.codex / "state_5.sqlite")
        self.assertEqual(2, db.execute("SELECT count(*) FROM threads").fetchone()[0])
        db.close()

    def test_restore_into_a_new_home_without_key_skips_secrets(self):
        manifest = self.create().manifest
        new = self.fx.base / "new"
        dest = destination_roots(manifest, {name: new / name for name in ("cwd", "claude", "home", "codex", "ciel", "ciel_ws")})
        result = restore_snapshot(self.target, manifest, dest, key=None)
        self.assertFalse(result["secrets_restored"])
        self.assertIn("claude/.credentials.json", result["secret_skipped"])
        self.assertTrue((new / "cwd" / "notes.md").is_file())
        public = json.loads((new / "ciel" / "config.json").read_text(encoding="utf-8"))
        self.assertNotIn("api_key", public["providers"]["zai"])

    def test_restore_to_another_folder_moves_what_follows_the_cwd(self):
        old_cwd = str(self.fx.cwd)
        (self.fx.codex / "config.toml").write_text(
            'model = "gpt-6.1-sol"\n[projects."' + old_cwd.replace("\\", "\\\\") + '"]\ntrust_level = "trusted"\n', encoding="utf-8")
        (self.fx.home / ".claude.json").write_text(json.dumps({"projects": {old_cwd.replace("\\", "/"): {"allowedTools": []}}}),
                                                   encoding="utf-8")
        manifest = self.create().manifest
        new = self.fx.base / "machine-b"
        overrides = {"cwd": new / "agent-work", "claude": new / "home" / ".claude", "home": new / "home",
                     "codex": new / "home" / ".codex", "ciel": new / "ciel"}
        overrides["ciel_ws"] = overrides["ciel"] / "workspaces" / "x"
        dest = destination_roots(manifest, overrides)
        result = restore_snapshot(self.target, manifest, dest, key=KEY)
        new_cwd = str(new / "agent-work")
        project = new / "home" / ".claude" / "projects" / claude_project_key(new_cwd)
        self.assertTrue((project / "s1.jsonl").is_file())
        self.assertTrue((project / "s1" / "subagents" / "agent-a.jsonl").is_file())
        db = sqlite3.connect(new / "home" / ".codex" / "state_5.sqlite")
        cwd, rollout = db.execute("SELECT cwd, rollout_path FROM threads WHERE id = 't1'").fetchone()
        other_cwd = db.execute("SELECT cwd FROM threads WHERE id = 't2'").fetchone()[0]
        db.close()
        self.assertEqual(new_cwd, cwd)
        self.assertTrue(Path(rollout).is_file(), rollout)
        self.assertEqual(str(self.fx.base / "elsewhere"), other_cwd)
        self.assertIn('[projects."' + new_cwd.replace("\\", "\\\\") + '"]',
                      (new / "home" / ".codex" / "config.toml").read_text(encoding="utf-8"))
        state = json.loads((new / "ciel" / "launch-state.json").read_text(encoding="utf-8"))
        self.assertIn(new_cwd, state["by_cwd"])
        claude_json = json.loads((new / "home" / ".claude.json").read_text(encoding="utf-8"))
        self.assertIn(new_cwd.replace("\\", "/"), claude_json["projects"])
        # t1 moved with the cwd; t2 (another folder) keeps its cwd but its rollout path follows CODEX_HOME.
        self.assertEqual(2, result["remapped"]["codex_threads"])

    def test_verify_reports_a_damaged_chunk(self):
        manifest = self.create().manifest
        self.assertEqual([], verify_snapshot(self.target, manifest))
        victim = next(path for path in (self.fx.backups / "blobs").rglob("*") if path.is_file())
        victim.write_bytes(b"garbage")
        self.assertTrue(verify_snapshot(self.target, manifest))

    def test_cli_create_list_restore(self):
        outputs = []
        context = self.fx.context(outputs, {"CIEL_RUNTIME_BACKUP_KEY": KEY.decode()})
        self.assertEqual(0, run_backup_command(["create", "--label", "first", "--json"], context))
        created = json.loads(outputs[-1])
        self.assertEqual("encrypted", created["secrets"])
        self.assertEqual(0, run_backup_command(["list", "--json"], context))
        rows = json.loads(outputs[-1])
        self.assertEqual([created["id"]], [row["id"] for row in rows])
        (self.fx.cwd / "notes.md").write_text("lost\n", encoding="utf-8")
        self.assertEqual(0, run_backup_command(["restore", created["id"][:10], "--json"], context))
        restored = json.loads(outputs[-1])
        self.assertTrue(restored["safety_snapshot"])
        self.assertEqual("plan v1\n", (self.fx.cwd / "notes.md").read_text(encoding="utf-8"))
        self.assertEqual(2, len(list_snapshots(LocalTarget(self.fx.config / "backups"))))
        self.assertEqual(0, run_backup_command(["verify", created["id"]], context))

    def test_restore_refuses_while_the_session_runs(self):
        import os

        outputs = []
        context = self.fx.context(outputs)
        run_backup_command(["create", "--json"], context)
        snapshot_id = json.loads(outputs[-1])["id"]
        state = {"by_cwd": {str(self.fx.cwd): {"pid": os.getppid()}}}
        (self.fx.config / "launch-state.json").write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(SystemExit):
            run_backup_command(["restore", snapshot_id], context)


class BackupPrimitiveTests(unittest.TestCase):
    def test_seal_round_trip_and_wrong_key(self):
        sealed = seal(b"payload", KEY)
        self.assertEqual(b"payload", open_sealed(sealed, KEY))
        with self.assertRaises(ValueError):
            open_sealed(sealed, b"other")

    def test_split_and_merge_secret_fields(self):
        value = {"a": 1, "api_key": "x", "providers": {"p": {"token": "t", "model": "m"}}, "list": [{"secret": "s"}]}
        public, hidden = split_secret_fields(value)
        self.assertEqual({"a": 1, "providers": {"p": {"model": "m"}}, "list": [{}]}, public)
        self.assertEqual(value, merge_secret_fields(public, hidden))

    def test_large_file_is_cut_into_chunks(self):
        with tempfile.TemporaryDirectory() as folder:
            target = LocalTarget(Path(folder))
            writer = ChunkWriter([target])
            digests = writer.write_stream([b"a" * (CHUNK_SIZE + 10)])
            self.assertEqual(2, len(digests))

    def test_s3_target_needs_env_references_for_keys(self):
        with self.assertRaises(ValueError):
            build_target("s", {"type": "s3", "endpoint": "http://x", "bucket": "b", "access_key": "AKIA", "secret_key": "plain"}, {})
        target = build_target("s", {"type": "s3", "endpoint": "http://x", "bucket": "b", "access_key": "${A}", "secret_key": "${S}"},
                              {"A": "ak", "S": "sk"})
        self.assertIsInstance(target, S3Target)

    def test_s3_requests_are_signed_and_listed(self):
        import datetime as dt

        seen = []

        class Response:
            def __init__(self, status, body):
                self.status, self._body = status, body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout):
            seen.append(request)
            if request.get_method() == "GET" and "list-type" in request.full_url:
                return Response(200, b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                                     b"<Key>p/snapshots/w/1.json</Key><IsTruncated>false</IsTruncated></ListBucketResult>")
            return Response(200, b"ok")

        target = S3Target("s", "http://127.0.0.1:9000", "bucket", prefix="p", access_key="ak", secret_key="sk",
                          opener=opener, clock=lambda: dt.datetime(2026, 10, 9, tzinfo=dt.timezone.utc))
        target.put("blobs/aa/aa1", b"data")
        self.assertEqual(["snapshots/w/1.json"], target.list("snapshots/"))
        self.assertIn("AWS4-HMAC-SHA256 Credential=ak/20261009/us-east-1/s3/aws4_request", seen[0].get_header("Authorization"))
        self.assertEqual("http://127.0.0.1:9000/bucket/p/blobs/aa/aa1", seen[0].full_url)

    def test_find_snapshot_by_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            target = LocalTarget(Path(folder))
            target.put("snapshots/w/20261009T000000Z-abc.json", json.dumps({"format": 1, "id": "20261009T000000Z-abc"}).encode())
            self.assertEqual("w", find_snapshot(target, "20261009T0000")[0])


class BackupTriggerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.config = self.base / "cfg"
        self.cwd = self.base / "work"
        self.cwd.mkdir(parents=True)
        self.watch = self.base / "watch"
        self.watch.mkdir()
        self.now = [1_000_000.0]
        self.launched = []

    def tearDown(self):
        self._tmp.cleanup()

    def scheduler(self):
        from ciel_runtime_support.session_backup_service import BackupScheduler, record_state

        def launch(config_dir, cwd, trigger):
            self.launched.append(trigger)
            record_state(config_dir, cwd, last_started=self.now[0])

        return BackupScheduler(self.config, self.cwd, lambda: [self.watch], clock=lambda: self.now[0], launch=launch)

    def set_schedule(self, **values):
        from ciel_runtime_support.session_backup_service import load_settings, save_settings

        settings = load_settings(self.config)
        settings["schedule"].update(values)
        save_settings(self.config, settings)

    def test_schedule_is_off_by_default(self):
        scheduler = self.scheduler()
        self.assertFalse(scheduler.tick())
        self.assertFalse(scheduler.on_turn_ended({}))
        self.assertEqual([], self.launched)

    def test_scheduled_backup_waits_for_the_interval_and_for_a_change(self):
        self.set_schedule(enabled=True, interval_minutes=60)
        scheduler = self.scheduler()
        (self.watch / "t.jsonl").write_text("1\n", encoding="utf-8")
        self.assertTrue(scheduler.tick())
        self.now[0] += 30 * 60
        self.assertFalse(scheduler.tick())
        self.now[0] += 31 * 60
        self.assertFalse(scheduler.tick(), "nothing changed")
        (self.watch / "t.jsonl").write_text("1\n2\n", encoding="utf-8")
        self.assertTrue(scheduler.tick())
        self.assertEqual(["scheduled", "scheduled"], self.launched)

    def test_turn_end_backup_respects_the_minimum_interval(self):
        self.set_schedule(on_turn_end=True, min_interval_minutes=10)
        scheduler = self.scheduler()
        self.assertTrue(scheduler.on_turn_ended({"turn_id": "a"}))
        self.now[0] += 5 * 60
        self.assertFalse(scheduler.on_turn_ended({"turn_id": "b"}))
        self.now[0] += 6 * 60
        self.assertTrue(scheduler.on_turn_ended({"turn_id": "c"}))

    def test_turn_ended_listener_runs_once_per_turn(self):
        from ciel_runtime_support import tui_observation

        seen = []
        tui_observation.TURN_ENDED_LISTENERS.append(seen.append)
        try:
            bus = tui_observation.TuiObservationBus(enabled=True)
            bus.publish_turn_ended({"turn_id": "t1"})
            bus.publish_turn_ended({"turn_id": "t1"})
        finally:
            tui_observation.TURN_ENDED_LISTENERS.remove(seen.append)
        self.assertEqual(1, len(seen))

    def test_run_backup_takes_one_lock_per_workspace(self):
        import subprocess as sp

        from ciel_runtime_support.session_backup_service import _acquire, run_backup, workspace_state

        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return sp.CompletedProcess(command, 0, json.dumps({"id": "snap-1", "targets": ["local"]}), "")

        self.assertEqual("snap-1", run_backup(self.config, self.cwd, "mcp", runner=runner)["id"])
        self.assertIn("--prune", calls[0])
        self.assertEqual("snap-1", workspace_state(self.config, self.cwd)["last_id"])
        self.assertTrue(_acquire(self.config, self.cwd))
        self.assertIn("skipped", run_backup(self.config, self.cwd, "mcp", runner=runner))
        self.assertEqual(1, len(calls))

    def test_after_cli_exit_only_when_enabled(self):
        from unittest import mock

        from ciel_runtime_support import session_backup_service

        with mock.patch.object(session_backup_service, "run_backup", return_value={"ok": True, "id": "x"}) as run:
            self.assertIsNone(session_backup_service.after_cli_exit(self.config, self.cwd, restarting=True))
            self.set_schedule(before_restart=True)
            self.assertEqual("x", session_backup_service.after_cli_exit(self.config, self.cwd, restarting=True)["id"])
            self.assertIsNone(session_backup_service.after_cli_exit(self.config, self.cwd, restarting=False))
        self.assertEqual("pre-restart", run.call_args.args[2])

    def test_mcp_tool_is_dispatched(self):
        from ciel_runtime_support.channel_mcp_tools import (
            ChannelMcpRuntimeServices,
            ChannelMcpToolServices,
            dispatch_channel_mcp_tool,
        )

        calls = []
        services = ChannelMcpToolServices(
            queue_compact=lambda *a: {}, append_message=lambda m: m, read_messages=lambda **k: [],
            store_file_path=lambda *a: {}, store_file_upload=lambda a: {}, file_message_text=lambda *a: "",
            handle_llm_options=lambda *a: ([], False),
            runtime=ChannelMcpRuntimeServices(session_backup=lambda args: calls.append(args) or {"ok": True, "id": "s"}),
        )
        response = dispatch_channel_mcp_tool(1, {"name": "session_backup", "arguments": {"action": "create"}}, services)
        self.assertFalse(response["result"]["isError"])
        self.assertEqual([{"action": "create"}], calls)
        bad = dispatch_channel_mcp_tool(2, {"name": "session_backup", "arguments": {"action": "restore"}}, services)
        self.assertTrue(bad["result"]["isError"])

    def test_menu_panel_toggles_and_cycles(self):
        from ciel_runtime_support import session_backup_menu
        from ciel_runtime_support.session_backup_service import load_settings

        rows, values = session_backup_menu.panel_rows(self.config, self.cwd)
        self.assertEqual("back", values[-1])
        self.assertIn("Scheduled backups  [off]", rows[1])
        session_backup_menu.apply(self.config, self.cwd, "toggle-enabled")
        session_backup_menu.apply(self.config, self.cwd, "interval")
        schedule = load_settings(self.config)["schedule"]
        self.assertTrue(schedule["enabled"])
        self.assertEqual(120, schedule["interval_minutes"])
        self.assertIn("every 120 min", session_backup_menu.summary(self.config, self.cwd))


class RemapPathFormTests(unittest.TestCase):
    def test_codex_extended_length_cwd_is_matched_and_kept_in_its_form(self):
        from ciel_runtime_support.session_backup_remap import remap_codex_threads

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            database = base / "state_5.sqlite"
            old_cwd, new_cwd = str(base / "old"), str(base / "new")
            db = sqlite3.connect(database)
            db.execute("CREATE TABLE threads (id TEXT, cwd TEXT, rollout_path TEXT)")
            db.execute("INSERT INTO threads VALUES ('t', ?, ?)", ("\\\\?\\" + old_cwd, str(base / "codex-a" / "r.jsonl")))
            db.commit()
            db.close()
            self.assertEqual(1, remap_codex_threads(database, old_cwd, new_cwd, str(base / "codex-a"), str(base / "codex-b")))
            db = sqlite3.connect(database)
            cwd, rollout = db.execute("SELECT cwd, rollout_path FROM threads").fetchone()
            db.close()
            self.assertEqual("\\\\?\\" + new_cwd, cwd)
            self.assertEqual(str(base / "codex-b" / "r.jsonl"), rollout)


class PruneTests(unittest.TestCase):
    def test_keeps_last_and_daily_and_deletes_only_unused_chunks(self):
        from ciel_runtime_support.session_backup_ops import prune_snapshots
        from ciel_runtime_support.session_backup_store import blob_key, encode_manifest, manifest_key

        with tempfile.TemporaryDirectory() as folder:
            target = LocalTarget(Path(folder))
            ids = ["20261001T010000Z-a", "20261001T020000Z-b", "20261002T010000Z-c", "20261003T010000Z-d", "20261003T020000Z-e"]
            for index, snapshot_id in enumerate(ids):
                chunks = ["shared", f"own{index}"]
                for digest in chunks:
                    target.put(blob_key(digest), b"x")
                target.put(manifest_key("w", snapshot_id),
                           encode_manifest({"format": 1, "id": snapshot_id, "entries": [{"chunks": chunks}]}))
            result = prune_snapshots(target, "w", keep_last=1, keep_daily=2)
            # keep_last=1 keeps e; keep_daily=2 keeps the newest of 10-02 (c) and 10-03 (e).
            self.assertEqual(["20261001T010000Z-a", "20261001T020000Z-b", "20261003T010000Z-d"], result["removed"])
            self.assertEqual(3, result["chunks_deleted"])
            self.assertTrue(target.has(blob_key("shared")))
            self.assertFalse(target.has(blob_key("own0")))
            self.assertTrue(target.has(blob_key("own2")))


if __name__ == "__main__":
    unittest.main()
