import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ciel_runtime_support.context_compaction import (
    ContextCompactionFailed, build_llm_compacted_messages,
)
from ciel_runtime_support.context_compaction_progress import MapProgress
from ciel_runtime_support.upstream_error_policy import UpstreamFailure

import test_context_compaction as fixtures

SCOPE = {"runtime": "claude", "session_id": "session-1", "epoch": "epoch-1"}
COMPACT = "<command-name>/compact</command-name>"


class MapProgressReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def services(self, root=None):
        services = fixtures.ContextCompactionTests().real_services()
        services = replace(services, workflow=replace(
            services.workflow,
            split_messages=lambda messages, _target: [(index, [message]) for index, message in enumerate(messages)],
        ))
        return replace(services, progress_root=self.root if root is None else root)

    def history(self, count=6, marker="fact"):
        return [{"role": "assistant", "content": f"{marker} {index}"} for index in range(count)]

    def compact(self, services, config=None, history=None):
        return build_llm_compacted_messages(
            "provider", "model", {"_context_compact_scope": SCOPE, **(config or {})},
            [*(history or self.history()), {"role": "user", "content": COMPACT}],
            8192, services, wire="openai",
        )

    def cancel_after(self, services, completed):
        state = {"calls": 0}

        def summary(*_args, **_kwargs):
            state["calls"] += 1
            return f"summary {state['calls']}"

        services.workflow.request_summary.side_effect = summary
        return {"_context_compact_cancelled": lambda: state["calls"] >= completed}

    def test_cancelled_run_is_resumed_without_repaying_completed_chunks(self):
        first = self.services()
        with self.assertRaisesRegex(ContextCompactionFailed, "client_cancelled"):
            self.compact(first, self.cancel_after(first, 4))
        # The fourth answer arrived while the client was disconnecting; it is kept.
        self.assertEqual(4, first.workflow.request_summary.call_count)

        second = self.services()
        second.workflow.request_summary.side_effect = lambda *_args, **_kwargs: "fresh summary"
        result = self.compact(second)

        self.assertEqual(2, second.workflow.request_summary.call_count)
        final = result[-1]["content"]
        for index in range(1, 5):
            self.assertIn(f"summary {index}", final)
        self.assertEqual(final.index("summary 1") < final.index("summary 4"), True)
        activity = second.workflow.write_activity.call_args.kwargs
        self.assertEqual(4, activity["reused_chunks"])

    def test_journal_holds_digests_and_summaries_never_source_text(self):
        services = self.services()
        secret_history = self.history(marker="SOURCE_SECRET_TEXT")
        with self.assertRaises(ContextCompactionFailed):
            self.compact(services, self.cancel_after(services, 2), history=secret_history)
        stored = (self.root / "context-compact-progress.json").read_text(encoding="utf-8")
        self.assertNotIn("SOURCE_SECRET_TEXT", stored)
        self.assertIn("summary 1", stored)

    def test_every_identity_dimension_starts_a_fresh_run(self):
        variants = {
            "history": {"history": self.history(marker="changed")},
            "scope": {"config": {"_context_compact_scope": {**SCOPE, "epoch": "epoch-2"}}},
            "session": {"config": {"_context_compact_scope": {**SCOPE, "session_id": "session-2"}}},
            "summary_output": {"output": 333},
        }
        for name, change in variants.items():
            with self.subTest(name=name):
                self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
                first = self.services()
                with self.assertRaises(ContextCompactionFailed):
                    self.compact(first, self.cancel_after(first, 3))
                second = self.services()
                if "output" in change:
                    second = replace(second, transport=replace(
                        second.transport, summary_output_tokens=lambda *_args, value=change["output"]: value,
                    ))
                self.compact(second, change.get("config"), change.get("history"))
                self.assertEqual(6, second.workflow.request_summary.call_count)

    def test_other_model_and_wire_do_not_reuse(self):
        first = self.services()
        with self.assertRaises(ContextCompactionFailed):
            self.compact(first, self.cancel_after(first, 3))
        for model, wire in (("other-model", "openai"), ("model", "anthropic")):
            with self.subTest(model=model, wire=wire):
                second = self.services()
                build_llm_compacted_messages(
                    "provider", model, {"_context_compact_scope": SCOPE},
                    [*self.history(), {"role": "user", "content": COMPACT}],
                    8192, second, wire=wire,
                )
                self.assertEqual(6, second.workflow.request_summary.call_count)

    def test_unidentified_request_or_missing_root_never_persists(self):
        for root, config in ((self.root, {"_context_compact_scope": None}), (None, {})):
            with self.subTest(root=root):
                services = replace(self.services(), progress_root=root)
                build_llm_compacted_messages(
                    "provider", "model", config,
                    [*self.history(), {"role": "user", "content": COMPACT}],
                    8192, services, wire="openai",
                )
        self.assertFalse((self.root / "context-compact-progress.json").exists())

    def test_empty_truncated_and_failed_answers_are_never_recorded(self):
        services = self.services()
        services.workflow.request_summary.side_effect = [
            "summary 1",
            UpstreamFailure("provider", "model", status=500, error_type="api_error",
                            message="failed", source="http_status"),
        ]
        with self.assertRaises(UpstreamFailure):
            self.compact(services)
        steps = MapProgress(self.root, {}, mock.Mock()).steps
        self.assertEqual([], steps)  # a different identity sees nothing
        stored = json.loads((self.root / "context-compact-progress.json").read_text(encoding="utf-8"))
        recorded = [step for run in stored["runs"].values() for step in run["steps"]]
        self.assertEqual(["summary 1"], [step["text"] for step in recorded])

    def test_split_decision_is_replayed_with_full_ordered_coverage(self):
        rejected = UpstreamFailure("provider", "model", status=400, error_type="context_length_exceeded",
                                   message="too much context", source="http_status")
        history = [{"role": "assistant", "content": "left " * 80 + "right " * 80}]

        def split_services():
            services = self.services()
            services = replace(services, workflow=replace(
                services.workflow, split_messages=fixtures.ContextCompactionTests().real_services().workflow.split_messages,
            ))
            return services

        first = split_services()
        calls = {"n": 0}

        def first_summary(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise rejected
            return f"part {calls['n']}"

        first.workflow.request_summary.side_effect = first_summary
        with self.assertRaises(ContextCompactionFailed):
            self.compact(first, {"_context_compact_cancelled": lambda: calls["n"] >= 2}, history=history)
        stored = json.loads((self.root / "context-compact-progress.json").read_text(encoding="utf-8"))
        kinds = [step["kind"] for run in stored["runs"].values() for step in run["steps"]]
        self.assertEqual(["split", "summary"], kinds)

        second = split_services()
        second.workflow.request_summary.side_effect = lambda *_args, **_kwargs: "tail part"
        result = self.compact(second, history=history)
        prompts = [call.args[3] for call in second.workflow.request_summary.call_args_list]
        # Only the unsummarised right half and the reducer are generated again;
        # the rejected whole chunk is never replayed.
        self.assertFalse(any("left left" in prompt and "right right" in prompt for prompt in prompts))
        self.assertTrue(any("right right" in prompt for prompt in prompts))
        self.assertFalse(any("left left" in prompt for prompt in prompts[:-1]))
        self.assertIn("part 2", result[-1]["content"])
        self.assertEqual(1, second.workflow.write_activity.call_args.kwargs["reused_chunks"])

    def test_corrupt_journal_is_quarantined_and_compaction_still_completes(self):
        (self.root / "context-compact-progress.json").write_text("{not json", encoding="utf-8")
        services = self.services()
        self.compact(services)
        self.assertEqual(6, services.workflow.request_summary.call_count)
        self.assertTrue(list(self.root.glob("context-compact-progress.json.corrupt-*")))

    def test_stale_attempt_cannot_overwrite_a_newer_journal(self):
        log = mock.Mock()
        first = MapProgress(self.root, {"run": 1}, log)
        second = MapProgress(self.root, {"run": 1}, log)
        first.record("prompt one", "summary", "one")
        second.record("prompt one", "summary", "other")
        self.assertFalse(second.enabled)
        reader = MapProgress(self.root, {"run": 1}, log)
        self.assertEqual(["one"], [step["text"] for step in reader.steps])

    def test_prompt_mismatch_stops_reuse_and_writes(self):
        log = mock.Mock()
        writer = MapProgress(self.root, {"run": 2}, log)
        writer.record("prompt one", "summary", "one")
        reader = MapProgress(self.root, {"run": 2}, log)
        self.assertIsNone(reader.take("different prompt"))
        self.assertFalse(reader.enabled)
        reader.record("different prompt", "summary", "never")
        self.assertEqual(["one"], [step["text"] for step in MapProgress(self.root, {"run": 2}, log).steps])

    def test_run_count_and_step_summary_byte_caps(self):
        from ciel_runtime_support import context_compaction_progress as progress
        log = mock.Mock()
        for number in range(progress.MAX_RUNS + 2):
            progress.MapProgress(self.root, {"run": number}, log).record("prompt", "summary", "one")
        stored = json.loads((self.root / "context-compact-progress.json").read_text(encoding="utf-8"))
        self.assertEqual(progress.MAX_RUNS, len(stored["runs"]))
        writer = progress.MapProgress(self.root, {"run": "oversized"}, log)
        writer.record("prompt", "summary", "x" * (progress.MAX_SUMMARY_BYTES + 1))
        self.assertFalse(writer.enabled)
        with mock.patch.object(progress, "MAX_STEPS", 1):
            writer = progress.MapProgress(self.root, {"run": "steps"}, log)
            writer.record("one", "summary", "one")
            writer.record("two", "summary", "two")
            self.assertFalse(writer.enabled)
            self.assertEqual(1, writer.recorded)

    def test_total_byte_cap_disables_append_without_overwriting(self):
        from ciel_runtime_support import context_compaction_progress as progress
        writer = progress.MapProgress(self.root, {"run": "bytes"}, mock.Mock())
        writer.record("one", "summary", "one")
        original = writer.path.read_bytes()
        with mock.patch.object(progress, "MAX_BYTES", len(original) + 20):
            writer.record("two", "summary", "x" * 500)
        self.assertFalse(writer.enabled)
        self.assertEqual(original, writer.path.read_bytes())

    def test_expired_runs_are_ignored(self):
        log = mock.Mock()
        MapProgress(self.root, {"run": 3}, log, now=lambda: 1000.0).record("prompt", "summary", "old")
        later = MapProgress(self.root, {"run": 3}, log, now=lambda: 1000.0 + 7200)
        self.assertEqual([], later.steps)


if __name__ == "__main__":
    unittest.main()
