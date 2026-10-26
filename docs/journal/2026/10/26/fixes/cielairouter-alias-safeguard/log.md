# Fix log

## 2026-10-26
- Implemented router-observed native model names for the CielAiRouter safeguard; tests, ruff and real CLI runs against the worktree and the 3c95a4b sources recorded. Removed the debug-log `latest` symlinks Claude Code creates in run directories (they point to absolute local paths), here and in the Walkie E2E runs committed in 3c95a4b.
