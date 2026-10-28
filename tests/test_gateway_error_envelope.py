"""Official gateway combo metadata is not generation
unknown siblings still are unknown.

Synthetic contract fixture from OneCiel/ciel-ai-router error.ts blob
9d80dc159fd6ab07ccd79f38bf1b6b62a05c817a, not a captured live body.
"""
import io
import json
import unittest
import urllib.error

from ciel_runtime_support import transient_retry as retry
import test_transient_retry_late_sse as late

OUTPUT, PING = late.OUTPUT, late.PING


def envelope():
    recovery = {"action": "retry", "next_step": "Retry the request."}
    return {"error": {"type": "server_error", "code": "504", "message": "Model timed out (HTTP 504)"},
            "diagnostics": {"poolSize": 2, "attempted": 2, "excluded": [],
                            "attemptOrder": [{"provider": "fixture", "model": "model"}],
                            "terminalReason": "all_targets_failed", "recovery": recovery},
            "recovery_hint": recovery.copy()}


def frame(value):
    return bytes([10]).join([b"event: error", b"data: " + json.dumps(value).encode(), b"", b""])


class GatewayEnvelopeTests(unittest.TestCase):
    def test_official_contract_is_recognized(self):
        value = envelope()
        self.assertEqual(retry._error_verdict(frame(value)),
                         ("server_error", "Model timed out (HTTP 504)"))
        self.assertTrue(retry._retryable_verdict(503, frame(value)))

    def test_delayed_contract_retries_before_generation(self):
        result, hits = late.LateSSETests().serve([PING + frame(envelope()), OUTPUT])
        self.assertEqual(result, OUTPUT)
        self.assertEqual(hits, [b"same-payload"] * 2)

    def test_http_contract_uses_same_budget(self):
        raw = json.dumps(envelope()).encode()
        calls, logs = [], []
        def open_once():
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError("http://fixture", 504, "timeout", {}, io.BytesIO(raw))
            return io.BytesIO(b"ok")
        with retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                log=lambda *args: logs.append(args), sleep=lambda _: None) as response:
            self.assertEqual(response.read(), b"ok")
        self.assertEqual(len(calls), 2)

    def test_unknown_or_malformed_metadata_still_rejected(self):
        variants = []
        for key, val in (("output", []), ("response", {}), ("PRIVATE-KEY", "secret")):
            v = envelope()
            v[key] = val
            variants.append(v)
        for key, val in (("diagnostics", []), ("recovery_hint", "retry")):
            v = envelope()
            v[key] = val
            variants.append(v)
        for key, val in (("poolSize", True), ("attempted", "2"), ("output", []),
                         ("attemptOrder", [{"provider": "fixture", "model": "model", "content": "secret"}])):
            v = envelope()
            v["diagnostics"][key] = val
            variants.append(v)
        for key, val in (("action", "generate"), ("retry_after_seconds", -1),
                         ("retry_after_seconds", True), ("retry_after_seconds", 3601), ("content", "secret")):
            v = envelope()
            v["recovery_hint"][key] = val
            variants.append(v)
        for value in variants:
            with self.subTest(value=value):
                self.assertIsNone(retry._error_verdict(frame(value)))

    def test_terminal_error_is_not_reclassified_by_retry_hint(self):
        for kind, text in (("authentication_error", "invalid API key"),
                           ("server_error", "quota exhausted")):
            value = envelope()
            value["error"].update(type=kind, message=text)
            self.assertFalse(retry._retryable_verdict(503, frame(value)))

    def test_metadata_does_not_reopen_after_output_or_mixed_frames(self):
        error = frame(envelope())
        for raw in (OUTPUT + error, error + OUTPUT):
            result, hits = late.LateSSETests().serve([raw, OUTPUT])
            self.assertEqual(result, raw)
            self.assertEqual(len(hits), 1)

    def test_optional_contract_metadata(self):
        for key in ("diagnostics", "recovery_hint"):
            value = envelope()
            del value[key]
            self.assertIsNotNone(retry._error_verdict(frame(value)))
        value = envelope()
        value["diagnostics"]["skippedTargets"] = [{"reason": "persisted_cooldown", "targets": ["fixture/model"]}]
        self.assertIsNotNone(retry._error_verdict(frame(value)))

    def test_duplicate_keys_and_nonfinite_values_still_rejected(self):
        raw = json.dumps(envelope())
        for bad in (raw.replace('"poolSize": 2', '"poolSize": 2, "poolSize": 1'),
                    raw.replace('"poolSize": 2', '"poolSize": NaN')):
            self.assertIsNone(retry._error_verdict(bad.encode()))
