# Directory Update Log

## 2026-10-24
* **Update**: Ported to the release branch with the plan-mode fix.
* **Correction**: The observed ciel-code failures were dropped ExitPlanMode calls, not empty upstream output; this retry does not cover them. Linked the plan-exit-drop finding.
* **Creation**: Recorded the bounded semantic retry for empty and reasoning-only Ollama turns, its option controls, tests and real Claude CLI E2E evidence. Not committed or deployed.
