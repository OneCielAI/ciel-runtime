# Change log

## 2026-11-07

- Confirmed idle teammate accumulation, missing projected context after resume, and zero newly completed receipts.
- Replaced inherit-mode teammate processing with one-event native forks; added unconditional cleanup and configurable instructions.
- Restored selected same-provider model choice and a single side-only parent-model retry.
- Passed 441 focused tests and installed local `0.2.51-local.20261107-3638400` without touching the stopped Walkie session.
- Fixed fresh-session claim wait (`ad5d7b6`) and prose-wrapped decision replay (`3a6ea3e`); live E2E on a new session: one fork, one ACK, one answer, clean main transcript.
- Deleted 248 leaked historical side-intake teammate sessions (496 files, 213,612,454 bytes) and their 7 team/task registries.
