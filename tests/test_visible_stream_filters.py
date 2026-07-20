import unittest

from ciel_runtime_support.visible_stream_filters import (
    VisibleThinkingMarkupFilter,
    VisibleToolCallArtifactFilter,
    strip_visible_thinking_markup,
)


class VisibleStreamFilterTests(unittest.TestCase):
    def test_thinking_markup_is_removed_across_chunk_boundaries(self):
        state = VisibleThinkingMarkupFilter()
        output = "".join(
            (
                state.feed("answer <thi"),
                state.feed("nk>private"),
                state.feed("</think> visible"),
                state.finish(),
            )
        )
        self.assertEqual("answer  visible", output)

    def test_unclosed_thinking_markup_does_not_leak(self):
        self.assertEqual("answer ", strip_visible_thinking_markup("answer <thinking>private"))

    def test_tool_call_artifact_suffix_is_held_and_removed(self):
        state = VisibleToolCallArtifactFilter(hold_chars=16)
        output = state.feed("visible response\ncall\nignore") + state.finish()
        self.assertEqual("visible response", output)
        self.assertTrue(state.stripped)


if __name__ == "__main__":
    unittest.main()
