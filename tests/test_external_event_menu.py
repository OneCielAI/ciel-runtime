import unittest

from ciel_runtime_support.external_event_menu import panel_rows, update_config


class _Vault:
    @staticmethod
    def status(_receiver_id):
        return {"stored_webhook_secret": False, "stored_authorization": False}


class _Service:
    vault = _Vault()

    def __init__(self):
        self.receiver = {
            "enabled": True,
            "transport": "sse",
            "input_transport": "session_socket",
            "url": "https://events.example/stream",
        }
        self.saved = None

    def receiver_configs(self):
        return {"default": dict(self.receiver)}

    def public_receiver(self, _receiver_id, receiver):
        return {**receiver, "environment_references": {}}

    def save_receiver(self, _receiver_id, body):
        self.saved = body
        return body


class ExternalEventMenuTests(unittest.TestCase):
    def test_panel_exposes_session_socket_input_transport(self):
        rows, values = panel_rows(_Service(), "http://127.0.0.1:6971")

        self.assertIn("Runtime input transport  [session_socket]", rows)
        self.assertEqual("input_transport", values[rows.index("Runtime input transport  [session_socket]")])

    def test_update_persists_explicit_transport(self):
        service = _Service()

        update_config(service, "input_transport", "tty")

        self.assertEqual("tty", service.saved["input_transport"])

    def test_live_status_comes_only_from_router_control_and_secrets_stay_local(self):
        service = _Service()
        calls = []

        def control(action, body=None):
            calls.append((action, body))
            if action == "status":
                return {"available": True, "status": {
                    "state": "disconnected", "phase": "connect", "diagnostic": {"category": "dns"},
                    "consecutive_failures": 3, "retry_seconds": 8, "recovery_count": 1}}
            return {"available": True, "accepted": True}

        rows, values = panel_rows(service, "http://127.0.0.1:6971", control=control)
        live = next(row for row in rows if row.startswith("Live receiver"))
        self.assertIn("disconnected", live)
        self.assertIn("dns", live)
        self.assertIn("refresh_receiver", values)
        self.assertIn("reconnect_receiver", values)

        update_config(service, "authorization", "Bearer sentinel-secret", control=control)
        save = [body for action, body in calls if action == "save"][0]
        self.assertNotIn("authorization", save)
        self.assertNotIn("sentinel-secret", repr(calls))

    def test_unreachable_router_is_unavailable_not_a_fabricated_state(self):
        rows, _values = panel_rows(_Service(), "http://127.0.0.1:6971",
                                   control=lambda *_a, **_k: {"available": False})
        self.assertIn("unavailable", next(row for row in rows if row.startswith("Live receiver")))
        messages = update_config(_Service(), "reconnect_receiver", None,
                                 control=lambda *_a, **_k: {"available": False})
        self.assertIn("unavailable", messages[0])

    def test_recovery_does_not_label_historical_failure_as_current_error(self):
        rows, _ = panel_rows(_Service(), "http://127.0.0.1:1", control=lambda *_a: {
            "available": True, "status": {"state": "connected", "error": "", "diagnostic": None,
                                          "last_failure": {"category": "dns"}}})
        self.assertIn("error none", next(row for row in rows if row.startswith("Live receiver")))
        messages = update_config(_Service(), "reconnect_receiver", None, control=lambda *_a: {
            "available": True, "accepted": False})
        self.assertIn("rejected", messages[0])

    def test_sse_recovery_row_shows_tool_evidence_and_only_resets_on_evidence(self):
        def control(action, body=None):
            if action == "status":
                return {"available": True, "status": {"state": "connected"}}
            if action == "sse_status":
                return {"available": True, "result": {
                    "ok": True, "problem": True, "reset": False,
                    "evidence": {"reasons": ["queued_input_stale"],
                                 "known_queued_count": 3,
                                 "oldest_queued_age_seconds": 400.0}}}
            if action == "sse_recover":
                return {"available": True, "result": {
                    "ok": True, "problem": True, "reset": True,
                    "evidence": {"reasons": ["queued_input_stale"]}}}
            return {"available": False}

        rows, values = panel_rows(_Service(), "http://127.0.0.1:6971", control=control)
        self.assertIn("recover_sse_delivery", values)
        status = next(row for row in rows if row.startswith("SSE delivery recovery"))
        self.assertIn("problem", status)
        self.assertIn("queued_input_stale", status)
        self.assertIn("queued 3", status)

        messages = update_config(_Service(), "recover_sse_delivery", None, control=control)
        self.assertIn("restart queued", messages[0])
        self.assertIn("not delivery confirmation", messages[1])

    def test_sse_recovery_refuses_without_evidence_or_tool(self):
        def control(action, body=None):
            if action == "status":
                return {"available": True, "status": {}}
            if action == "sse_status":
                return {"available": True, "result": {
                    "ok": True, "problem": False, "reset": False,
                    "evidence": {"reasons": [], "known_queued_count": 0}}}
            if action == "sse_recover":
                return {"available": True, "result": {
                    "ok": True, "problem": False, "reset": False,
                    "reason": "no_sse_delivery_problem", "evidence": {"reasons": []}}}
            return {"available": False}

        rows, _ = panel_rows(_Service(), "http://127.0.0.1:6971", control=control)
        self.assertIn("ok", next(row for row in rows if row.startswith("SSE delivery recovery")))
        messages = update_config(_Service(), "recover_sse_delivery", None, control=control)
        self.assertIn("No session was restarted", messages[0])
        unavailable = update_config(_Service(), "recover_sse_delivery", None,
                                    control=lambda *_a, **_k: {"available": False})
        self.assertIn("unavailable", unavailable[0])

    def test_unobservable_sse_health_is_not_labelled_ok(self):
        def control(action, body=None):
            if action == "status":
                return {"available": True, "status": {}}
            return {"available": True, "result": {
                "ok": True, "problem": False, "reset": False,
                "evidence": {"observable": False, "reason": "delivery_health_unavailable:OSError"}}}
        rows, _ = panel_rows(_Service(), "http://127.0.0.1:6971", control=control)
        row = next(row for row in rows if row.startswith("SSE delivery recovery"))
        self.assertIn("unavailable", row)
        self.assertNotIn("[ok", row)

    def test_compaction_recovery_reason_and_evidence_time_are_visible(self):
        rows, values = panel_rows(_Service(), "http://127.0.0.1:1", control=lambda *_args: {
            "available": True, "status": {}, "result": {"compaction": {
                "phase": "awaiting_shrink", "failure": "shrink_unconfirmed",
                "checkpoint_observed_at": 100, "failure_at": 200}}})
        row = next(row for row in rows if row.startswith("Compaction recovery"))
        self.assertIn("awaiting_shrink", row)
        self.assertIn("shrink_unconfirmed", row)
        self.assertIn("checkpoint observed 100", row)
        self.assertIn("failure at 200", row)
        self.assertEqual("__info__", values[rows.index(row)])
        print(row)

    def test_idle_timeout_is_set_and_blank_clears_it_explicitly(self):
        service = _Service()
        update_config(service, "idle_timeout_seconds", "45")
        self.assertEqual(45.0, service.saved["idle_timeout_seconds"])
        update_config(service, "idle_timeout_seconds", "")
        self.assertIn("idle_timeout_seconds", service.saved)
        self.assertIsNone(service.saved["idle_timeout_seconds"])
        with self.assertRaises(ValueError):
            update_config(service, "idle_timeout_seconds", "999")


if __name__ == "__main__":
    unittest.main()
