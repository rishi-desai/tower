# CI is the repo's `mise run ci` task; checks that need a logged-in Claude stay out of it

tower's CI is whatever `mise run ci` runs. `.github/workflows/ci.yml` only calls `rishi-desai/ci`'s shared workflow,
which installs the tools pinned in `mise.toml` and runs that task, so a local pass means a CI pass and every personal
repo's CI changes in one place. A check goes into `ci` only if it runs with no Claude login. That's why the pane's
TypeScript type check is a separate `mise run pane-typecheck` task: its type stubs are written only when a logged-in
Claude Code session loads the pane, so CI can't produce them. (`claude plugin validate` and `claude plugin test` need no
login, so they are in `ci`.)

## Considered options

- **Commit the engine's type stubs** so CI can type-check the pane: rejected. The file is about 850 KB, marked early
  access, and rewritten by every Claude Code release, so a committed copy goes stale and fails CI on unrelated changes.
- **Put the type check in `ci` and skip it when the stubs are missing**: rejected. CI would always skip it, and a local
  run would fail or pass depending on whether the pane happened to be loaded, which breaks "local equals CI".
