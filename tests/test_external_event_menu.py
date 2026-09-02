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


if __name__ == "__main__":
    unittest.main()
