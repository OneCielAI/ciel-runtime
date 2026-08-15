import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support import codex_turn_recovery
from ciel_runtime_support.runtime_constants import (
    ROUTED_CODEX_COMPAT_PROMPT,
    ROUTED_COMPAT_PROMPT,
)


def work_request_body(**extra):
    """The shape captured from a stalled routed Codex session.

    The client already ran tools this turn, so the newest user message carries
    tool results rather than prose. Note the Codex tool name: the mid-work check
    must not depend on Claude Code's tool vocabulary.
    """

    body = {
        "model": "ciel-runtime-deepseek-deepseek-v4-flash",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Update the ATR noise threshold in the signal registry and run the tests.",
                    }
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_prev",
                        "name": "exec_command",
                        "input": {"cmd": "git status --short"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_prev",
                        "content": "?? docs/okf/\n?? research/bb-slope-regimes/",
                    }
                ],
            },
        ],
    }
    body.update(extra)
    return body


def text_message(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def tool_message(text=""):
    content = [{"type": "text", "text": text}] if text else []
    content.append({"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/a"}})
    return {"role": "assistant", "content": content}


def reasoning_message(text="private reasoning"):
    return {"role": "assistant", "content": [{"type": "thinking", "thinking": text}]}


def reasoning_notice_message(text="private reasoning"):
    return {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": text},
            {
                "type": "text",
                "text": (
                    "[ciel-runtime] Upstream model returned reasoning without a final answer or "
                    "tool call. Please retry or ask me to continue."
                ),
            },
        ],
    }


def reasoning_promise_message(text):
    return {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "I should run another measurement."},
            {"type": "text", "text": text},
        ],
    }


class PreambleOnlyTurnPolicyTests(unittest.TestCase):
    def test_announcement_after_a_work_request_is_retryable(self):
        self.assertTrue(
            ciel_runtime.should_retry_preamble_only_turn(
                work_request_body(), "이제 실제 조회를 시작합니다.", []
            )
        )

    def test_old_completed_tool_result_does_not_suppress_a_resumed_turn(self):
        body = work_request_body()
        body["messages"][1]["content"][0]["name"] = "Edit"
        body["messages"].extend(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "일부 작업만 진행했습니다."}],
                },
                {"role": "user", "content": [{"type": "text", "text": "계속"}]},
            ]
        )

        self.assertTrue(ciel_runtime.latest_tool_result_indicates_completed_work(body))
        self.assertTrue(
            ciel_runtime.should_retry_preamble_only_turn(
                body, "이제 fallback을 적용합니다.", []
            )
        )

    def test_turn_with_a_tool_call_is_never_retried(self):
        self.assertFalse(
            ciel_runtime.should_retry_preamble_only_turn(
                work_request_body(), "바로 진행합니다.", [{"function": {"name": "Read"}}]
            )
        )

    def test_empty_turn_is_left_to_the_empty_end_turn_path(self):
        self.assertFalse(
            ciel_runtime.should_retry_preamble_only_turn(work_request_body(), "", [])
        )

    def test_long_substantive_answer_is_not_an_announcement(self):
        self.assertFalse(
            ciel_runtime.should_retry_preamble_only_turn(
                work_request_body(), "x" * 401, []
            )
        )

    def test_multiline_report_after_tools_is_treated_as_a_real_answer(self):
        report = "ATR 임계값을 0.8로 조정했습니다.\n테스트 12개가 통과했습니다."

        self.assertFalse(
            ciel_runtime.should_retry_preamble_only_turn(work_request_body(), report, [])
        )

    def test_plan_mode_turn_is_never_retried(self):
        body = work_request_body()
        body["messages"][0]["attachment"] = {"type": "plan_mode"}

        self.assertTrue(ciel_runtime._CONVERSATION_TURN_API.plan_mode_active(body))
        self.assertFalse(
            ciel_runtime.should_retry_preamble_only_turn(body, "이제 시작합니다.", [])
        )


class RecoverPreambleOnlyTurnTests(unittest.TestCase):
    def _services(self, retry_result, calls):
        def collect(handler, provider, pcfg, body):
            calls.append(body)
            return retry_result

        return codex_turn_recovery.CodexTurnRecoveryServices(
            should_retry=ciel_runtime.should_retry_preamble_only_turn,
            collect_message=collect,
            log=lambda *_: None,
        )

    def test_retry_replaces_the_announcement_with_real_work(self):
        calls = []
        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "deepseek",
            {},
            work_request_body(),
            text_message("이제 실제 조회를 시작합니다."),
            self._services(tool_message(), calls),
        )

        self.assertTrue(codex_turn_recovery.message_has_tool_use(recovered))
        self.assertIn("이제 실제 조회를 시작합니다.", codex_turn_recovery.message_text(recovered))
        self.assertEqual(1, len(calls), "recovery must be bounded to one extra call")
        replayed = calls[0]["messages"]
        self.assertEqual("assistant", replayed[-2]["role"])
        self.assertEqual("user", replayed[-1]["role"])
        self.assertIn(
            codex_turn_recovery.CODEX_CONTINUATION_NUDGE,
            replayed[-1]["content"][0]["text"],
        )

    def test_retry_without_tools_keeps_the_original_reply(self):
        calls = []
        original = text_message("이제 실제 조회를 시작합니다.")
        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None, "deepseek", {}, work_request_body(), original,
            self._services(text_message("여전히 계획만 말합니다."), calls),
        )

        self.assertEqual(original, recovered)

    def test_turn_that_already_called_a_tool_is_untouched(self):
        calls = []
        original = tool_message("진행합니다.")
        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None, "deepseek", {}, work_request_body(), original,
            self._services(tool_message(), calls),
        )

        self.assertEqual(original, recovered)
        self.assertEqual([], calls, "no upstream call when the model already acted")

    def test_upstream_failure_during_recovery_keeps_the_original_reply(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError("upstream down")

        original = text_message("이제 시작합니다.")
        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None, "deepseek", {}, work_request_body(), original,
            codex_turn_recovery.CodexTurnRecoveryServices(
                should_retry=ciel_runtime.should_retry_preamble_only_turn,
                collect_message=boom,
                log=lambda *_: None,
            ),
        )

        self.assertEqual(original, recovered)

    def test_kimi_reasoning_only_turn_retries_once_and_accepts_tool_work(self):
        calls = []
        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "kimi",
            {},
            work_request_body(),
            reasoning_message(),
            self._services(tool_message(), calls),
        )

        self.assertTrue(codex_turn_recovery.message_has_tool_use(recovered))
        self.assertEqual(1, len(calls))
        self.assertIn(
            codex_turn_recovery.CODEX_EMPTY_REASONING_CONTINUATION_NUDGE,
            calls[0]["messages"][-1]["content"][0]["text"],
        )

    def test_kimi_reasoning_only_turn_accepts_a_visible_final_answer(self):
        calls = []
        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "kimi",
            {},
            work_request_body(),
            reasoning_message(),
            self._services(text_message("수정과 검증을 완료했습니다."), calls),
        )

        self.assertEqual("수정과 검증을 완료했습니다.", codex_turn_recovery.message_text(recovered))
        self.assertEqual(1, len(calls))

    def test_kimi_reasoning_promise_with_inline_code_retries_once(self):
        calls = []
        original = reasoning_promise_message(
            "17초는 `to_tsvector` 계산 때문입니다. `ILIKE` 인덱스로 바꿔 "
            "실제로 빨라지는지 측정하겠습니다."
        )

        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "kimi",
            {"gateway_retries": 10},
            work_request_body(),
            original,
            self._services(tool_message(), calls),
        )

        self.assertTrue(codex_turn_recovery.message_has_tool_use(recovered))
        self.assertEqual(1, len(calls))

    def test_kimi_promise_recovery_disables_nested_gateway_retries(self):
        captured = []

        def collect(_handler, _provider, pcfg, _body):
            captured.append(pcfg)
            return tool_message()

        codex_turn_recovery.recover_preamble_only_turn(
            None,
            "kimi",
            {"gateway_retries": 10},
            work_request_body(),
            reasoning_promise_message("인덱스를 적용하고 다시 측정하겠습니다."),
            codex_turn_recovery.CodexTurnRecoveryServices(
                should_retry=ciel_runtime.should_retry_preamble_only_turn,
                collect_message=collect,
                log=lambda *_: None,
            ),
        )

        self.assertEqual(0, captured[0]["gateway_retries"])

    def test_promised_followup_match_is_kimi_only(self):
        calls = []
        original = reasoning_promise_message(
            "17초는 순수 `to_tsvector` 계산에서도 발생했습니다. 조인 컬럼마다 "
            "계산하기 때문입니다. trigram 인덱스를 만들고 `ILIKE`로 바꿔 "
            "실제로 빨라지는지 측정하겠습니다."
        )

        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "deepseek",
            {},
            work_request_body(),
            original,
            self._services(tool_message(), calls),
        )

        self.assertEqual(original, recovered)
        self.assertEqual([], calls)

    def test_kimi_projected_reasoning_notice_retries_without_replaying_notice(self):
        calls = []
        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "kimi",
            {},
            work_request_body(),
            reasoning_notice_message(),
            self._services(tool_message(), calls),
        )

        self.assertTrue(codex_turn_recovery.message_has_tool_use(recovered))
        self.assertEqual(1, len(calls))
        replayed = calls[0]["messages"]
        self.assertEqual("user", replayed[-1]["role"])
        self.assertIn(
            codex_turn_recovery.CODEX_EMPTY_REASONING_CONTINUATION_NUDGE,
            replayed[-1]["content"][0]["text"],
        )
        self.assertFalse(
            any(
                "Upstream model returned reasoning" in str(block.get("text") or "")
                for message in replayed
                for block in message.get("content") or []
                if isinstance(message, dict) and isinstance(block, dict)
            )
        )

    def test_reasoning_only_turn_is_not_retried_for_other_providers(self):
        calls = []
        original = reasoning_message()
        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "deepseek",
            {},
            work_request_body(),
            original,
            self._services(tool_message(), calls),
        )

        self.assertEqual(original, recovered)
        self.assertEqual([], calls)

    def test_kimi_truly_empty_turn_is_not_retried(self):
        calls = []
        original = {"role": "assistant", "content": []}
        recovered = codex_turn_recovery.recover_preamble_only_turn(
            None,
            "kimi",
            {},
            work_request_body(),
            original,
            self._services(tool_message(), calls),
        )

        self.assertEqual(original, recovered)
        self.assertEqual([], calls)


class CodexCompatInstructionTests(unittest.TestCase):
    CFG = {"claude_code": {"compat_prompt_for_non_anthropic": True}}

    def test_routed_provider_receives_the_codex_wording(self):
        body = ciel_runtime.body_with_codex_compat_instructions(
            self.CFG, "deepseek", {}, {"instructions": "Base codex instructions."}
        )

        self.assertIn("Base codex instructions.", body["instructions"])
        self.assertIn(ROUTED_CODEX_COMPAT_PROMPT, body["instructions"])
        self.assertNotIn("Claude Code", ROUTED_CODEX_COMPAT_PROMPT)

    def test_applied_when_the_request_carries_no_instructions(self):
        body = ciel_runtime.body_with_codex_compat_instructions(self.CFG, "deepseek", {}, {})

        self.assertEqual(ROUTED_CODEX_COMPAT_PROMPT, body["instructions"])

    def test_appended_only_once_so_the_cached_prefix_stays_stable(self):
        once = ciel_runtime.body_with_codex_compat_instructions(self.CFG, "deepseek", {}, {})
        twice = ciel_runtime.body_with_codex_compat_instructions(self.CFG, "deepseek", {}, once)

        self.assertEqual(once["instructions"], twice["instructions"])
        self.assertIs(once, twice)

    def test_native_codex_backend_is_left_alone(self):
        original = {"instructions": "Base codex instructions."}
        with mock.patch.object(ciel_runtime, "codex_routed_enabled", return_value=True):
            body = ciel_runtime.body_with_codex_compat_instructions(
                self.CFG, "openai", {}, original
            )

        self.assertIs(original, body)

    def test_disabled_by_the_same_switch_as_the_claude_prompt(self):
        original = {"instructions": "Base."}
        body = ciel_runtime.body_with_codex_compat_instructions(
            {"claude_code": {"compat_prompt_for_non_anthropic": False}}, "deepseek", {}, original
        )

        self.assertIs(original, body)

    def test_claude_prompt_is_still_claude_specific(self):
        self.assertIn("Claude Code", ROUTED_COMPAT_PROMPT)


if __name__ == "__main__":
    unittest.main()
