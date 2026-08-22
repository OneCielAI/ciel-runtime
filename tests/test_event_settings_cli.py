"""Parameter injection for the external-event and remote-instruction menus."""

import argparse
import unittest

from ciel_runtime_support.event_settings_cli import (
    EventSettingsCli,
    EventSettingsCliError,
    EventSettingsCliPorts,
    handlers,
)


class _Vault:
    def __init__(self):
        self.secrets = {}

    def update(self, receiver_id, values):
        admitted = {name: value for name, value in values.items() if value}
        if admitted:
            self.secrets.setdefault(receiver_id, {}).update(admitted)

    def status(self, receiver_id):
        stored = self.secrets.get(receiver_id, {})
        return {
            "stored_webhook_secret": bool(stored.get("webhook_secret")),
            "stored_authorization": bool(stored.get("authorization")),
        }


class _ReceiverService:
    def __init__(self, config=None):
        self.receivers = {"default": dict(config or {})}
        self.vault = _Vault()
        self.saved = []

    def receiver_configs(self):
        return {key: dict(value) for key, value in self.receivers.items()}

    def save_receiver(self, receiver_id, body):
        if str(body.get("transport")) == "sse" and not str(body.get("url") or ""):
            raise ValueError("SSE receiver requires url")
        self.saved.append(dict(body))
        stored = dict(body)
        stored.pop("webhook_secret", None)
        stored.pop("authorization", None)
        self.receivers[receiver_id] = stored
        self.vault.update(
            receiver_id,
            {
                "webhook_secret": str(body.get("webhook_secret") or ""),
                "authorization": str(body.get("authorization") or ""),
            },
        )
        return stored


def _args(*values):
    return argparse.Namespace(values=list(values))


class EventSettingsCliTests(unittest.TestCase):
    def _sync(self):
        self.syncs += 1
        return ["claude: updated"]

    def setUp(self):
        self.syncs = 0
        self.config = {}
        self.service = _ReceiverService(
            {
                "enabled": True,
                "transport": "sse",
                "url": "https://events.example/stream",
                "event_types": ["a.b"],
                "cursor_json_pointer": "/data/id",
                "cursor_query_parameter": "since",
            }
        )
        self.output = []
        self.controller = EventSettingsCli(
            EventSettingsCliPorts(
                load_config=lambda: self.config,
                save_config=lambda value: self.config.update(value),
                receiver_service=lambda: self.service,
                sync_instructions=self._sync,
                sync_memories=lambda: ["remote-memory: updated"],
                output=self.output.append,
            )
        )

    # -- external events ---------------------------------------------------

    def test_naming_one_key_leaves_every_other_value_alone(self):
        self.controller.external_events(_args("enabled=false"))

        stored = self.service.receivers["default"]
        self.assertFalse(stored["enabled"])
        self.assertEqual("sse", stored["transport"])
        self.assertEqual("https://events.example/stream", stored["url"])
        self.assertEqual(["a.b"], stored["event_types"])
        self.assertEqual("/data/id", stored["cursor_json_pointer"])
        self.assertEqual("since", stored["cursor_query_parameter"])

    def test_boolean_aliases_are_explicit_not_a_toggle(self):
        for spelling in ("false", "off", "0", "no", "disable", "disabled"):
            with self.subTest(spelling=spelling):
                self.controller.external_events(_args(f"enabled={spelling}"))
                self.assertFalse(self.service.receivers["default"]["enabled"])
        for spelling in ("true", "on", "1", "yes", "enable", "enabled"):
            with self.subTest(spelling=spelling):
                self.controller.external_events(_args(f"enabled={spelling}"))
                self.assertTrue(self.service.receivers["default"]["enabled"])

    def test_event_types_accept_a_comma_separated_list(self):
        self.controller.external_events(_args("event_types=a.b, c.d ,"))

        self.assertEqual(["a.b", "c.d"], self.service.receivers["default"]["event_types"])

    def test_secrets_are_stored_and_never_echoed(self):
        self.controller.external_events(
            _args("webhook_secret=whsec_abc", "authorization=Bearer {TOKEN}")
        )

        self.assertEqual(
            "whsec_abc", self.service.vault.secrets["default"]["webhook_secret"]
        )
        self.assertNotIn("whsec_abc", "\n".join(self.output))
        self.assertEqual("stored", self.controller.external_event_values()["webhook_secret"])

    def test_unknown_external_key_is_rejected(self):
        with self.assertRaises(EventSettingsCliError):
            self.controller.external_events(_args("bogus=1"))
        self.assertEqual([], self.service.saved)

    def test_unknown_boolean_spelling_is_rejected_rather_than_defaulted(self):
        with self.assertRaises(EventSettingsCliError):
            self.controller.external_events(_args("enabled=maybe"))
        self.assertEqual([], self.service.saved)

    def test_transport_only_accepts_webhook_or_sse(self):
        with self.assertRaises(EventSettingsCliError):
            self.controller.external_events(_args("transport=grpc"))

    def test_a_token_without_a_value_is_rejected(self):
        with self.assertRaises(EventSettingsCliError):
            self.controller.external_events(_args("enabled"))

    def test_no_arguments_reports_the_current_values(self):
        self.controller.external_events(_args())

        report = "\n".join(self.output)
        self.assertIn("transport=sse", report)
        self.assertIn("url=https://events.example/stream", report)

    # -- transcript events ------------------------------------------------

    def test_transcript_events_store_registered_destination_and_limits(self):
        self.controller.transcript_events(
            _args(
                "enabled=true",
                "url=https://memory.example/transcripts",
                "authorization=Bearer token",
                "timeout_seconds=8",
                "poll_interval_ms=500",
                "max_batch_bytes=2097152",
                "start_mode=beginning",
            )
        )

        stored = self.config["transcript_events"]
        self.assertTrue(stored["enabled"])
        self.assertEqual("https://memory.example/transcripts", stored["url"])
        self.assertEqual("Bearer token", stored["authorization"])
        self.assertEqual(8, stored["timeout_seconds"])
        self.assertEqual(500, stored["poll_interval_ms"])
        self.assertEqual(2_097_152, stored["max_batch_bytes"])
        self.assertEqual("beginning", stored["start_mode"])
        self.assertNotIn("Bearer token", "\n".join(self.output))

    def test_transcript_events_require_url_when_enabled(self):
        with self.assertRaisesRegex(EventSettingsCliError, "requires url"):
            self.controller.transcript_events(_args("enabled=true"))
        self.assertNotIn("transcript_events", self.config)

    def test_transcript_events_validate_mode_url_and_numeric_limits(self):
        for value in (
            "start_mode=all",
            "url=file:///tmp/transcript",
            "poll_interval_ms=99",
            "max_batch_bytes=100",
        ):
            with self.subTest(value=value), self.assertRaises(EventSettingsCliError):
                self.controller.transcript_events(_args(value))

    # -- remote instructions -----------------------------------------------

    def test_remote_urls_are_written_and_others_preserved(self):
        self.controller.remote_instructions(_args("claude_url=https://a.example/CLAUDE.md"))
        self.controller.remote_instructions(_args("grok_url=https://b.example/AGENTS.md"))

        remote = self.config["remote_instructions"]
        self.assertEqual("https://a.example/CLAUDE.md", remote["claude_url"])
        self.assertEqual("https://b.example/AGENTS.md", remote["grok_url"])

    def test_every_launchable_runtime_has_a_url_parameter(self):
        for key in ("claude_url", "codex_url", "agy_url", "kimi_url", "grok_url"):
            with self.subTest(key=key):
                self.controller.remote_instructions(_args(f"{key}=https://x.example/f.md"))
                self.assertEqual(
                    "https://x.example/f.md",
                    self.config["remote_instructions"][key],
                )

    def test_a_blank_url_clears_the_entry(self):
        self.controller.remote_instructions(_args("grok_url=https://b.example/AGENTS.md"))
        self.controller.remote_instructions(_args("grok_url="))

        self.assertEqual("", self.config["remote_instructions"]["grok_url"])

    def test_non_http_url_is_rejected(self):
        with self.assertRaises(EventSettingsCliError):
            self.controller.remote_instructions(_args("grok_url=ftp://nope"))
        self.assertNotIn("remote_instructions", self.config)

    def test_timeout_is_bounded(self):
        self.controller.remote_instructions(_args("timeout_seconds=9"))
        self.assertEqual(9, self.config["remote_instructions"]["timeout_seconds"])
        for value in ("0", "31", "soon"):
            with self.subTest(value=value):
                with self.assertRaises(EventSettingsCliError):
                    self.controller.remote_instructions(_args(f"timeout_seconds={value}"))

    def test_remote_authorization_is_masked_in_the_report(self):
        self.controller.remote_instructions(_args("authorization=Bearer {TOKEN}"))
        self.output.clear()
        self.controller.remote_instructions(_args())

        report = "\n".join(self.output)
        self.assertIn("authorization=stored", report)
        self.assertNotIn("TOKEN", report)

    def test_sync_runs_on_its_own(self):
        self.controller.remote_instructions(_args("sync"))

        self.assertEqual(1, self.syncs)
        self.assertNotIn("remote_instructions", self.config)
        self.assertIn("claude: updated", "\n".join(self.output))

    def test_sync_runs_after_the_batch_is_stored(self):
        self.controller.remote_instructions(
            _args("enabled=true", "grok_url=https://g.example/AGENTS.md", "sync")
        )

        self.assertEqual(1, self.syncs)
        self.assertTrue(self.config["remote_instructions"]["enabled"])
        self.assertEqual(
            "https://g.example/AGENTS.md",
            self.config["remote_instructions"]["grok_url"],
        )

    def test_a_rejected_parameter_in_the_batch_skips_the_sync(self):
        with self.assertRaises(EventSettingsCliError):
            self.controller.remote_instructions(_args("grok_url=ftp://nope", "sync"))

        self.assertEqual(0, self.syncs)

    def test_environment_reference_is_stored_verbatim_not_expanded(self):
        self.controller.remote_instructions(_args("authorization=Bearer {AINET_API_KEY}"))

        self.assertEqual(
            "Bearer {AINET_API_KEY}",
            self.config["remote_instructions"]["authorization"],
        )

    # -- remote memory ----------------------------------------------------

    def test_remote_memory_manifest_and_limits_are_stored(self):
        self.controller.remote_memory(
            _args(
                "enabled=true",
                "manifest_url=https://memory.example/manifest.json",
                "directory=.ciel/team-memory",
                "max_files=42",
            )
        )

        stored = self.config["remote_memory"]
        self.assertTrue(stored["enabled"])
        self.assertEqual(
            "https://memory.example/manifest.json", stored["manifest_url"]
        )
        self.assertEqual(".ciel/team-memory", stored["directory"])
        self.assertEqual(42, stored["max_files"])

    def test_remote_memory_rejects_unsafe_directory_and_non_http_manifest(self):
        for token in (
            "directory=../outside",
            "directory=C:\\outside",
            "manifest_url=ftp://memory.example/manifest.json",
        ):
            with self.subTest(token=token), self.assertRaises(EventSettingsCliError):
                self.controller.remote_memory(_args(token))
        self.assertNotIn("remote_memory", self.config)

    def test_remote_memory_authorization_is_masked_and_sync_is_available(self):
        self.controller.remote_memory(_args("authorization=Bearer {MEMORY_TOKEN}"))
        self.output.clear()

        self.controller.remote_memory(_args())
        self.controller.remote_memory(_args("sync"))

        report = "\n".join(self.output)
        self.assertIn("authorization=stored", report)
        self.assertNotIn("MEMORY_TOKEN", report)
        self.assertIn("remote-memory: updated", report)

    def test_remote_memory_limits_are_bounded(self):
        for token in ("timeout_seconds=0", "max_files=2049", "max_file_bytes=nope"):
            with self.subTest(token=token), self.assertRaises(EventSettingsCliError):
                self.controller.remote_memory(_args(token))

    # -- CLI boundary ------------------------------------------------------

    def test_handlers_report_a_rejected_parameter_without_a_traceback(self):
        external, _transcript, remote, _memory = handlers(
            EventSettingsCliPorts(
                load_config=lambda: self.config,
                save_config=lambda value: self.config.update(value),
                receiver_service=lambda: self.service,
                sync_instructions=self._sync,
                sync_memories=lambda: ["remote-memory: updated"],
                output=self.output.append,
            )
        )

        with self.assertRaises(SystemExit) as raised:
            external(_args("enabled=maybe"))
        self.assertIn("enabled must be one of", str(raised.exception))

        with self.assertRaises(SystemExit) as raised:
            remote(_args("timeout_seconds=99"))
        self.assertIn("1 to 30", str(raised.exception))

    def test_receiver_contract_errors_also_arrive_as_a_message(self):
        external, _transcript, _remote, _memory = handlers(
            EventSettingsCliPorts(
                load_config=lambda: self.config,
                save_config=lambda value: self.config.update(value),
                receiver_service=lambda: _ReceiverService(),
                sync_instructions=self._sync,
                sync_memories=lambda: ["remote-memory: updated"],
                output=self.output.append,
            )
        )

        with self.assertRaises(SystemExit) as raised:
            external(_args("transport=sse"))
        self.assertIn("SSE receiver requires url", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
