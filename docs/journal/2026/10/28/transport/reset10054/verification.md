---
type: Verification
title: Typed open reset recovery
description: Bounded recovery before response acceptance using the existing transient retry budget.
---
Implemented direct and URLError-wrapped ConnectionResetError recovery only during open_once, using existing deadline/retry/cancellation/reselection path. Accepted responses and read failures are not replayed. No string-based retry matching, no TLS/DNS/broken-pipe broadening. Reset after server acceptance may duplicate provider work; no exactly-once claim. New tests failed before fix and passed after; timeout regression policy updated only for typed reset. 82 transient tests and5 integration tests passed before adding explicit disabled-reset regression; final suite rerun recorded in tool output. No installed runtime replacement or session restart. Branch based on nightly-20261028, not shared dirty checkout; push is isolated nightly-20261028-reset10054.
