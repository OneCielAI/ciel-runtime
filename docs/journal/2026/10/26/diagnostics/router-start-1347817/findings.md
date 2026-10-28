---
type: Investigation
title: Router did not start on the 10.0.0.238 machine
description: A Codex TUI + app-server launch on the user's machine (account 1347817, web backend 10.0.0.238:9683) failed because the spawned router never answered health within 30 seconds; the router log that would say why is not readable from here.
tags: [router, startup, codex-app-server, diagnostics]
timestamp: 2026-10-26T21:45:00Z
---
# Report

The user pasted a traceback ending in `RuntimeError: ciel-runtime router did not start. See C:\Users\1347817\AppData\Roaming\ciel-runtime\router-instances\9683-a2f58d1342e0\router.log`, raised while launching Codex TUI + app-server with provider cielairouter.

# Confirmed

- The message comes from `router_process_lifecycle.py:253`: the launcher spawns `ciel_runtime.py serve` with its output appended to that `router.log`, then polls router health for 30 s. The router did not answer within that time.
- The traceback's line numbers match the sources of 3c95a4b and f9c3eea, and not 81f6a4b or 2faae14 ([02](evidence/02-traceback-line-match.txt)). This narrows the installed build. It does not prove it.
- From this computer at 21:43Z: 10.0.0.238 answers ping; TCP 9683 does not accept connections, so no router is listening there now. TCP 22 is closed. 445 and 3389 are open ([01](evidence/01-reachability.txt)). Earlier notes record that the router there requires an administrative token, and no SSH path exists.

# Not determined

- Why the router did not start. The reason would be in the router's own output in `router.log` on that machine, which cannot be read from here. No SMB or RDP access was attempted.

# Needed

The last lines of that `router.log` and the installed version from the user's machine.
