import unittest

from ciel_runtime_support.channel_probe_report import (
    ChannelProbeReportServices,
    channel_probe_report_lines,
)


class ChannelProbeReportTests(unittest.TestCase):
    def test_projects_buckets_diagnostics_and_hints(self):
        result = {
            "probed_at": 10,
            "servers": [
                {"name": "native", "bucket": "capable", "transport": "stdio", "source_path": "<built-in>"},
                {"name": "bad", "bucket": "non_capable", "reason": "no_tools"},
                {
                    "name": "slow",
                    "bucket": "inconclusive",
                    "transport": "stdio",
                    "reason": "timeout_initialize",
                    "elapsed_ms": 5000,
                    "stderr_preview": "starting",
                },
                {"name": "dead", "bucket": "inconclusive", "reason": "exited_without_response", "exit_code": 1},
                {"name": "off", "bucket": "skipped", "reason": "disabled"},
            ],
        }
        lines = channel_probe_report_lines(
            result,
            5.0,
            ChannelProbeReportServices(
                bucket=lambda record: record["bucket"],
                format_timestamp=lambda value: "timestamp",
            ),
        )
        text = "\n".join(lines)

        self.assertIn("probed at timestamp, timeout 5.0s", text)
        self.assertIn("native (stdio) built-in", text)
        self.assertIn("slow (stdio) reason=timeout_initialize elapsed=5000ms", text)
        self.assertIn("stderr: starting", text)
        self.assertIn("inconclusive timeout", text)
        self.assertIn("child died before responding", text)
        self.assertIn("skipped     : 1", text)


if __name__ == "__main__":
    unittest.main()
