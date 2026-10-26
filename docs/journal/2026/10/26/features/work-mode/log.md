# Work mode update log

## 2026-10-26
- **Release**: Published as 0.2.51-nightly.20261026-162537.2faae14 (CI 40357359092, Publish 40357359093) after a failed first attempt; installed into a new local snapshot and verified with the real CLI (navigation run-b0ef1f45). See [release record](../../releases/work-mode-nightly/release.md).
- **Fix**: Release check found an import cycle (work_mode_launch -> work_mcp_tools -> work_mode_launch) reported by the architecture contract test. WORK_TOOL_NAMES now lives in work_mode_launch and work_mcp_tools re-exports it; 458 focused tests, ruff and 18 mod tests pass.
- **Evaluation**: One-minute amendment rerun run-4997239b: 9 amendments + FINAL each received once with full text at 60 s intervals, A/B/C plan branches kept separate, re-authored spec-only verifier 38/38. Earlier run-2e0993a3 scored 37/38 with one ambiguous B3 assertion. Live router PIDs unchanged. See [minute evaluation](minute-evaluation.md).
- **Correction**: User reported cross-session /work data. Located the installed global command and external ciel-code global board implementation, without changing that project or its data.
- **Fix**: Experimental runtime Work stores now require runtime-qualified conversation identities; Claude mod supplies $.session.id() on every call and overrides model-supplied identities.
- **Verification**: 118 focused Python tests and 13 mod tests passed; Codex native MCP metadata was captured; isolated real app-server calls separated two conversation stores and rejected missing identity.
- **Docs**: docs/Configuration.md gained the experimental Work mode section (defaults, native exclusion, /work-mode vs external /work, Claude-enforced vs Codex model-mediated approval, session separation).
- **Packaging**: tests/test_work_mode_packaging.py, 4 passed via the isolated focused runner; npm pack --dry-run in a scrubbed temp environment lists the mod manifest, hooks.json and register.js (506 entries).
- **Runtime**: Actual Claude 2.1.310 planning Edit/Bash denial, auto execute/edit, evidence progress and exact-conversation resume passed. Interactive ask denial/approval and footer captured in five PNGs.
- **Deployment**: New independent local snapshot verified using its own code and selected as User CIEL_RUNTIME_HOME. No live router/session restart.
- **Hardening**: Approval now rejects conversation changes even with identical plan counters; text approval reads the full bounded plan rather than an 80-line truncation.
- **Evaluation**: Real three-problem/multiple-in-flight-amendment run-f5587ac4 received every request once and passed 23 independent tests; full requirement texts and all verified plan leaves retained.
- **Display**: Work status now includes branch progress and current hierarchical step. 48 Python + 15 mod tests and actual CLI screenshot proof passed; local snapshot and generated status script selected.
