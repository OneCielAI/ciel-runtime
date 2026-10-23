---
type: Diagnostic
title: Celly stops work after compaction
description: User requests continuation of unfinished work after a successful compact; mechanism under investigation.
tags: [claude, compaction, continuation]
timestamp: 2026-10-22
---

# Scope

Distinguish successful automatic mid-turn compaction from manually invoked idle compact, error/cancel and intentional completion. Reuse native continuation semantics; do not blindly inject a new turn on every PostCompact.

# Current observation

Router logs include end_turn at19:35:03 before native_compact_started19:35:08, transcript summary19:38:09, then next input around19:57. This ordering alone does not prove a mid-turn continuation was lost. Exact transcript boundary and native hook payload inspection in progress. No live session input or queue changed.
