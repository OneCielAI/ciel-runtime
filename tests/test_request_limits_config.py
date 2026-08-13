import unittest

from ciel_runtime_support import prelaunch
from ciel_runtime_support.request_limits_config import (
    INFLIGHT_REQUEST_TECHNICAL_MAX_BYTES,
    MIB,
    RequestLimitsMenuService,
    REQUEST_BODY_MEMORY_MULTIPLIER,
    TTS_BATCH_REQUEST_MAX_BYTES,
    base64_json_wire_max_bytes,
    parse_menu_size,
    resolve_workspace_request_limits,
    update_workspace_request_limit,
)
from ciel_runtime_support.workspace_router_selection import workspace_digest


class WorkspaceRequestLimitTests(unittest.TestCase):
    def test_defaults_equal_hard_maxima_and_are_workspace_scoped(self):
        limits = resolve_workspace_request_limits({}, "C:/work/alpha", {})

        self.assertEqual(512 * MIB, limits.model_request_max_bytes)
        self.assertEqual(500 * MIB, limits.chat_attachment_max_bytes)
        self.assertEqual(500 * MIB, limits.speech_audio_max_bytes)
        self.assertEqual(500 * MIB, limits.tts_reference_audio_max_bytes)
        self.assertEqual(4 * 1024 * MIB, limits.inflight_request_max_bytes)
        self.assertEqual("default", limits.sources["model_request_max_bytes"])

    def test_every_configurable_default_is_the_hard_max_and_minimum_is_one_byte(self):
        from ciel_runtime_support.request_limits_config import REQUEST_LIMIT_SPECS

        for spec in REQUEST_LIMIT_SPECS:
            with self.subTest(key=spec.key):
                self.assertEqual(spec.hard_max_bytes, spec.default_bytes)
                self.assertEqual(1, spec.minimum_bytes)

    def test_workspace_records_do_not_leak_between_folders(self):
        config = {}
        update_workspace_request_limit(config, "C:/work/alpha", "model_request_max_bytes", "256 MiB")

        alpha = resolve_workspace_request_limits(config, "C:/work/alpha", {})
        beta = resolve_workspace_request_limits(config, "C:/work/beta", {})

        self.assertEqual(256 * MIB, alpha.model_request_max_bytes)
        self.assertEqual(512 * MIB, beta.model_request_max_bytes)
        record = config["request_limits"][workspace_digest("C:/work/alpha")]
        self.assertEqual(256 * MIB, record["model_request_max_bytes"])

    def test_environment_overrides_workspace_and_is_clamped_to_hard_max(self):
        config = {}
        update_workspace_request_limit(config, "C:/work/alpha", "model_request_max_bytes", "256 MiB")

        limits = resolve_workspace_request_limits(
            config,
            "C:/work/alpha",
            {"CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES": str(900 * MIB)},
        )

        self.assertEqual(512 * MIB, limits.model_request_max_bytes)
        self.assertEqual(
            "environment:CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES",
            limits.sources["model_request_max_bytes"],
        )

    def test_inflight_is_auto_raised_to_largest_derived_wire_limit(self):
        config = {}
        update_workspace_request_limit(config, "C:/work/alpha", "chat_attachment_max_bytes", "500 MiB")
        update_workspace_request_limit(config, "C:/work/alpha", "inflight_request_max_bytes", "4 MiB")

        limits = resolve_workspace_request_limits(config, "C:/work/alpha", {})

        expected = base64_json_wire_max_bytes(500 * MIB)
        self.assertEqual(4 * MIB, limits.configured_inflight_request_max_bytes)
        self.assertEqual(expected, limits.chat_attachment_wire_max_bytes)
        self.assertEqual(
            REQUEST_BODY_MEMORY_MULTIPLIER * TTS_BATCH_REQUEST_MAX_BYTES,
            limits.inflight_request_max_bytes,
        )

    def test_provider_neutral_tts_batch_ceiling_is_included_in_inflight_floor(self):
        config = {}
        workspace = "C:/work/alpha"
        update_workspace_request_limit(config, workspace, "model_request_max_bytes", "1 byte")
        update_workspace_request_limit(config, workspace, "chat_attachment_max_bytes", "1 byte")
        update_workspace_request_limit(config, workspace, "speech_audio_max_bytes", "1 byte")
        update_workspace_request_limit(config, workspace, "tts_reference_audio_max_bytes", "1 byte")
        update_workspace_request_limit(config, workspace, "inflight_request_max_bytes", "1 byte")

        limits = resolve_workspace_request_limits(config, workspace, {})

        self.assertEqual(TTS_BATCH_REQUEST_MAX_BYTES, limits.tts_batch_wire_max_bytes)
        self.assertEqual(
            TTS_BATCH_REQUEST_MAX_BYTES,
            limits.largest_wire_request_bytes,
        )
        self.assertEqual(
            REQUEST_BODY_MEMORY_MULTIPLIER * TTS_BATCH_REQUEST_MAX_BYTES,
            limits.inflight_request_max_bytes,
        )
        self.assertLessEqual(
            limits.inflight_request_max_bytes,
            INFLIGHT_REQUEST_TECHNICAL_MAX_BYTES,
        )
        self.assertGreaterEqual(
            TTS_BATCH_REQUEST_MAX_BYTES,
            base64_json_wire_max_bytes(500 * MIB),
        )

    def test_one_byte_value_has_an_actionable_menu_error_and_display(self):
        config = {}
        update_workspace_request_limit(
            config,
            "C:/work/alpha",
            "model_request_max_bytes",
            "1 byte",
        )
        service = RequestLimitsMenuService(lambda: config, lambda _value: None, "C:/work/alpha", {})

        rows, _values = service.panel_rows(config)

        self.assertIn("1 byte", rows[0])

    def test_menu_service_persists_and_resets_only_the_active_workspace(self):
        config = {}
        saved = []

        def save(value):
            saved.append(value.copy())

        service = RequestLimitsMenuService(lambda: config, save, "C:/work/alpha", {})
        lines = service.update("speech_audio_max_bytes", "200")
        rows, values = service.panel_rows(config)

        self.assertTrue(saved)
        self.assertIn("200 MiB", lines[0])
        self.assertIn("ASR / speech input", rows[2])
        self.assertEqual("speech_audio_max_bytes", values[2])

        service.update("reset", "")
        self.assertNotIn("request_limits", config)

    def test_menu_size_parser_uses_mib_for_bare_numbers(self):
        self.assertEqual(128 * MIB, parse_menu_size("128"))
        self.assertEqual(1024 * MIB, parse_menu_size("1 GiB"))
        with self.assertRaises(ValueError):
            parse_menu_size("many")

    def test_main_menu_action_matches_request_limits_row(self):
        self.assertEqual("request-limits", prelaunch.MAIN_MENU_ACTIONS[-3])
        self.assertEqual("web-backend", prelaunch.MAIN_MENU_ACTIONS[-2])


if __name__ == "__main__":
    unittest.main()
