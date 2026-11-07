---
type: fix
title: Localized Work and SSE setting feedback
description: Work and SSE menus explain highlighted and applied values in English, Korean, Japanese and Chinese.
tags: [work, sse, settings, localization]
timestamp: 2026-11-07
---

# Problem

The Work and SSE setting descriptions were English/Korean only. Japanese and Chinese fell back to English, highlighted choices showed only static setting help, and raw apply feedback was overwritten before a render. `Work plan approval` also looked like an ordinary boolean even though off means automatic approval, not no approval.

# Fix

- Every Work and SSE setting has en/ko/ja/zh text.
- Choice navigation adds the highlighted value to the explanation.
- Applied feedback is localized and preserved for one parent-panel render.
- Work plan approval presents `ask user` and `auto`: ask/on/true means manual approval; auto/off/false means automatic approval.
- The side model description states that an unavailable selected model injects the original SSE event into the main session.
- CLI and prelaunch reuse the same descriptions while storage and JSON values remain machine-readable.

# Verification

Focused setting and side lifecycle tests passed. Real prelaunch screenshots and installed beta evidence will be appended after the beta artifact is installed.
