import os
import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.channel_transcript_repository import (
    ChannelTranscriptRepository,
)


class ChannelTranscriptRepositoryTests(unittest.TestCase):
    def repository(self, home, cache=None, scope=None, now=300.0):
        return ChannelTranscriptRepository(
            home=home,
            cache=cache if cache is not None else {},
            scope=scope if scope is not None else {},
            now=lambda: now,
        )

    def test_runtime_scope_selects_codex_root_and_resets_cache(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            codex_home = home / "custom-codex"
            cache = {"checked_at": 10.0, "path": Path("old")}
            scope = {}
            repository = self.repository(home, cache, scope)

            repository.set_scope(
                "CODEX",
                started_at=200,
                codex_home=codex_home,
            )

            self.assertEqual(
                ((codex_home / "sessions", "**/*.jsonl"),),
                repository.roots(),
            )
            self.assertEqual(
                {"checked_at": 0.0, "path": None},
                cache,
            )

    def test_latest_ignores_transcripts_older_than_scope(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            sessions = home / ".codex" / "sessions" / "2026"
            sessions.mkdir(parents=True)
            stale = sessions / "stale.jsonl"
            current = sessions / "current.jsonl"
            stale.write_text("stale", encoding="utf-8")
            current.write_text("current", encoding="utf-8")
            os.utime(stale, (100, 100))
            os.utime(current, (201, 201))
            repository = self.repository(
                home,
                scope={"runtime": "codex", "started_at": 200},
            )

            self.assertEqual(current, repository.latest(ttl_seconds=0))

    def test_latest_uses_codex_session_start_and_cwd_not_recent_write_time(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            sessions = home / ".codex" / "sessions" / "2026"
            sessions.mkdir(parents=True)
            unrelated = sessions / "unrelated.jsonl"
            current = sessions / "current.jsonl"
            unrelated.write_text(json.dumps({
                "type": "session_meta",
                "payload": {
                    "timestamp": "1970-01-01T00:01:40Z",
                    "cwd": "/repo/other",
                },
            }) + "\n", encoding="utf-8")
            current.write_text(json.dumps({
                "type": "session_meta",
                "payload": {
                    "timestamp": "1970-01-01T00:03:21Z",
                    "cwd": "/repo/target",
                },
            }) + "\n", encoding="utf-8")
            os.utime(unrelated, (250, 250))
            os.utime(current, (201, 201))
            repository = self.repository(
                home,
                scope={
                    "runtime": "codex",
                    "started_at": 200,
                    "cwd": Path("/repo/target"),
                },
            )

            self.assertEqual(current, repository.latest(ttl_seconds=0))

    def test_latest_binds_resumed_codex_session_despite_old_session_start(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            sessions = home / ".codex" / "sessions" / "2026"
            sessions.mkdir(parents=True)
            resumed = sessions / "resumed.jsonl"
            competitor = sessions / "competitor.jsonl"
            for path, session_id, timestamp in (
                (resumed, "resumed-session", "1970-01-01T00:01:40Z"),
                (competitor, "other-session", "1970-01-01T00:03:21Z"),
            ):
                path.write_text(
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "session_id": session_id,
                                "timestamp": timestamp,
                                "cwd": "/repo/target",
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            os.utime(resumed, (150, 150))
            os.utime(competitor, (260, 260))
            repository = self.repository(home, scope={})
            repository.set_scope(
                "codex",
                started_at=200,
                cwd=Path("/repo/target"),
                session_id="resumed-session",
            )

            self.assertEqual(resumed, repository.latest(ttl_seconds=0))

    def test_latest_reuses_cached_path_within_ttl(self):
        cached = Path("cached.jsonl")
        repository = self.repository(
            Path("unused"),
            cache={"checked_at": 299.0, "path": cached},
        )

        self.assertEqual(cached, repository.latest(ttl_seconds=2))

    def test_claude_scope_ignores_newer_transcript_from_other_workspace(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            projects = home / ".claude" / "projects"
            target_dir = projects / "C--repo-target"
            other_dir = projects / "C--repo-other"
            target_dir.mkdir(parents=True)
            other_dir.mkdir(parents=True)
            target = target_dir / "supervisor.jsonl"
            unrelated = other_dir / "unrelated.jsonl"
            target.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "cwd": r"C:\repo\target",
                        "sessionId": "supervisor",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            unrelated.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "cwd": r"C:\repo\other",
                        "sessionId": "unrelated",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            os.utime(target, (201, 201))
            os.utime(unrelated, (250, 250))
            repository = self.repository(home, scope={})
            repository.set_scope(
                "claude", started_at=200, cwd=Path(r"C:\repo\target")
            )

            self.assertEqual(target, repository.latest(ttl_seconds=0))

    def test_claude_scope_binds_first_valid_supervisor_transcript(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            project_dir = home / ".claude" / "projects" / "C--repo-target"
            project_dir.mkdir(parents=True)
            supervisor = project_dir / "supervisor.jsonl"
            competitor = project_dir / "competitor.jsonl"
            record = {"type": "user", "cwd": r"C:\repo\target"}
            supervisor.write_text(json.dumps(record) + "\n", encoding="utf-8")
            os.utime(supervisor, (201, 201))
            repository = self.repository(home, scope={})
            repository.set_scope(
                "claude", started_at=200, cwd=Path(r"C:\repo\target")
            )
            self.assertEqual(supervisor, repository.latest(ttl_seconds=0))

            competitor.write_text(json.dumps(record) + "\n", encoding="utf-8")
            os.utime(competitor, (250, 250))
            self.assertEqual(supervisor, repository.latest(ttl_seconds=0))

    def test_claude_explicit_session_id_wins_within_same_workspace(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            project_dir = home / ".claude" / "projects" / "C--repo-target"
            project_dir.mkdir(parents=True)
            supervisor = project_dir / "supervisor.jsonl"
            competitor = project_dir / "competitor.jsonl"
            for path, session_id in (
                (supervisor, "supervisor-session"),
                (competitor, "other-session"),
            ):
                path.write_text(
                    json.dumps(
                        {
                            "type": "user",
                            "cwd": r"C:\repo\target",
                            "sessionId": session_id,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            os.utime(supervisor, (201, 201))
            os.utime(competitor, (250, 250))
            repository = self.repository(home, scope={})
            repository.set_scope(
                "claude",
                started_at=200,
                cwd=Path(r"C:\repo\target"),
                session_id="supervisor-session",
            )

            self.assertEqual(supervisor, repository.latest(ttl_seconds=0))

    def test_claude_identity_accumulates_session_then_cwd_across_records(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            project_dir = home / ".claude" / "projects" / "C--repo-target"
            project_dir.mkdir(parents=True)
            supervisor = project_dir / "supervisor.jsonl"
            supervisor.write_text(
                "\n".join(
                    (
                        json.dumps(
                            {
                                "type": "permission-mode",
                                "sessionId": "supervisor-session",
                                "timestamp": "2026-08-18T04:58:00Z",
                            }
                        ),
                        json.dumps(
                            {
                                "type": "user",
                                "cwd": r"C:\repo\target",
                                "sessionId": "supervisor-session",
                                "timestamp": "2026-08-18T04:58:35Z",
                            }
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            repository = self.repository(home, scope={})
            repository.set_scope(
                "claude",
                started_at=200,
                cwd=Path(r"C:\repo\target"),
                session_id="supervisor-session",
            )

            _started_at, cwd, session_id = repository._claude_session_identity(
                supervisor
            )
            self.assertEqual(r"C:\repo\target", cwd)
            self.assertEqual("supervisor-session", session_id)
            self.assertEqual(supervisor, repository.latest(ttl_seconds=0))

    def test_read_tail_text_bounds_bytes(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "transcript.jsonl"
            path.write_text("0123456789", encoding="utf-8")

            self.assertEqual(
                "6789",
                ChannelTranscriptRepository.read_tail_text(
                    path,
                    max_bytes=4,
                ),
            )

    def test_turn_updates_track_lifecycle_beyond_bounded_tail(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            sessions = home / ".codex" / "sessions" / "2026"
            sessions.mkdir(parents=True)
            transcript = sessions / "resumed.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "session_id": "resumed-session",
                            "timestamp": "1970-01-01T00:01:40Z",
                            "cwd": "/repo/target",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            scope = {}
            repository = self.repository(home, scope=scope, now=200)
            repository.set_scope(
                "codex",
                started_at=200,
                cwd=Path("/repo/target"),
                session_id="resumed-session",
            )

            with transcript.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "timestamp": "1970-01-01T00:03:21Z",
                            "type": "event_msg",
                            "payload": {"type": "task_started"},
                        }
                    )
                    + "\n"
                )
                stream.write(
                    json.dumps(
                        {
                            "timestamp": "1970-01-01T00:03:22Z",
                            "type": "response_item",
                            "payload": {"type": "reasoning", "text": "x" * (600 * 1024)},
                        }
                    )
                    + "\n"
                )

            updates = repository.read_turn_updates(transcript)
            self.assertIn('"task_started"', updates)
            self.assertGreater(len(updates.encode("utf-8")), 512 * 1024)
            self.assertEqual("", repository.read_turn_updates(transcript))

            with transcript.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "timestamp": "1970-01-01T00:03:23Z",
                            "type": "event_msg",
                            "payload": {"type": "task_complete"},
                        }
                    )
                    + "\n"
                )
            self.assertIn('"task_complete"', repository.read_turn_updates(transcript))

    def test_turn_updates_process_large_backlog_in_bounded_batches(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "backlog.jsonl"
            records = [
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_started"},
                    }
                )
            ]
            records.extend(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "reasoning", "text": "x" * 900},
                    }
                )
                for _ in range(40)
            )
            records.append(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_complete"},
                    }
                )
            )
            path.write_text("\n".join(records) + "\n", encoding="utf-8")
            scope = {
                "turn_scan_path": path,
                "turn_scan_offset": 0,
                "turn_active": False,
            }
            repository = self.repository(Path(raw_dir), scope=scope)

            batches = []
            while int(scope.get("turn_scan_offset") or 0) < path.stat().st_size:
                batch = repository.read_turn_updates(path, max_bytes=4096)
                self.assertTrue(batch)
                self.assertLessEqual(len(batch.encode("utf-8")), 4096)
                batches.append(batch)

            self.assertGreater(len(batches), 1)
            self.assertIn('"task_started"', batches[0])
            self.assertIn('"task_complete"', batches[-1])

    def test_turn_updates_skip_one_oversized_record_and_reach_lifecycle(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "oversized.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "reasoning", "text": "x" * 12000},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_complete"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            scope = {
                "turn_scan_path": path,
                "turn_scan_offset": 0,
                "turn_active": True,
            }
            logs = []
            repository = ChannelTranscriptRepository(
                home=Path(raw_dir),
                cache={},
                scope=scope,
                now=lambda: 300.0,
            )

            updates = ""
            while int(scope.get("turn_scan_offset") or 0) < path.stat().st_size:
                updates = repository.read_turn_updates(
                    path,
                    max_bytes=4096,
                    log=lambda level, message: logs.append((level, message)),
                )
                if updates:
                    break

            self.assertIn('"task_complete"', updates)
            self.assertEqual(path.stat().st_size, scope["turn_scan_offset"])
            self.assertFalse(scope["turn_scan_skipping_record"])
            self.assertTrue(
                any("channel_turn_record_exceeds_memory_limit" in message for _, message in logs)
            )

    def test_turn_updates_continue_discarding_oversized_record_after_append(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "growing-oversized.jsonl"
            path.write_bytes(b"x" * 5000)
            scope = {
                "turn_scan_path": path,
                "turn_scan_offset": 0,
                "turn_active": True,
            }
            repository = self.repository(Path(raw_dir), scope=scope)

            self.assertEqual("", repository.read_turn_updates(path, max_bytes=4096))
            self.assertEqual(4096, scope["turn_scan_offset"])
            self.assertTrue(scope["turn_scan_skipping_record"])

            with path.open("ab") as stream:
                stream.write(b"y" * 5000 + b"\n")
                stream.write(
                    (
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "task_complete"},
                            }
                        )
                        + "\n"
                    ).encode("utf-8")
                )

            updates = ""
            while int(scope.get("turn_scan_offset") or 0) < path.stat().st_size:
                updates = repository.read_turn_updates(path, max_bytes=4096)
                if updates:
                    break
            self.assertIn('"task_complete"', updates)
            self.assertEqual(path.stat().st_size, scope["turn_scan_offset"])
            self.assertFalse(scope["turn_scan_skipping_record"])


if __name__ == "__main__":
    unittest.main()
