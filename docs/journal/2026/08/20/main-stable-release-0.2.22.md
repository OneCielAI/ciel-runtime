# Main stable release 0.2.22

## Release scope

- Promote the tested `nightly` history through commit `6d7ffb8` to `main`.
- Publish a new stable npm artifact rather than reusing the already-published
  `0.2.21` artifact.
- Start the next nightly development branch from the released main commit.

## Version evidence

- Before release, `origin/main` was `829f389` and was a direct ancestor of
  `origin/nightly` at `6d7ffb8`.
- npm `latest` was already `0.2.21`; the main publish workflow skips an artifact
  when that exact version already exists.
- The stable source and package version were therefore advanced together to
  `0.2.22`.

## Verification

- Pre-push full suite passed: unit 1,105, router 902, channel 372, and
  runtime 247; 136 declared environment-dependent tests were skipped.
- Ruff and documentation metadata checks passed.
- `npm pack --dry-run --json` reported version `0.2.22`, 380 packaged files,
  and included both the remote-memory implementation and this release journal.
- Published-artifact verification remains pending until the main workflow runs.
