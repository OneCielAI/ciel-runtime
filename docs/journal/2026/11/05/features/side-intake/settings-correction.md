---
type: Implementation
title: Separate Work and side intake configuration
description: First-screen independent settings, explicit SSE lanes and honest mod receipt visibility.
tags: [settings, side-intake, sse]
timestamp: 2026-11-05
status: released-with-explicit-boundaries
---

# Contract

User requests independent first environment-screen Work and btw_with_mcp entries, explicit session/btw_with_mcp injection selection, and visible mod handling. Primary prelaunch menu now has separate panels; legacy POSIX menu also wired. Settings do not enable Work when side intake toggles. Default feature remains disabled, default selected type btw_with_mcp. Explicit data.injection_type or submit_external_event injection_type overrides configured lane; disabled retains ordinary original injection.

# Honest visibility

Automatic busy SSE path still commits Runtime WorkStore directly (native socket priority next cannot execute immediate command while busy). It is not misrepresented as mod command execution. New Work status projection returns bounded receipt summaries; opt-in busy mod poll logs `btw_with_mcp · mod observed runtime queue → item · planning only · recap`. Typed immediate command remains actual MCP intake. Owned native probe .local/side-busy-f489334c verified busy queue+mod displayed receipt, one main model call/no side text, duplicate does not create second node. First probe failed byte-substring assertion because terminal VT split phrase across cursor movements; inspected actual frame contained receipt, normalized matcher passed.

# Verification status

70 focused tests pass, including independent panel keys, retained lane on toggles, explicit session override, disabled fallback and one mod receipt display. Actual primary prelaunch UI operated in isolated owned session `.local/separate-menu-b3298b14`: first screen has Work21/side22 independent entries and side panel displays selected type plus disabled toggle. Actual VT replay screenshot side.png inspected. Busy mod receipt screenshot `.local/side-busy-f489334c/receipt.png` inspected. Work controls removed from LLM option panel;79 focused tests pass after moving old row tests to dedicated panel. Independent side operation while Work OFF remains unverified. Busy receipt observer now gated by side_intake_enabled to avoid periodic reads for ordinary Work-only sessions. Release003f60f CI42087875648+Publish42087875751 success after fixing four stale menu-position/retention assertions from failed7c85236 release. Exact npm190045.003f60f globally installed; new immutable snapshot+User pin synchronized,76 installed tests1skip pass. Installed primary menu `.local/separate-menu-99a76554` verified. Installed busy receipt `.local/side-busy-a52da81f/receipt.png` visually confirms mod receipt while main streams; raw ANSI substring assertion failed, so do not claim that assertion passed. Runtime admission remains direct queue, mod observes receipt rather than executes automatic intake. User requirement for genuine mod-owned automatic intake is not fully satisfied by receipt observation. Passive Stop correction5b6bea7 is independently published and installed180737; do not conflate release states.
