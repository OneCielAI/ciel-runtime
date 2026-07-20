import os
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

    def test_latest_reuses_cached_path_within_ttl(self):
        cached = Path("cached.jsonl")
        repository = self.repository(
            Path("unused"),
            cache={"checked_at": 299.0, "path": cached},
        )

        self.assertEqual(cached, repository.latest(ttl_seconds=2))

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
