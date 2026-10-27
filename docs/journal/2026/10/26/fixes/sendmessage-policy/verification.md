---
type: Verification
title: Routed SendMessage policy
description: Verified narrow SendMessage exposure and provider-menu blocklist controls.
tags: [tools, providers, messaging]
timestamp: 2026-10-27T05:00:00Z
---
# Scope

Only SendMessage is removed from default non-Anthropic blocks. Legacy SendMessageTool, team lifecycle and unverified server-side tools stay blocked. Explicit blocked_tools overrides retain priority. The routed blocklist is configurable in the existing provider options menu.

# Evidence

See verification.okf and evidence/verification.json for exact verdicts and native source digests. Real Claude 2.1.310 background-child message and ACK passed on owned CielAiRouter Messages and vLLM Chat loopback routes, default policy after the change. This is native local routing proof, not remote model reliability.

# UI

[Blocklist menu](evidence/menu/tool-blocklist.png), [saved setting](evidence/menu/tool-blocklist-saved.png). Native permission policy is not overridden and no live config was changed.
