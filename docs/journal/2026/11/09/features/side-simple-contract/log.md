# Change log

## 2026-11-09
- **Owned real console (run `224432`).** Main stayed busy with a 273k context while six Walkie events (status, info with a code read, request, restated request, reorder, P1) went through. Each was committed and answered by one host send 10–17 s after receipt. Redelivery produced nothing new.
- **Fixed three defects found by the first owned runs:**
  - code reads refused for missing identity, because the engine skips the plugin's own hook for agents it spawned;
  - a new P1 outage merged into the active refactoring item;
  - a recap that dropped a stated requirement.
- **Removed the side pane's stale `external ACK: not_sent` line,** which contradicted the host delivery state.
- **Implemented the four-kind contract:**
  - host-composed `final_text` with the committed queue position;
  - Walkie preset prefetch and send;
  - `reorder_queued_root` and `queue_position`;
  - router-guarded code reads;
  - the parent-prefix cache mark;
  - an actor with no send or Work tools and `maxTurns` 4.
