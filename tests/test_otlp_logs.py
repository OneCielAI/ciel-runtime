import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.otlp_logs import (
    OtlpLogsHttpController,
    OtlpLogsHttpPorts,
    TelemetryLogRecord,
    TelemetryLogRepository,
    TelemetryLogTokenRepository,
    extract_otlp_log_records,
)


def attr(key, value_key, value):
    return {"key": key, "value": {value_key: value}}


def export_request(*records, resource_attributes=None):
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": resource_attributes or []},
                "scopeLogs": [{"scope": {"name": "test"}, "logRecords": list(records)}],
            }
        ]
    }


class Handler:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.status = None
        self.response_headers = []
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name.lower(), value))

    def end_headers(self):
        return None


class TelemetryLogTokenTests(unittest.TestCase):
    def test_generated_token_is_stable_and_authorizes_bearer_or_dedicated_header(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = TelemetryLogTokenRepository(Path(directory) / "ingest-token", {})
            token = repository.ensure()

            self.assertEqual(token, repository.ensure())
            self.assertTrue(repository.authenticate(Handler({"authorization": f"Bearer {token}"})))
            self.assertTrue(repository.authenticate(Handler({"x-ciel-telemetry-token": token})))
            self.assertFalse(repository.authenticate(Handler({"authorization": "Bearer wrong"})))


class OtlpLogProjectionTests(unittest.TestCase):
    def test_standard_file_name_original_record_and_ciel_policy_are_projected(self):
        payload = export_request(
            {
                "body": {"stringValue": "normalized"},
                "attributes": [
                    attr("log.file.name", "stringValue", "application.log"),
                    attr("log.record.original", "stringValue", "raw source line"),
                    attr("ciel.log.roll.max_bytes", "intValue", "131072"),
                    attr("ciel.log.retention.max_segments", "intValue", "3"),
                    attr("ciel.log.retention.ttl_seconds", "intValue", "3600"),
                ],
            }
        )

        records, rejected, errors = extract_otlp_log_records(payload)

        self.assertEqual(0, rejected)
        self.assertEqual([], errors)
        self.assertEqual("application.log", records[0].logical_file)
        self.assertEqual("raw source line", records[0].text)
        self.assertEqual(
            {"segment_max_bytes": 131072, "max_segments": 3, "ttl_seconds": 3600},
            records[0].policy,
        )

    def test_resource_file_name_is_fallback_and_invalid_record_is_partial(self):
        payload = export_request(
            {"body": {"stringValue": "one"}},
            "invalid",
            resource_attributes=[attr("log.file.name", "stringValue", "resource.log")],
        )

        records, rejected, errors = extract_otlp_log_records(payload)

        self.assertEqual("resource.log", records[0].logical_file)
        self.assertEqual(1, rejected)
        self.assertIn("logRecords item is not an object", errors)


class TelemetryLogRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = [1000.0]
        self.repository = TelemetryLogRepository(
            Path(self.temp.name),
            now=lambda: self.clock[0],
            default_segment_max_bytes=65536,
            default_max_segments=3,
            default_ttl_seconds=60,
        )

    def test_same_logical_file_appends_and_returns_exact_byte_and_line_cursors(self):
        first_range = self.repository.append(
            [TelemetryLogRecord("server.log", "first", {})]
        )[0]
        ranges = self.repository.append(
            [TelemetryLogRecord("server.log", "second\ncontinued", {})]
        )

        self.assertEqual(1, len(ranges))
        cursor = ranges[0]
        self.assertEqual(first_range.segment, cursor.segment)
        self.assertEqual((2, 3), (cursor.line_start, cursor.line_end))
        self.assertEqual(first_range.offset_end, cursor.offset_start)
        by_offset = self.repository.read(
            "server.log",
            segment=first_range.segment,
            offset=first_range.offset_start,
            max_bytes=cursor.offset_end - first_range.offset_start,
        )
        by_line = self.repository.read(
            "server.log",
            segment=cursor.segment,
            line_start=2,
            line_end=3,
        )
        self.assertEqual("first\nsecond\ncontinued\n", by_offset["content"])
        self.assertEqual("second\ncontinued\n", by_line["content"])
        self.assertTrue(by_offset["eof"])

    def test_size_roll_and_max_segments_are_enforced_per_file(self):
        policy = {"segment_max_bytes": 65536, "max_segments": 2, "ttl_seconds": 0}
        for marker in ("a", "b", "c"):
            self.repository.append(
                [TelemetryLogRecord("large.log", marker * 40000, policy)]
            )

        item = self.repository.list_files()[0]
        self.assertEqual(2, len(item["segments"]))
        self.assertEqual([2, 3], [segment["id"] for segment in item["segments"]])
        self.assertEqual(2, item["total_records"])

    def test_ttl_cleanup_removes_expired_segments(self):
        self.repository.append([TelemetryLogRecord("ttl.log", "old", {})])
        self.clock[0] = 1061.0

        result = self.repository.cleanup()

        self.assertEqual(1, result["removed_segments"])
        self.assertEqual([], self.repository.list_files()[0]["segments"])

    def test_manual_roll_configure_and_delete(self):
        configured = self.repository.configure(
            "manual.log",
            {"segment_max_bytes": 131072, "max_segments": 4, "ttl_seconds": 0},
        )
        rolled = self.repository.roll("manual.log")
        deleted = self.repository.delete("manual.log", rolled["segment"])

        self.assertEqual(4, configured["policy"]["max_segments"])
        self.assertEqual(1, deleted["removed_segments"])


class OtlpLogsHttpControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.responses = []
        self.notices = []
        self.logs = []
        self.controller = OtlpLogsHttpController(
            OtlpLogsHttpPorts(
                repository=TelemetryLogRepository(Path(self.temp.name)),
                submit_notice=lambda ranges, count: self.notices.append((ranges, count)) or {"id": 1},
                write_json=lambda _handler, payload, status=200: self.responses.append((status, payload)),
                log=lambda level, message: self.logs.append((level, message)),
                authenticate=lambda handler: handler.headers.get("authorization") == "Bearer test-token",
            )
        )

    def test_gzip_json_export_stores_logs_and_emits_cursor_only_notice(self):
        raw = json.dumps(
            export_request(
                {
                    "body": {"stringValue": "hello"},
                    "attributes": [attr("log.file.name", "stringValue", "agent.log")],
                }
            )
        ).encode()

        handled = self.controller.post(
            Handler({"content-encoding": "gzip", "authorization": "Bearer test-token"}),
            "/v1/logs",
            gzip.compress(raw),
            "application/json",
        )

        self.assertTrue(handled)
        self.assertEqual((200, {}), self.responses[0])
        self.assertEqual(1, self.notices[0][1])
        notice_range = self.notices[0][0][0]
        self.assertEqual("agent.log", notice_range["file"])
        self.assertNotIn("hello", json.dumps(self.notices[0]))

    def test_partial_success_uses_otlp_json_field_names(self):
        raw = json.dumps(export_request({"body": {"stringValue": "ok"}}, 7)).encode()

        self.controller.post(Handler({"authorization": "Bearer test-token"}), "/v1/logs", raw, "application/json")

        status, payload = self.responses[0]
        self.assertEqual(200, status)
        self.assertEqual("1", payload["partialSuccess"]["rejectedLogRecords"])

    def test_binary_protobuf_is_rejected_with_configuration_guidance(self):
        self.controller.post(Handler({"authorization": "Bearer test-token"}), "/v1/logs", b"\x00", "application/x-protobuf")

        self.assertEqual(415, self.responses[0][0])
        self.assertIn("http/json", self.responses[0][1]["message"])

    def test_missing_or_wrong_token_is_rejected_before_storage(self):
        raw = json.dumps(export_request({"body": {"stringValue": "secret"}})).encode()
        handler = Handler()

        self.controller.post(handler, "/v1/logs", raw, "application/json")

        self.assertEqual(401, handler.status)
        self.assertEqual(16, json.loads(handler.wfile.getvalue())["code"])
        self.assertIn(("www-authenticate", 'Bearer realm="ciel-runtime-otlp"'), handler.response_headers)
        self.assertEqual([], self.responses)
        self.assertEqual([], self.notices)


if __name__ == "__main__":
    unittest.main()
