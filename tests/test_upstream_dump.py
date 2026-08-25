import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.upstream_dump import (
    DUMP_ENV_VAR,
    dump_upstream_request,
    upstream_dump_dir,
)


class UpstreamDumpTests(unittest.TestCase):
    def test_disabled_without_environment_variable(self):
        logs = []
        written = dump_upstream_request(
            "https://upstream.example/v1/responses",
            b"{}",
            lambda level, message: logs.append((level, message)),
            env=lambda _name: None,
        )

        self.assertIsNone(written)
        self.assertEqual([], logs)

    def test_blank_environment_variable_stays_disabled(self):
        self.assertIsNone(upstream_dump_dir(env=lambda _name: "   "))

    def test_writes_exact_bytes_and_meta(self):
        payload = '{"input":[{"type":"reasoning","id":"한글"}]}'.encode("utf-8")
        logs = []
        with tempfile.TemporaryDirectory() as target:
            written = dump_upstream_request(
                "https://chatgpt.com/backend-api/codex/responses",
                payload,
                lambda level, message: logs.append((level, message)),
                env={DUMP_ENV_VAR: target}.get,
            )

            self.assertIsNotNone(written)
            self.assertEqual(payload, written.read_bytes())
            meta_path = Path(str(written).replace("-body.json", "-meta.json"))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "https://chatgpt.com/backend-api/codex/responses", meta["url"]
            )
            self.assertEqual(len(payload), meta["body_bytes"])
        self.assertEqual(1, len(logs))
        self.assertEqual("INFO", logs[0][0])

    def test_header_metadata_preserves_contract_and_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as target:
            written = dump_upstream_request(
                "https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages",
                b"{}",
                lambda _level, _message: None,
                env={DUMP_ENV_VAR: target}.get,
                headers={
                    "anthropic-version": "2023-06-01",
                    "user-agent": "ZCode/0.16.3",
                    "authorization": "Bearer private-value",
                    "x-zai-captcha-param": "private-captcha-value",
                },
            )

            meta_path = Path(str(written).replace("-body.json", "-meta.json"))
            headers = json.loads(meta_path.read_text(encoding="utf-8"))["headers"]
            self.assertEqual("2023-06-01", headers["anthropic-version"])
            self.assertEqual("ZCode/0.16.3", headers["user-agent"])
            self.assertEqual("<redacted len=20>", headers["authorization"])
            self.assertEqual("<redacted len=21>", headers["x-zai-captcha-param"])

    def test_capture_failure_only_logs(self):
        logs = []
        with tempfile.TemporaryDirectory() as target:
            blocker = Path(target) / "occupied"
            blocker.write_text("file, not a directory", encoding="utf-8")
            written = dump_upstream_request(
                "https://upstream.example/v1/responses",
                b"{}",
                lambda level, message: logs.append((level, message)),
                env={DUMP_ENV_VAR: str(blocker)}.get,
            )

        self.assertIsNone(written)
        self.assertEqual(1, len(logs))
        self.assertEqual("WARN", logs[0][0])
        self.assertIn("upstream_dump_failed", logs[0][1])


if __name__ == "__main__":
    unittest.main()
