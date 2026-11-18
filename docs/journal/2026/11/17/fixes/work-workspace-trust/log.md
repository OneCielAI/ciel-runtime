# Change log

## 2026-11-17
- 0dc0014 pushed to beta. Publish run 44189662051, job `test (unit, 2/3)`: 4 trust tests failed on git 2.56.1.
  - GitHub's Ubuntu runner images write `[safe] directory = *` to `/etc/gitconfig` (actions/runner-images `install-git.sh`), so git never refused there.
  - Reproduced in Docker with that system file: the 4 tests fail before the fix and all 15 pass after ([runner-gitconfig-repro.txt](evidence/runner-gitconfig-repro.txt)).
  - Test fix: the foreign-owner env sets `GIT_CONFIG_NOSYSTEM=1` and an empty `GIT_CONFIG_GLOBAL`, so only command-scope trust counts. Product code is unchanged.
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
