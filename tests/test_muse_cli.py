"""Muse Code argv translation: Claude-style session flags to `muse resume`."""

from __future__ import annotations

import unittest

from ciel_runtime_support.muse_cli import (
    muse_passthrough_has_command,
    muse_passthrough_mapping,
)


class MusePassthroughMappingTests(unittest.TestCase):
    def test_continue_becomes_resume_last(self):
        argv, notes = muse_passthrough_mapping(["--yolo", "--continue"])

        self.assertEqual(["--yolo", "resume", "--last"], argv)
        self.assertEqual(["--continue -> resume --last"], notes)

    def test_bare_c_is_continue_but_key_value_is_not(self):
        mapped, notes = muse_passthrough_mapping(["-c"])
        kept, kept_notes = muse_passthrough_mapping(["-c", "features.js_repl=false"])

        self.assertEqual(["resume", "--last"], mapped)
        self.assertEqual(["-c", "features.js_repl=false"], kept)
        self.assertEqual([], kept_notes)
        self.assertEqual(["-c -> resume --last"], notes)

    def test_resume_carries_its_session_reference(self):
        argv, notes = muse_passthrough_mapping(
            ["--resume", "01a0b8-2", "--model", "muse-spark-1.3"]
        )

        self.assertEqual(["resume", "01a0b8-2", "--model", "muse-spark-1.3"], argv)
        self.assertEqual(["--resume <session> -> resume <session>"], notes)

    def test_session_id_maps_to_the_resume_command(self):
        argv, _notes = muse_passthrough_mapping(["--session-id", "abc"])

        self.assertEqual(["resume", "abc"], argv)

    def test_existing_subcommand_wins_and_the_flag_is_dropped(self):
        argv, notes = muse_passthrough_mapping(["--continue", "exec", "say hi"])

        self.assertEqual(["exec", "say hi"], argv)
        self.assertEqual([], notes)
        self.assertTrue(muse_passthrough_has_command(["exec", "say hi"]))
        self.assertFalse(muse_passthrough_has_command(["--yolo", "say hi"]))

    def test_unrelated_arguments_pass_through_untouched(self):
        argv = ["--yolo", "--model", "muse-spark-1.3", "fix the tests"]

        self.assertEqual(argv, muse_passthrough_mapping(argv)[0])

    def test_only_the_first_session_flag_maps(self):
        argv, notes = muse_passthrough_mapping(["--continue", "--resume", "01a0b8"])

        self.assertEqual(["resume", "--last"], argv)
        self.assertEqual(["--continue -> resume --last"], notes)


if __name__ == "__main__":
    unittest.main()
