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


if __name__ == "__main__":
    unittest.main()
