# Wing Dave Runtime recovery

## Task

- Diagnose and repair repeated `exec_command` execution in Dave's routed Codex session.
- Prevent Ciel Runtime's Windows ConPTY path from breaking Korean input on Wing.

## Evidence

- Wing package: `0.2.21-nightly.20260820-145530.57956d9`.
- Router logs showed the same two `exec_command` calls repeating about every four seconds.
- Historical tool-use/tool-result counts increased from 36 to 60.
- The default completed-execution suffix policy did not contain Codex's actual `exec_command` tool name.
- `WindowsConPtySession._configure_parent_console()` switches the parent input and output code pages to UTF-8 and restores them only when the session closes.

## Changes

- Added `exec_command` to the default repeated-execution suffix policy.
- Added a regression test using two successful Codex `exec_command` results.
- Replaced the ConPTY parent input path with `ReadConsoleW` and explicit
  UTF-16-to-UTF-8 conversion.
- Removed all parent-console `SetConsoleCP` and `SetConsoleOutputCP` mutations.
- Kept the operator's original input/output code pages unchanged.
- Removed the temporary Wing user-environment and npm-wrapper ConPTY overrides
  after deploying the package-level fix.

## Verification

- Full `npm test` passed:
  - Unit group: 1,079 tests (44 skipped).
  - Router group: 902 tests.
  - Channel group: 369 tests (80 skipped).
  - Runtime group: 241 tests (12 skipped).
- `npm run lint`, `npm run check:docs`, and Python `compileall` passed.
- The first Linux publish run exposed that `ctypes.c_wchar` is 32-bit on that
  host. The ConPTY input buffer now uses fixed-width 16-bit UTF-16 code units;
  the regression test therefore exercises the same representation on Windows
  and Linux runners.
- The fixed-width input regression suite passed on Windows (12 tests) and WSL
  Ubuntu 26.04 (12 tests, one Windows-only ConPTY test skipped).
- Local regression tests passed:
  - `test_tool_side_effect_dedupe.py`: 9 tests.
  - `test_ollama_provider_options.py`: 49 tests.
  - `test_windows_conpty.py`: 12 tests.
  - Channel terminal tests: 11 tests.
  - Architecture contracts: 261 tests (42 skipped).
- Wing installed module compiled successfully and reported `exec_command_guard=PASS`.
- Wing stored `CIEL_RUNTIME_WINDOWS_CONPTY=0` and reported `conpty_override=PASS`.
- A real Wing launch using `ciel-runtime --ca-runtime codex -- --version` returned
  `codex-cli 0.148.0` with exit code 0.
- The final Wing package contains zero `SetConsoleCP(` calls and one
  `ReadConsoleW(` call.
- The temporary host overrides were removed: wrapper override count 0 and user
  override unset.
- A real Wing ConPTY launch returned `codex-cli 0.148.0` with exit code 0.
- The Wing router log recorded `transport=conpty` and `bracketed_paste=True`.
- In the same SSH console, input and output code pages were unchanged across the
  routed launch: input `437 -> 437`, output `437 -> 437`.
- An interactive Wing Codex launch rendered the injected Korean validation text
  `한글입력검증-가나다라마바사` exactly in the prompt before clean shutdown.
- Local and Wing installed module SHA-256:
  `EA6ED7C6DBC6BE063DF6754C42F5E324C6CA773B5B357F4A62EE4A121E3F419C`.
- Local and Wing `windows_conpty.py` SHA-256:
  `A79E36329B5746986C2CD426C6DC495E71B87CA35557ACA4F948EF68BBD22AE7`.
