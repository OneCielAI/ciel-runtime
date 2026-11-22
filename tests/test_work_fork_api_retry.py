"""Fork transient API error detection and work_parallel_status projection."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support.fork_turn_watch import EMPTY_STREAM_ERROR, subagents_dir
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_parallel_mcp import project as project_parallel

IDENTITY = "claude:fork-retry-sess"
SESSION_ID = "fork-retry-sess"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


class WorkForkApiRetryTests(unittest.TestCase):
    def test_classify_transient_api_errors_compact_and_empty_stream(self):
        from ciel_runtime_support.work_fork_api_retry import classify_fork_transcript

        cases = [
            ("503", {"type": "assistant", "uuid": "u-503", "isApiErrorMessage": True,
                     "error": "server_error", "apiErrorStatus": 503,
                     "timestamp": "2026-11-22T07:00:00Z"}, False, "server_error 503"),
            ("overloaded", {"type": "assistant", "uuid": "u-529", "isApiErrorMessage": True,
                            "error": "overloaded_error", "apiErrorStatus": 529,
                            "timestamp": "2026-11-22T07:01:00Z"}, False, "overloaded_error 529"),
            ("terminated", {"type": "assistant", "uuid": "u-term", "isApiErrorMessage": True,
                            "error": "unknown",
                            "message": {"content": [{"type": "text", "text": "API Error: terminated"}]},
                            "timestamp": "2026-11-22T07:02:00Z"}, False, "transport_interrupted"),
            ("empty_stream", {"type": "assistant", "uuid": "u-empty", "isApiErrorMessage": True,
                              "error": "unknown",
                              "message": {"content": [{"type": "text", "text": EMPTY_STREAM_ERROR}]},
                              "timestamp": "2026-11-22T07:03:00Z"}, False, "empty_stream"),
            ("mid_cut", {"type": "assistant", "uuid": "u-cut", "isApiErrorMessage": True,
                         "error": "api_error", "truncatedAfterOutput": True,
                         "timestamp": "2026-11-22T07:04:00Z"}, False, "stream_cut_after_output"),
            ("compact_failed", {"type": "assistant", "uuid": "u-cmp", "isApiErrorMessage": True,
                                "error": "invalid_request_error",
                                "message": {"content": [{"type": "text",
                                                         "text": "Prompt is too long · automatic compaction failed: 503"}]},
                                "timestamp": "2026-11-22T07:05:00Z"}, True, "auto_compact_failed"),
        ]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, err_row, expect_compact, expect_kind in cases:
                with self.subTest(name=name):
                    path = root / f"agent-{name}.jsonl"
                    _write_jsonl(path, [
                        {"type": "user", "uuid": "u-0", "message": {"content": "start"}},
                        {"type": "assistant", "uuid": "u-prog", "message": {"content": [{"type": "text", "text": "step 1"}]}},
                        err_row,
                    ])
                    info = classify_fork_transcript(path)
                    self.assertIsNotNone(info)
                    self.assertTrue(info["retryable"], info)
                    self.assertEqual(err_row["uuid"], info["error_uuid"])
                    self.assertEqual("u-prog", info["last_progress_uuid"])
                    self.assertEqual(expect_compact, info["compaction"])
                    self.assertEqual(expect_kind, info["kind"])

    def test_non_transient_recovered_and_normal_turns_not_retryable(self):
        from ciel_runtime_support.work_fork_api_retry import classify_fork_transcript

        cases = [
            ("bad_request_400", [{"type": "assistant", "uuid": "e1", "isApiErrorMessage": True,
                                  "error": "invalid_request_error", "apiErrorStatus": 400}]),
            ("usage_limit_429", [{"type": "assistant", "uuid": "e2", "isApiErrorMessage": True,
                                  "error": "rate_limit", "apiErrorStatus": 429, "apiErrorIsTransient": False}]),
            ("cannot_cure", [{"type": "assistant", "uuid": "e3", "isApiErrorMessage": True,
                              "error": "server_error", "apiErrorStatus": 500, "resendCannotCure": True}]),
            ("normal_reply", [{"type": "assistant", "uuid": "ok1", "message": {"content": [{"type": "text", "text": "done"}]}}]),
            ("recovered_after_503", [
                {"type": "assistant", "uuid": "e4", "isApiErrorMessage": True, "error": "server_error", "apiErrorStatus": 503},
                {"type": "user", "uuid": "u2", "message": {"content": "retry"}},
                {"type": "assistant", "uuid": "ok2", "message": {"content": [{"type": "text", "text": "continued"}]}},
            ]),
        ]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, rows in cases:
                with self.subTest(name=name):
                    path = root / f"agent-{name}.jsonl"
                    _write_jsonl(path, rows)
                    self.assertIsNone(classify_fork_transcript(path))

    def test_project_parallel_includes_fork_api_errors_only_in_host_view(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            claude_cfg = root / "claude-cfg"
            workspace = root / "repo"
            workspace.mkdir()
            pstore = WorkParallelStore(root / "work-parallel.json")
            pstore.configure({"enabled": True}, capability_ready=True, expected_version=pstore.read()["version"])
            cand = {
                "item_id": "n10", "priority": "P1", "created_at": 100.0, "approved": True,
                "focused": False, "dependencies_done": True, "plan_version": 1,
                "usage": 0, "role": "worker", "mode": "fork",
            }
            _, lease_id = pstore.reserve([cand], scope=IDENTITY, expected_version=pstore.read()["version"])
            pstore.bind(lease_id, "agent-err-1", expected_version=pstore.read()["version"])
            sub = subagents_dir(claude_cfg, workspace, SESSION_ID)
            _write_jsonl(sub / "agent-agent-err-1.jsonl", [
                {"type": "assistant", "uuid": "prog-1", "message": {"content": [{"type": "text", "text": "SECRET_BODY"}]}},
                {"type": "assistant", "uuid": "err-503", "isApiErrorMessage": True,
                 "error": "server_error", "apiErrorStatus": 503, "timestamp": "2026-11-22T07:10:00Z"},
            ])
            fake_scope = {"runtime": "claude", "session_id": SESSION_ID,
                          "cwd": str(workspace), "claude_config_dir": str(claude_cfg)}
            with patch("ciel_runtime_support.work_fork_api_retry._active_claude_scope", return_value=fake_scope):
                host_view = project_parallel(pstore, IDENTITY, host_view=True)
                model_view = project_parallel(pstore, IDENTITY, host_view=False)
            self.assertIn(lease_id, host_view.get("fork_api_errors") or {})
            row = host_view["fork_api_errors"][lease_id]
            self.assertEqual("agent-err-1", row["agent_id"])
            self.assertEqual("err-503", row["error_uuid"])
            self.assertEqual("prog-1", row["last_progress_uuid"])
            self.assertEqual("server_error 503", row["kind"])
            self.assertNotIn("SECRET_BODY", json.dumps(host_view))
            self.assertNotIn("fork_api_errors", model_view)


if __name__ == "__main__":
    unittest.main()
