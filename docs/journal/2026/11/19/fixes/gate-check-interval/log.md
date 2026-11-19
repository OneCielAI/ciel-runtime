# Log

## 2026-11-19

- User: the blocked-item side check should run every 10 minutes instead of 5, and not at all with no blocked items.
- Measured on Andy: 121 periodic gate-check events today, median gap 5.7 minutes. 9 turn-end events. No round with 0 gates.
- Source: `tick_rows` already returns nothing for a board with no gates, except at main's turn end. The router answers `no_gates`.
- `DEFAULT_MINUTES` changed from 5 to 10. Tests were updated and one test added. The 7 gate-check files passed: 150 tests, 0 failures.
- Live: Andy, Celly and KOBE were set to 10 through Work REST `settings-update`. All three answered `ok` and read back 10. This takes effect without a restart.
- See [record.md](record.md).
