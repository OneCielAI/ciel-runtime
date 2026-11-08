# Change log

## 2026-11-07

- Released and installed corrected beta 0a881e0 locally after the initial 8bd9fe2 release (both registry-verified; new snapshots; installed smokes passed).
- Real SSE probe found that `/work-mode view` did not open the pane, that SSE rows vanished and that
  finished forks stayed painted; fixed all three and verified fork active → ended → answered in a real console.

- Acknowledged that 93be24c did not implement the reviewed graphical mockup.
- Added terminal Client dashboard with Work hierarchy, SSE flow, progress, parallel lanes and inspector.
- Added host-owned active side-fork projection.
- Added keyboard and pointer navigation; retained responsive fallback.
- Verified populated split overview in an owned local conhost.
