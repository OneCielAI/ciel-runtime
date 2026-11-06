---
type: diagnostic
title: Startup subprocess CP949 decoder failure
description: User screenshot shows a Python reader thread failing to decode child output after the Claude update check.
tags: [startup, windows, encoding, subprocess]
timestamp: 2026-11-05
---

# Evidence

Screenshot18: Python313 subprocess.py readerthread buffer.append(fh.read()) raises UnicodeDecodeError cp949 for byte0xe2 at position1556 after Claude2.1.318 update status. This proves a text-mode child output decoder failed; screenshot alone does not identify which launch command emitted those bytes.

Owned child emits bytes `ready e2 9c 93`. Decoding as cp949 reproduces UnicodeDecodeError on0xe2. Explicit utf8 decoding returns `ready checkmark`. tests/test_npm_runtime.py adds a real subprocess test under patched cp949 default, UTF8 multibyte output and malformed byteff; explicit UTF8/errors=replace preserves valid text and substitutes malformed bytes rather than crashing the reader thread.

# Correction scope

npm_runtime.py subprocess captures (version lookup, npm root, executable version, upgrade output) now specify encoding=utf-8/errors=replace. package_lifecycle.py self-update capture uses the same explicit policy. These are confirmed vulnerable source calls with text=True and previously no encoding; exact screenshot invocation remains uncorrelated. This does not claim all subprocess calls throughout the product have been audited or that the actual startup screenshot was reproduced end-to-end.

# Additional reproduced startup path, 2026-11-06

Source `compatibility_runtime.py` ClaudeCliCapabilityProbe captures `claude --help` using text mode. An owned help-output fixture under actual Python3.13.5with patched cp949default reproduced the same reader-thread exception class on0xe2(position36), returned no valid help result and falsely detected unsupported permission mode. Explicit UTF8/errorsreplace corrected that exact owned path: permission-mode detectiontrue, reader-errors empty. The added real child test also includes malformed0xff. Before/after evidence is retained in `docs/journal/2026/11/06/features/side-decision/evidence/startup-capability-cp949*.json`. This identifies another confirmed startup vulnerability, not the exact screenshot command.

# Verification

Focused npm9tests pass, including a fresh2026-11-06run under actual Python3.13matching the screenshot interpreter family with patched cp949default and a real multibyte/malformed-output child. Package lifecycle8, runtime upgrade4 and headless update19tests pass after channel-pinning expectation updates. Full Ruff passes. Actual existing user session was not restarted. Full launch validation and screenshot evidence for the installed correction are not yet available; no release performed.
