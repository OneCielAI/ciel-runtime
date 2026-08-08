"""Fitting a Responses input array inside the target model's context budget."""

import json
import unittest

from ciel_runtime_support.prompt_compaction import (
    PromptCompactionRuntime,
    PromptCompactionServices,
    PromptCompactionText,
    compact_responses_input_for_budget,
    responses_item_as_message,
    responses_tail_is_safe,
)


def content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
    return "" if content is None else str(content)


def estimate_tokens(value):
    return len(json.dumps(value, ensure_ascii=False)) // 4


def services(log=None):
    return PromptCompactionServices(
        text=PromptCompactionText(
            content_to_text=content_to_text,
            compact_text=lambda text: text[:200],
            build_summary=lambda omitted, budget: f"[guard: {len(omitted)} omitted]",
            append_system_texts=lambda system, texts: system,
            truncate=lambda text, limit: text[:limit],
            chunk_count=lambda omitted, budget: 1 if omitted else 0,
        ),
        runtime=PromptCompactionRuntime(
            estimate_tokens=estimate_tokens,
            llm_compact_messages=lambda *a, **k: None,
            write_activity=lambda *a, **k: None,
            log=(log if log is not None else (lambda level, message: None)),
        ),
    )


def turn(index, output_chars):
    """One assistant tool call plus its result, as Responses records them."""

    return [
        {"type": "message", "id": f"msg_{index}", "role": "user",
         "content": [{"type": "input_text", "text": f"question {index}"}]},
        {"type": "function_call", "id": f"fc_{index}", "call_id": f"call_{index}",
         "name": "shell", "arguments": '{"cmd":"ls"}'},
        {"type": "function_call_output", "id": f"fco_{index}", "call_id": f"call_{index}",
         "output": "x" * output_chars},
    ]


class ResponsesTailSafetyTests(unittest.TestCase):
    def test_a_tail_may_not_begin_on_an_orphan_tool_output(self):
        items = turn(1, 10)

        self.assertTrue(responses_tail_is_safe(items, 0))
        self.assertTrue(responses_tail_is_safe(items, 1))
        self.assertFalse(responses_tail_is_safe(items, 2))

    def test_items_project_onto_the_shared_summary_shape(self):
        message, call, output = turn(7, 5)

        self.assertEqual("user", responses_item_as_message(message)["role"])
        self.assertEqual("assistant", responses_item_as_message(call)["role"])
        self.assertIn("shell", responses_item_as_message(call)["content"])
        self.assertEqual("tool", responses_item_as_message(output)["role"])


class CompactResponsesInputTests(unittest.TestCase):
    def test_a_payload_within_budget_is_returned_untouched(self):
        body = {"model": "gpt-5.6-sol", "input": turn(1, 10)}

        self.assertIs(body, compact_responses_input_for_budget(body, 100_000, services=services()))

    def test_an_oversized_history_is_brought_under_budget(self):
        # Shaped like the captured session: a few enormous tool outputs carrying
        # most of the transcript, which is what one-item-at-a-time trimming
        # cannot converge on.
        items = [block for index in range(40) for block in turn(index, 20_000)]
        body = {"model": "gpt-5.6-sol", "input": items}
        budget = 20_000

        compacted = compact_responses_input_for_budget(body, budget, services=services())

        self.assertLessEqual(estimate_tokens(compacted), budget)
        self.assertLess(len(compacted["input"]), len(items))
        self.assertEqual(40 * 3, len(body["input"]))

    def test_the_omitted_history_is_replaced_by_one_summary_item(self):
        items = [block for index in range(30) for block in turn(index, 20_000)]
        body = {"model": "gpt-5.6-sol", "input": items}

        compacted = compact_responses_input_for_budget(body, 20_000, services=services())

        head = compacted["input"][0]
        self.assertEqual("message", head["type"])
        self.assertEqual("user", head["role"])
        self.assertIn("guard", head["content"][0]["text"])
        self.assertEqual("input_text", head["content"][0]["type"])

    def test_every_retained_output_keeps_the_call_it_answers(self):
        items = [block for index in range(30) for block in turn(index, 20_000)]

        compacted = compact_responses_input_for_budget(
            {"model": "gpt-5.6-sol", "input": items}, 20_000, services=services()
        )

        kept = compacted["input"][1:]
        calls = {i["call_id"] for i in kept if i.get("type") == "function_call"}
        outputs = [i for i in kept if i.get("type") == "function_call_output"]
        self.assertTrue(outputs or not kept)
        for output in outputs:
            self.assertIn(output["call_id"], calls)

    def test_compaction_is_reported_at_warning_level(self):
        logs = []
        items = [block for index in range(30) for block in turn(index, 20_000)]

        compact_responses_input_for_budget(
            {"model": "gpt-5.6-sol", "input": items},
            20_000,
            provider="codex",
            model="gpt-5.6-sol",
            services=services(lambda level, message: logs.append((level, message))),
        )

        self.assertTrue(any(level == "WARN" and "compacted responses payload" in message for level, message in logs))

    def test_a_single_oversized_item_still_produces_valid_input(self):
        body = {"model": "gpt-5.6-sol", "input": turn(1, 400_000)}

        compacted = compact_responses_input_for_budget(body, 20_000, services=services())

        self.assertTrue(compacted["input"])
        self.assertEqual("message", compacted["input"][0]["type"])


if __name__ == "__main__":
    unittest.main()
