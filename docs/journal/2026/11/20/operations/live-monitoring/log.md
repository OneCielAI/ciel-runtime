# Change log

## 2026-11-20

- 12:15Z tick:
  - Andy relaunched 12:12Z (c955630); 3 running, 1 reserved, 46 waiting, 22 submitted. The launch sweep orphaned 4 leases (3 P1 running, 1 P3 reserved).
  - Celly: 4 running.
    - New: 4 × `router_post_uncaught ChannelFileLockBusy` at 12:03:16Z–12:08:31Z. One was on `work-plan.json.lock` (work session 909aa6ee…), three on side-inbox event locks.
    - A `work_parallel_run` was refused at 12:10:13Z with `workspace_prepare_retained ... channel file lock acquisition busy`.
    - Confirmed: `msvcrt.locking(LK_LOCK)` gives up after 10 one-second attempts, so some holder kept these locks for more than 10 s.
    - Not confirmed: which code path held them.
  - KOBE: unchanged; n878 and n880 are still without reviews.
- 12:14Z: user question "어떻게 앤디 세션이 워크모드가 저렇게 그냥 멈춰버릴수있지?". Read-only check of Andy:
  - Main transcript: 11:56:22Z `ciel-work: ciel-work was unloaded: it crashed the hooks worker`, then 12:04:44Z `Unknown command: /work-mode`. With the mod unloaded, none of its hooks run: no pump, no native observation, no review wake, no fork gate.
  - Same notice earlier: Andy 04:08:09Z; Celly 11-19 08:59:03Z and 11-20 06:42:51Z; KOBE 11-18 12:34:33Z, 17:26:43Z and 17:43:06Z.
  - Andy's ciel-runtime-router MCP log: 515–685 tool calls per minute 11:53–11:55Z, of which 461–621 were `work_host_page`. The previous Andy process logged 288–358 per minute before its 10:13Z exit. Celly's current session: 64–119 per minute.
  - Andy's 11:52:34Z notice `Parallel read failed: EADDRINUSE` came 4 minutes before the unload.
  - Router timeline (local -6):
    - 06:11:49 `router_lifetime_stopped reason=claude_exit`.
    - Andy relaunched at 12:12:13Z under the same conversation. The router restarted at 06:12:15 and 06:12:23 local, and the launch orphan sweep released 4 leases.
    - The `/health` miss was during that relaunch. At 12:13:56Z the router answered with c955630, pid 99752.
  - No Windows Application Error or WER event 1000/1001 for claude.exe between 11:55 and 11:58Z.
  - Not confirmed: what the hooks worker died of. The engine's notice carries no reason, and no other log was found. The `work_host_page` volume is the measured anomaly, and the claim that it caused the crash is unproven.
- 11:55Z tick:
  - Andy: unchanged lease counts.
    - New notice at 11:52:34Z: "Work reconciliation: Parallel read failed: EADDRINUSE: Unable to connect".
    - The router log shows no error then.
    - The router answered 500–700 `/ca/mcp` POSTs per minute (05:48–05:55 local), mostly `work_host_page`. Ephemeral port exhaustion on the client side is a hypothesis, not confirmed.
  - Celly: 3 running, no findings.
  - KOBE: n878 and n880 are still without reviews, waiting for the vanished-fork fix. KOBE has no `work-parallel-stranded-v1.json` yet, which is expected: the store is created on first write.
- 11:40Z tick:
  - Andy: 46 waiting, 22 submitted, 3 running, 1 reserved; n1856 still blocked.
    - New: `router_post_uncaught PermissionError [WinError 5]` at 11:38:32Z, raised by `os.replace` of `work-parallel-stranded-v1.json` in `_stranded_roll_forward`. That call was writing `stage_failed` after `prepare` raised `workspace preparation/removal incomplete; retained`.
    - The stranded and finalization stores have no `PermissionError` retry around `os.replace`; the ledger, native, P0 and receipts stores retry 20 times.
  - Celly: restarted again (scope b86a350f); 2 running, 1 waiting; no findings.
  - KOBE: n878 is still without a review, and n880 (lease edf577f8) has the same shape: native `failed` 11:19:59Z, receipt 11:27:35Z, listed idle 11:28:39Z, no review. Covered by the vanished-fork fix under test.
- 11:25Z: First passes of the read-only monitor over Andy, Celly and KOBE. Confirmed a vanished-fork review gap on Celly (n1070, n1135; published only by Celly's 11:14Z restart) and KOBE (n878, open). Periodic monitoring scheduled.
