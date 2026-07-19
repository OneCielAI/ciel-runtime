import unittest

from ciel_runtime_support.timeout_profile import (
    TimeoutProfilePorts,
    TimeoutProfileService,
    TimeoutProfileSettings,
)


def positive_int(value):
    try:
        fixed = int(value)
    except (TypeError, ValueError):
        return None
    return fixed if fixed > 0 else None


class TimeoutProfileServiceTests(unittest.TestCase):
    def service(self):
        return TimeoutProfileService(
            TimeoutProfileSettings(
                default_timeout_ms=300000,
                profiles={
                    "fast": (120000, "Fast", "short requests"),
                    "slow": (600000, "Slow", "long requests"),
                },
                localized_profiles={
                    "ko": {"fast": ("빠름", "짧은 요청")},
                },
                llm_preset_timeouts={"long-context": 600000},
            ),
            TimeoutProfilePorts(
                positive_int=positive_int,
                pad_cells=lambda value, width: value.ljust(width),
                ui_text=lambda key, _language: "Back" if key == "back" else key,
                format_minutes=lambda value, _language: f"{value // 60000} min",
            ),
        )

    def test_profile_lookup_and_localized_fallback(self):
        service = self.service()

        self.assertEqual("fast", service.profile_id(120000))
        self.assertEqual(("빠름", "짧은 요청"), service.text("fast", "ko"))
        self.assertEqual(("Slow", "long requests"), service.text("slow", "ko"))
        self.assertEqual("사용자 지정", service.text("__custom__", "ko")[0])

    def test_status_distinguishes_custom_and_idle_timeout(self):
        status = self.service().status(
            {"request_timeout_ms": 180000, "stream_idle_timeout_ms": 120000},
            "en",
        )

        self.assertEqual("Custom; 180000ms; idle 120000ms", status)

    def test_panel_marks_current_profile_and_preserves_value_order(self):
        rows, values = self.service().panel_rows(
            {"request_timeout_ms": 120000}, "en"
        )

        self.assertEqual(["__info__", "fast", "slow", "back"], values)
        self.assertEqual("Current timeout: 120000 ms = 2 min", rows[0])
        self.assertTrue(rows[1].startswith("*"))

    def test_apply_updates_request_and_capped_idle_timeout(self):
        config = {}
        messages = self.service().apply(config, "slow", "en")

        self.assertEqual(600000, config["request_timeout_ms"])
        self.assertEqual(300000, config["stream_idle_timeout_ms"])
        self.assertEqual("Timeout preset: Slow", messages[0])

    def test_llm_preset_tokens_replace_existing_timeout_aliases(self):
        tokens = self.service().with_llm_preset_timeout(
            ["temperature=0.7", "timeout_ms=120000", "stream_idle_timeout=60000"],
            "long-context",
        )

        self.assertEqual(
            [
                "temperature=0.7",
                "timeout=600000",
                "stream_idle_timeout_ms=300000",
            ],
            tokens,
        )


if __name__ == "__main__":
    unittest.main()
