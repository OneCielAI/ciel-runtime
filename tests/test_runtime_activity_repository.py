import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.runtime_activity_repository import (
    RuntimeActivityClock,
    RuntimeActivityEffects,
    RuntimeActivityPaths,
    RuntimeActivityRepository,
)


class RuntimeActivityRepositoryTests(unittest.TestCase):
    def repository(self, root: Path, *, display=lambda: "2026-07-19T12:00:00"):
        publish = mock.Mock()
        log = mock.Mock()
        repository = RuntimeActivityRepository(
            RuntimeActivityPaths(root / "router.json", root / "compact.json", root / "usage.json"),
            RuntimeActivityClock(lambda: 123.5, display, lambda: "1.2"),
            RuntimeActivityEffects(publish, log),
        )
        return repository, publish, log

    def test_router_and_compact_snapshots_are_atomic_and_publish_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository, publish, _log = self.repository(Path(tmp))
            repository.router_activity("retry", "openai", "gpt", attempt=2)
            repository.context_compact("openai", "gpt", reason="limit")
            router = json.loads(repository.paths.router.read_text(encoding="utf-8"))
            compact = json.loads(repository.paths.context_compact.read_text(encoding="utf-8"))

        self.assertEqual("retry", router["event"])
        self.assertEqual(2, router["attempt"])
        self.assertEqual("compact", compact["event"])
        self.assertEqual(2, publish.call_count)
        self.assertEqual("warn", publish.call_args_list[0].kwargs["level"])

    def test_context_usage_projects_counts_and_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository, publish, _log = self.repository(Path(tmp))
            repository.context_usage(
                "openai",
                {"current_model": "gpt"},
                {"messages": [{"role": "user"}], "tools": [{"name": "x"}]},
                "messages",
                estimate_tokens=lambda _body: 250,
                context_limit=lambda _provider, _config: 1000,
            )
            usage = json.loads(repository.paths.context_usage.read_text(encoding="utf-8"))

        self.assertEqual(25.0, usage["percent"])
        self.assertEqual(1, usage["messages"])
        self.assertEqual("context.usage", publish.call_args.kwargs["category"])

    def test_snapshot_failure_is_observable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository, _publish, log = self.repository(
                Path(tmp),
                display=mock.Mock(side_effect=OSError("clock failed")),
            )
            repository.context_compact("openai")

        log.assert_called_once()
        self.assertIn("runtime_activity_context_compact_failed", log.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
