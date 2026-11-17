# Change log

## 2026-11-17
- Measured git's ownership check with `GIT_TEST_ASSUME_DIFFERENT_OWNER=1`:
  - command-scope `safe.directory` works with the exact root and not with a parent;
  - a linked worktree needs its own root.
- `ec4e527`:
  - exact trust for the router's launch folder and its Work worktrees;
  - full release of a failed reservation (lease, coordination run, P0 intent);
  - workspace failure codes on every surface;
  - reconciliation of never-launched runs and P0 intents, with backup;
  - operator control on menu, web, REST and ctl.
- Behavior probe before and after on `6622029`.
- 15 new tests; Windows Work modules, Linux and `claude plugin test` runs.
- Real Claude Code 2.1.326 E2E before and after.
