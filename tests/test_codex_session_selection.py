import unittest
from pathlib import Path

from ciel_runtime_support.codex_session_selection import (
    CodexSessionPresentationPorts,
    CodexSessionRepositoryPorts,
    CodexSessionSelectionService,
)


class CodexSessionSelectionServiceTests(unittest.TestCase):
    def service(self, sessions, selected=0, outputs=None):
        outputs = outputs if outputs is not None else []
        return CodexSessionSelectionService(
            repository=CodexSessionRepositoryPorts(
                sqlite_home=lambda *_args, **_kwargs: Path("C:/codex"),
                resumable=lambda _database, _limit, _include: sessions,
            ),
            presentation=CodexSessionPresentationPorts(
                select=lambda *_args, **_kwargs: selected,
                compact_text=lambda text, limit: text[:limit],
                output=outputs.append,
            ),
        )

    def test_repository_query_uses_codex_state_database(self):
        calls = []
        service = CodexSessionSelectionService(
            repository=CodexSessionRepositoryPorts(
                sqlite_home=lambda *_args, **_kwargs: Path("C:/codex"),
                resumable=lambda database, limit, include: calls.append(
                    (database, limit, include)
                )
                or [],
            ),
            presentation=CodexSessionPresentationPorts(
                select=lambda *_args, **_kwargs: None,
                compact_text=lambda text, _limit: text,
                output=lambda _message: None,
            ),
        )

        service.local_resume_sessions(limit=25, include_non_interactive=True)

        self.assertEqual([(Path("C:/codex/state_5.sqlite"), 25, True)], calls)

    def test_selection_returns_the_selected_session_id(self):
        service = self.service(
            [
                {
                    "id": "session-1",
                    "title": "First task",
                    "cwd": "C:/work/repo",
                    "model_provider": "ciel-runtime",
                    "activity_ms": 0,
                }
            ]
        )

        self.assertEqual("session-1", service.select_resume_session())

    def test_empty_repository_reports_the_database(self):
        outputs = []
        service = self.service([], outputs=outputs)

        self.assertIsNone(service.select_resume_session())
        self.assertEqual(1, len(outputs))
        self.assertIn("state_5.sqlite", outputs[0])


if __name__ == "__main__":
    unittest.main()
