# Work mode hints log

## 2026-10-26
- **Queue**: A new independent request now queues behind the unfinished active goal; finishing the active goal activates the oldest queued one. Real CLI run-6a0d4cec verified it after a live reproduction of the old behavior on router 9524.
- **Triage guidance**: Claude context, Codex developer instructions and Work tool descriptions say: independent request = numbered root item, dependent request = child of the related item.
- **Hints**: Session-start transcript notice, empty-plan line, planning toast and readable deny text, `/work-mode help` and readable bare `/work-mode`, Codex launch console line.
