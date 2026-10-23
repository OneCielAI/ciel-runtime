---
type: FixVerification
title: Native custom tool response contract
description: Fix only reproduced runtime response-restoration defects, without attributing the original gateway response.
tags: [responses, custom-tools, codex]
timestamp: 2026-10-22
---

# Scope

Independent inbound custom restoration, original namespace-aware declarations and optional-space SSE parsing. No global provider setting changes or remote deployment. Preserve genuine functions and already-native custom calls.

# Evidence boundary

Ara rollout establishes incompatible function_call exec reached Codex, not which layer first produced it. Helper reproductions establish local gaps. Actual Ara checkpoint/tool success remains unverified after release.

# Baseline verification

Existing exact-file suites test_meta_provider, test_responses_input_compatibility, and test_provider_responses_completion_gate: 88 tests OK (evidence/baseline-regressions.txt).

# Non-goals

Do not redesign outbound namespace hoisting or unrelated translated Anthropic name precedence in this patch. Do not guess ambiguous tool identities or enable a global provider conversion setting.

# Status

Focused validation: 177 tests across 10 Responses/provider files passed. The new contract suite covers namespace, additional_tools, exact-function precedence, ambiguity, native custom preservation, SSE optional-space/CRLF/event fallback, all byte-split positions sampled every13 bytes, JSON and buffered stream delivery, no conversion flag, and remote-bridge exclusion. Ruff, compile and diff checks pass.

One initial nonstream byte-preservation assertion was corrected in the product: when projection produces no change, the original JSON bytes are now forwarded unchanged.

Implementation ready for commit/release; Celly post-compact continuation remains a separate confirmed behavior investigation.
