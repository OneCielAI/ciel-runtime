---
type: Verification
title: Side receipt stream verification
description: What the owned browser probe checks and, once run, its recorded results and screenshots.
timestamp: 2026-11-10T22:00:00Z
tags: [work, web, side-intake, receipts, verification, playwright]
---

# Status

Passed in the owned, isolated source-tree browser run. The API uses a top-level `receipts` array, top-level `next_cursor`, and `q=` search. The first integration run exposed the backend's older nested response; after aligning the controller to the approved contract, the final run passed every assertion.

# What the probe checks

| Check | How |
|---|---|
| Every receipt state is shown | Seeded through the real inbox functions (`receive`, `claim`, `finish`, `fail`, `prepare`, `record`): received, P1 received, processing, processed (sent / route_unavailable / failed), duplicate source, quarantined. A repeated `receive` of one event key must not add a row. |
| More than one page | Older processed fillers are seeded first, so the first page ends before them. |
| Live arrival without a click | After the Side tab is open, a new event is received and a processing receipt finishes from Python. Each must appear in the page with no click. The `side.changed` frames are captured in the page and by a separate raw SSE reader. |
| Detail redaction | One receipt's route holds a reply token, authorization header and token. The detail view, the whole page DOM and the list/detail JSON must not contain them; the inbox file still must. |
| Load older | The oldest filler is absent until "Load older" is used; afterwards it is present and no receipt id repeats. |
| Filter and search | Stage filters isolate quarantined and duplicate rows; P1 scope isolates the two seeded P1 rows; searches by current text, older-page text and receipt ID each show only matching receipts. |
| Draft preservation | Text typed into a handoff answer and the search box survives a live re-render. |
| Mobile 390 px | No horizontal overflow of the page or of any Side panel element; screenshot. |
| Readback | Final inbox stages and delivery states are read from the files, not the page. |

# Results

- Result: [e2e-side-receipts-result.json](evidence/e2e-side-receipts-result.json), all checks passed.
- First page: 25 unique newest receipts; every derived state and delivery result rendered.
- Live behavior: a new receipt appeared without a click in 1.032 s; an in-flight receipt changed to processed in 0.141 s; three `side.changed` frames were observed.
- Pagination: three pages, 71 unique rows, oldest receipt reached only after `Load older`.
- Redaction: list JSON, detail JSON and DOM contained none of the seeded secrets; opaque keys were omitted while the bounded original remained readable.
- Draft behavior: a handoff draft prevented destructive refresh and survived until `Refresh now`.
- Mobile: 390 px viewport and body both had 390 px scroll width; no overflowing element was found.
- Screenshots: [desktop list](evidence/00-side-receipts-desktop.png), [redacted detail](evidence/01-side-receipt-redacted-detail.png), [mobile 390 px](evidence/06-side-receipts-mobile-390.png).
- Focused automated tests: receipt projection, controller and UI — 33 passed, 1 skipped; browser-enabled UI/controller set — 43 passed.
- Side duplicate-processing regressions: source-message grounding, no P1-update duplicate handoff, and question-only coordinator follow-up — 46 passed.
