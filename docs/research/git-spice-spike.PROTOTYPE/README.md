# PROTOTYPE: git-spice with worktrees (#30). Throwaway, never merge.

Primary source for `docs/research/git-spice-with-worktrees.md` on integration/a-plus. Captured 2026-11-06 with git-spice 0.33.0.

- `local.sh`: questions 1 and 2 (tracking across worktrees, restacking branches held by other worktrees). Local only.
- `remote.sh OWNER/REPO`: questions 3 and 4 with one worktree per branch. Creates a private scratch repo; archive it after.
- `remote-stack.sh OWNER/REPO`: the recommended layout, one worktree for the whole stack, plus the REST 403, the
  stale-push rejection and the same-tree check.
- `*-out.txt`: output of the final runs (temp paths shown as `$ROOT`). `env-out.txt`: versions and install facts.

`remote-out.txt` was captured before two step titles in `remote.sh` were reworded; it still says "push rejected" above a
push that succeeded (it ran after a `git fetch`). `remote-stack-out.txt` shows both the rejection and the success.
