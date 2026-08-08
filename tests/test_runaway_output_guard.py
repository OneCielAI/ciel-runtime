import unittest

from ciel_runtime_support.runaway_output_guard import (
    CONSECUTIVE,
    INTERLEAVED,
    RunawayOutputDetector,
    RunawayOutputPolicy,
    _find_consecutive_loop,
    find_runaway_tail,
    policy_from_env,
    trim_runaway_tail,
)

# The block the reported ollama-cloud deepseek-v4-flash:0731 session repeated
# until the request timed out, copied from the captured terminal frame.
REPORTED_UNIT = (
    "먼저 관련 레포와 지표 계산/차트 데이터 경로를 찾겠습니다. "
    "responseZEC 4h/8h 인디케이터 누락 원인을 확인하겠습니다. "
)


def env(values):
    return lambda name: values.get(name)


class ReportedLoopTests(unittest.TestCase):
    def test_detects_the_reported_repetition_loop(self):
        text = "관련 파일을 살펴보겠습니다.\n\n" + REPORTED_UNIT * 60

        verdict = find_runaway_tail(text)

        self.assertIsNotNone(verdict)
        self.assertEqual(len(REPORTED_UNIT), verdict.period_chars)
        self.assertEqual(60, verdict.repeats)
        self.assertEqual(len(REPORTED_UNIT) * 60, verdict.repeated_chars)

    def test_detects_a_loop_cut_mid_block(self):
        # A stream is almost never sliced on a block boundary. The probe still
        # anchors one period back, so a trailing partial block is detected.
        text = REPORTED_UNIT * 60 + REPORTED_UNIT[: len(REPORTED_UNIT) // 2]

        verdict = find_runaway_tail(text)

        self.assertIsNotNone(verdict)
        self.assertEqual(len(REPORTED_UNIT), verdict.period_chars)

    def test_trim_keeps_the_prefix_and_one_block(self):
        prefix = "관련 파일을 살펴보겠습니다.\n\n"

        trimmed, verdict = trim_runaway_tail(prefix + REPORTED_UNIT * 60)

        self.assertIsNotNone(verdict)
        self.assertEqual(prefix + REPORTED_UNIT, trimmed)


class InterleavedLoopTests(unittest.TestCase):
    """A loop rarely repeats cleanly; variation between repeats must still count."""

    def test_detects_a_block_that_recurs_with_noise_between_repeats(self):
        text = "".join(
            REPORTED_UNIT + f" 시도 {index} 번째 경로를 확인합니다.\n" for index in range(40)
        )

        # Strict periodicity cannot hold: every repeat has a different number.
        self.assertIsNone(_find_consecutive_loop(text, RunawayOutputPolicy()))

        verdict = find_runaway_tail(text)
        self.assertIsNotNone(verdict)
        self.assertEqual(INTERLEAVED, verdict.kind)
        self.assertGreaterEqual(verdict.repeats, 10)

    def test_detects_an_alternating_two_phrase_loop(self):
        text = ("첫 번째 후보 파일을 열어 확인하겠습니다. 이제 지표 계산 경로를 추적합니다.\n"
                "다시 관련 레포와 차트 데이터 경로를 찾겠습니다. 원인을 확인하겠습니다.\n") * 40

        verdict = find_runaway_tail(text)

        self.assertIsNotNone(verdict)
        self.assertGreaterEqual(verdict.repeats, 10)

    def test_trim_keeps_the_prefix_and_the_first_pass(self):
        prefix = "요청을 확인했습니다.\n\n"
        text = prefix + "".join(
            REPORTED_UNIT + f" 시도 {index}.\n" for index in range(40)
        )

        trimmed, verdict = trim_runaway_tail(text)

        self.assertIsNotNone(verdict)
        self.assertTrue(trimmed.startswith(prefix))
        self.assertLess(len(trimmed), len(text) // 2)

    def test_reports_which_rule_fired(self):
        consecutive = find_runaway_tail(REPORTED_UNIT * 60)
        self.assertEqual(CONSECUTIVE, consecutive.kind)
        self.assertIn("in a row", consecutive.notice())

        interleaved = find_runaway_tail(
            "".join(REPORTED_UNIT + f" {index}.\n" for index in range(40))
        )
        self.assertEqual(INTERLEAVED, interleaved.kind)
        self.assertIn("within the last", interleaved.notice())


class FalsePositiveTests(unittest.TestCase):
    def test_a_recurring_line_in_real_content_is_left_alone(self):
        # A 62-character boilerplate assertion recurring 40 times, each one a
        # minority of the surrounding code: density stays under the bar.
        boilerplate = "        self.assertEqual(expected_value, computed_value, msg)\n"
        cases = [
            "parses an empty payload without raising",
            "keeps the original ordering of the rows",
            "rejects a negative retry budget outright",
            "falls back to the bundled catalog offline",
            "reports the upstream status verbatim",
        ]
        text = "".join(
            f"    def test_case_{index:03d}(self):\n"
            f'        """{cases[index % len(cases)]} ({index})."""\n'
            f"        computed_value = compute(sample_{index:03d}, depth={index})\n"
            f"        expected_value = EXPECTED[{index}]\n" + boilerplate
            for index in range(40)
        )

        self.assertIsNone(find_runaway_tail(text))

    def test_a_long_markdown_table_is_left_alone(self):
        header = "| id | value | other | tail |\n| --- | --- | --- | --- |\n"
        text = header + "".join(
            f"| cell {index:03d} | value {index:03d} | other {index:03d} | tail {index:03d} |\n"
            for index in range(200)
        )

        self.assertIsNone(find_runaway_tail(text))

    def test_density_is_a_threshold_not_a_proof(self):
        # Documented limitation: content that really is mostly one repeated
        # block sits in the same place a loop does. A pathological table with a
        # separator between every row is under the default bar, but only just,
        # so operators get CIEL_RUNTIME_RUNAWAY_MIN_DENSITY and the kill switch.
        row = "| ---------- | ---------- | ---------- | ---------- |\n"
        text = "".join(
            row + f"| cell {index:03d} | value {index:03d} | other {index:03d} | tail {index:03d} |\n"
            for index in range(60)
        )

        self.assertIsNone(find_runaway_tail(text))
        self.assertIsNotNone(
            find_runaway_tail(text, RunawayOutputPolicy(min_density_percent=50))
        )

    def test_a_few_duplicated_paragraphs_are_left_alone(self):
        paragraph = "Retrying the failed request with the same arguments.\n"

        self.assertIsNone(find_runaway_tail(paragraph * 9))

    def test_a_short_repeated_run_is_left_alone(self):
        # 80 repeats, but only 240 characters: under the repeated-length budget.
        self.assertIsNone(find_runaway_tail("abc" * 80))

    def test_ordinary_long_output_is_left_alone(self):
        text = "".join(f"line {index}: computed a distinct value\n" for index in range(400))

        self.assertIsNone(find_runaway_tail(text))

    def test_empty_and_short_text_are_left_alone(self):
        self.assertIsNone(find_runaway_tail(""))
        self.assertIsNone(find_runaway_tail("hello"))


class StreamingDetectorTests(unittest.TestCase):
    def test_fires_mid_stream_and_stays_decided(self):
        detector = RunawayOutputDetector()
        verdicts = []
        for _ in range(200):
            verdicts.append(detector.feed(REPORTED_UNIT))

        first_hit = next(index for index, value in enumerate(verdicts) if value)
        self.assertLess(first_hit, 60)
        self.assertIs(detector.verdict, verdicts[-1])
        self.assertEqual(len(REPORTED_UNIT), detector.verdict.period_chars)

    def test_stops_consuming_after_a_verdict(self):
        detector = RunawayOutputDetector()
        for _ in range(200):
            detector.feed(REPORTED_UNIT)
        settled = detector.total_chars

        detector.feed("more text that arrives after the guard already decided")

        self.assertEqual(settled, detector.total_chars)

    def test_healthy_stream_never_fires(self):
        detector = RunawayOutputDetector()
        for index in range(2000):
            self.assertIsNone(detector.feed(f"step {index}: distinct progress line\n"))
        self.assertIsNone(detector.verdict)

    def test_tail_buffer_stays_bounded(self):
        policy = RunawayOutputPolicy(min_repeats=10, max_period_chars=64)
        detector = RunawayOutputDetector(policy)
        for index in range(5000):
            detector.feed(f"line {index}\n")

        self.assertLessEqual(len(detector._tail), policy.tail_budget())
        self.assertGreater(detector.total_chars, policy.tail_budget())

    def test_disabled_policy_never_fires(self):
        detector = RunawayOutputDetector(RunawayOutputPolicy(enabled=False))
        for _ in range(200):
            self.assertIsNone(detector.feed(REPORTED_UNIT))
        self.assertEqual(0, detector.total_chars)


class PolicyFromEnvTests(unittest.TestCase):
    def test_defaults_when_unset(self):
        policy = policy_from_env(env({}))

        self.assertTrue(policy.enabled)
        self.assertEqual(10, policy.min_repeats)
        self.assertEqual(2000, policy.min_repeated_chars)

    def test_kill_switch(self):
        for value in ("0", "off", "false", "no", "disabled"):
            self.assertFalse(policy_from_env(env({"CIEL_RUNTIME_RUNAWAY_GUARD": value})).enabled)
        self.assertTrue(policy_from_env(env({"CIEL_RUNTIME_RUNAWAY_GUARD": "on"})).enabled)

    def test_threshold_overrides(self):
        policy = policy_from_env(
            env(
                {
                    "CIEL_RUNTIME_RUNAWAY_MIN_REPEATS": "4",
                    "CIEL_RUNTIME_RUNAWAY_MIN_CHARS": "120",
                    "CIEL_RUNTIME_RUNAWAY_MAX_PERIOD": "256",
                }
            )
        )

        self.assertEqual(4, policy.min_repeats)
        self.assertEqual(120, policy.min_repeated_chars)
        self.assertEqual(256, policy.max_period_chars)
        self.assertIsNotNone(find_runaway_tail("loop forever. " * 20, policy))

    def test_invalid_overrides_fall_back_to_defaults(self):
        policy = policy_from_env(
            env(
                {
                    "CIEL_RUNTIME_RUNAWAY_MIN_REPEATS": "not-a-number",
                    "CIEL_RUNTIME_RUNAWAY_MIN_CHARS": "-5",
                }
            )
        )

        self.assertEqual(10, policy.min_repeats)
        self.assertEqual(2000, policy.min_repeated_chars)


class VerdictTextTests(unittest.TestCase):
    def test_notice_states_the_measured_facts(self):
        verdict = find_runaway_tail(REPORTED_UNIT * 60)

        notice = verdict.notice()
        self.assertIn("[ciel-runtime]", notice)
        self.assertIn(f"{verdict.period_chars}-character block", notice)
        self.assertIn(f"{verdict.repeats} times", notice)
        self.assertIn("period=", verdict.log_fields())


if __name__ == "__main__":
    unittest.main()
