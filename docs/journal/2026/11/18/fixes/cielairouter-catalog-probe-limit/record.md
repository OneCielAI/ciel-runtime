---
type: fix
title: CielAiRouter launch blocked as "Base URL unreachable (ValueError)" — the catalog outgrew the 1 MiB probe cap
description: The launch readiness probe read the CielAiRouter model catalog with a 1 MiB cap and treated a larger body as a failure. The catalog is now 1,292,256 bytes (2,319 models), so every Claude launch with cielairouter was blocked (reported on the KOBE sandbox, latest beta). The cap is now 16 MiB and an oversized body names the cause. Reproduced and verified against the real gateway and the real launch gate.
timestamp: 2026-11-18T08:10:00Z
tags: [cielairouter, launch, readiness, provider-status, hotfix, kobe]
---

# Report (user, 2026-11-18)

> 14 kobe 샌드박스에서.. cielairouter로 실행하려고할때 ,Claude로 실행하면, launche blocked  base url unreachable (value error)에러가남 최신버전.. 긴급 패치필요..

In English: on the KOBE sandbox, launching Claude with cielairouter fails with "Launch blocked: Base URL unreachable (ValueError)", on the latest version. An urgent patch is needed.

# Cause (measured)

- The CielAiRouter catalog GET (`/v1/models`, authenticated) returned **1,292,256 bytes, 2,319 models**, in 13.3 s (this machine, 2026-11-18).
- `provider_status._generic_status` read the catalog with `read(1048577)` for providers with a retry policy (CielAiRouter only). A body above 1 MiB raised `ValueError("model catalog exceeds probe limit")`. That was reported as `Base URL: unreachable (ValueError)`, and `provider_readiness.launch_readiness_errors` blocks any status containing "unreachable".
- The cap came from 2ccfbaf (2026-10-29), when the catalog was below 1 MiB. Nothing machine-specific: every machine on that gateway is affected once the catalog passes 1 MiB.

Reproduced with the installed beta `0.2.51-beta.20261118-064156.66da4e2`, an isolated config holding only this machine's cielairouter provider entry, and the real gateway:

| Tree | `base_url_status_line` | `launch_readiness_errors` |
|---|---|---|
| installed 66da4e2 | `Base URL: unreachable (ValueError)` | `Launch blocked: Base URL: unreachable (ValueError)`, `Set a reachable Base URL before launching Claude Code.` |
| patched | `Base URL: model list reachable (/v1/models?prefix=alias&configuredOnly=true&availableOnly=true, 1917 models)` | none (launch allowed) |

The patched status counts 1,917 models. The launch probe uses the filtered path (`configuredOnly`, `availableOnly`), which lists fewer models than the plain GET above (2,319); both are over 1 MiB.

KOBE itself was not touched; the same code path and gateway reproduce the report here.

# Fix

`ciel_runtime_support/provider_status.py`:
- `CATALOG_PROBE_MAX_BYTES = 16 MiB` replaces the 1 MiB cap for the full-catalog probe. Providers without a retry policy keep the 128 KiB preview read.
- An oversized body raises `CatalogTooLarge` and reports `Base URL: model list unreachable (<path>; model catalog exceeds the 16 MiB probe limit)`, so the cause is visible instead of a bare `ValueError`.

# Verification

| Test | Before (66da4e2) | After |
|---|---|---|
| `test_a_catalog_above_one_mib_is_reachable` (2,319 models, >1 MiB) | FAIL: `Base URL: unreachable (ValueError)` | pass |
| `test_large_catalog_is_fully_read` (reads up to the cap) | FAIL (old cap) | pass |
| `test_oversized_catalog_fails_closed_without_retry` (cap + 1 byte, named cause) | FAIL (bare ValueError) | pass |
| `test_provider_status_retry` + `test_cielairouter_provider` | — | 38 run, 0 failures |

Evidence: [evidence/tests-before.txt](evidence/tests-before.txt), [evidence/tests-after.txt](evidence/tests-after.txt), [evidence/real-gateway.txt](evidence/real-gateway.txt).
