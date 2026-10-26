---
type: Diagnostic
title: Emily context limit manual compact boundary
description: Screenshot shows context limit reached without automatic compact; the deployed recovery fixes do not change proactive compact initiation.
tags: [emily, compaction, context-limit]
timestamp: 2026-10-23
---

# Observed report

User screenshot: context857571/872000(98.3%), "Context limit reached · /compact or /clear to continue", mistyped /compo, then manual /compact in progress22seconds. This demonstrates manual compaction started; neither completion nor the cause of missing auto initiation is established by the screenshot.

# Verified source at ed7ab55

- runtime_compaction_binding.py410-432: ordinary requests without existing recovery/native compact return without initiating proactive pressure; native client owns proactive timing.
- provider_request_builder.py148-166: CielAiRouter ordinary preflight opt-out remains; native compact or confirmed upstream overflow can use chunk recovery.
- providers/cielairouter.py175: preflight_prompt_compaction=False.
- dfcf41a..ed7ab55 did not modify any of these three initiation paths.
- Automatic continuation in the new build is post-boundary only; manual compact does not authorize it.

# Remote evidence boundary

Historical Emily path8966, profile aapw-a11u4 on100.64.0.14. Current read-only SSH encoded PowerShell query through ara-ai port1004 returned "exec request failed on channel0"(255). Its running version, native auto-compact configuration and incident logs remain unavailable. No remote changes or live inputs occurred.

# Conclusion

No: missing automatic compact initiation at the displayed limit is not shown to be repaired by ed7ab55. Retry budget, internal summary retries, post-boundary gate release and safe automatic continuation address different phases. Whether this exact screenshot is a native threshold/config problem, failed native compact or upstream rejection requires native transcript/request logs; do not assign one from the visible context percentage alone.

# Auto compact environment followup

Screenshot8 reports CLAUDE_CODE_AUTO_COMPACT_WINDOW is set and takes precedence.
This proves presence/override UI, not its numeric value or autoCompactEnabled.

Current launcher code claude_environment.py495-505 explicitly emits:
MAX_CONTEXT_TOKENS=context_window, AUTO_COMPACT_WINDOW=auto_compact_window,
AUTOCOMPACT_PCT_OVERRIDE=calculated percentage. Pure current-policy probe for
context872000/budget716750 gives window872000,percent82 (nominal715040).
An explicit auto_compact_window715040 instead gives window715040 and no
percentage override, while full context stays872000.

Official model-config documentation states AUTO_COMPACT_WINDOW is the token
window override and outranks /autocompact/flag/settings; status used_percentage
still measures the full context window. Official env-vars documents PCT_OVERRIDE
only for sessions that compact before the model context limit, and
DISABLE_AUTO_COMPACT disables automatic compaction while manual /compact works.
Thus deriving only a percent from a full window is not sufficient evidence
that Emily's actual CLI triggers at715040. Her exact process env/settings are
still unavailable (SSH exec refused; device-key attempt timed out).

Sources: https://code.claude.com/docs/en/model-config#set-the-auto-compact-window
and https://code.claude.com/docs/en/env-vars . No remote settings changed.
