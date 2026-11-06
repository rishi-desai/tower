# PROTOTYPE: git-spice with worktrees (#30). Throwaway, never merge.

Primary source for `docs/research/git-spice-with-worktrees.md` on integration/a-plus. Captured 2026-11-06 with git-spice 0.33.0.

- `local.sh`: questions 1 and 2 (tracking across worktrees, restacking branches held by other worktrees). Local only.
- `remote.sh OWNER/REPO`: questions 3 and 4 (submit bases, retargeting after a merge). Creates a private scratch repo; archive it after.
- `local-out.txt`, `remote-out.txt`: the output of the final runs (temp paths shown as `$ROOT`).
