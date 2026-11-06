# git-spice with Orca worktrees (#30)

Spike for #30 (T12a: does git-spice work cleanly when stacked branches live in Orca worktrees?). Run **2026-11-06** with
git-spice **0.33.0**, git 2.x, gh 2.102.1, Orca's bundled CLI, on macOS. git-spice is a CLI that tracks a *stack*
(branches each based on the one below) and rebases, submits and syncs them together.

The throwaway scripts and their full output are on the branch
[`prototype/30-git-spice`](https://github.com/rishi-desai/tower/tree/prototype/30-git-spice/docs/research/git-spice-spike.PROTOTYPE)
(`local.sh`: worktree questions, no network; `remote.sh`: GitHub questions, needs a scratch repo). The GitHub runs used
private throwaway repos `rishi-desai/gs-spike-30-prototype{,-2,-3}`, now archived.

## Recommendation

**One worktree per stack**: the whole stack is checked out in one worktree (one session) and you move between its
branches with `git-spice up`/`down`. Per-branch worktrees work, but every restack and every merge turns into manual,
bottom-up steps across several worktrees, and the default `repo sync` leaves the stack in a state `submit` refuses.

For tower this means the rule "one ticket = one branch = one worktree" becomes "one *stack* = one worktree" for stacked
tickets. A stack is sequential anyway: the upper branch can't land before the lower one.

If per-branch worktrees are ever needed (say, two agents on two layers at once), set
`git config spice.repoSync.detachWorktrees true` and restack bottom-up from each worktree, as described below.

## The bigger finding: GitHub now stacks the PRs itself

This changes more than the worktree question. When `git-spice stack submit` opens PRs whose bases chain
(`feat2 -> feat1 -> main`), GitHub groups them into a native stack on its own (no opt-in on our side). The REST PR object
gets a `stack` field (`{"base":{"ref":"main"},"position":2,"size":3,...}`), and from then on:

- **`gh pr merge` fails** on every PR in the stack: `GraphQL: This pull request is part of a stack and must be merged
  using the asynchronous merge REST API.` The REST `PUT .../pulls/N/merge` returns 403 too. The only route is
  `PUT /repos/{owner}/{repo}/pulls/{n}/merge-async` (`merge_method`, `merge_action=direct_merge|merge_queue|default`),
  then poll `GET .../merge-async/{uuid}` until `status` is `merged`.
- **GitHub retargets and rebases the upper PRs itself** when a lower one merges, even with "delete head branch on merge"
  off: PR 2's base became `main`, and `origin/feat2` was rewritten server-side onto the squash commit.
- **A stacked PR's base can't be edited.** git-spice's own retarget after sync gets `UNPROCESSABLE: Cannot change the base
  branch because the pull request is part of a stack.` It only logs a warning, and the base is already right.

Consequence for tower: anything that merges with `gh pr merge` (workers' kickoff rule 6, `babysit-pr`) breaks on stacked
PRs. GitHub also ships an official `gh stack` extension (`gh extension install github/gh-stack`, suggested by `gh help`).
It may make git-spice unnecessary and deserves its own spike before tower adopts either one.

## Answers

### 1. Is git-spice's branch tracking shared across worktrees?

**Yes.** Tracking is one ref, `refs/spice/data`, in the shared `.git`. A branch created with `git-spice branch create` in
any worktree shows up immediately in `git-spice log short --all` from every other worktree. `log` also tags each branch
with the worktree that holds it (`feat2 [wt: .../wt-feat2]`).

Two gotchas:

- `git-spice branch create` in a per-branch worktree **moves that worktree** to the new branch: `wt-feat3` then holds
  `feat4`, and `feat3` isn't checked out anywhere.
- A branch made outside git-spice is untracked. That includes Orca's, since `orca worktree create --base-branch feat1`
  makes a plain new branch. Run `git-spice branch track` inside the worktree: it inferred `base feat1` on its own.

```sh
git-spice log short --all            # from any worktree: whole stack, with [wt: path] per branch
git for-each-ref refs/spice          # the single shared tracking ref
git-spice branch track               # adopt an Orca-created branch; base is inferred
```

### 2. Can git-spice restack a branch that's checked out in another worktree?

**No, and it skips everything above it too.** Plain git refuses outright (`fatal: 'feat2' is already used by worktree`).
git-spice (since v0.16.0) skips instead of failing, but the skip cascades up the stack:

```text
$ git-spice stack restack            # from wt-feat1, trunk has moved
WRN feat2: checked out in another worktree (.../wt-feat2), skipping
WRN feat3: base branch feat2 was not restacked, skipping
WRN feat4: base branch feat3 was not restacked, skipping
INF feat1: restacked on main
```

`git-spice repo restack` from the main checkout skipped *every* branch, because each one is held by some worktree.
Branches above a skip aren't restacked even when nobody holds them. In the per-branch layout the only fix is bottom-up,
one worktree at a time:

```sh
(cd wt-feat1 && git-spice branch restack)
(cd wt-feat2 && git-spice branch restack)
(cd wt-feat3 && git-spice upstack restack --branch feat3)   # this worktree holds feat4 now
```

With **one worktree per stack** none of this comes up: `git-spice stack restack` restacked all four branches in one go.

### 3. Does `git-spice stack submit` set each PR's base branch correctly?

**Yes**, even when run from one worktree while the other branches are held by others (pushing doesn't need a checkout):

```text
$ git-spice stack submit --fill --no-draft --nav-comment=false
#1 feat1 -> main
#2 feat2 -> feat1
#3 feat3 -> feat2
```

Amending an upper branch and running `git-spice branch submit` afterwards force-pushes a stacked branch without trouble.

### 4. After a lower PR merges, is the next PR retargeted?

**On GitHub, yes, by GitHub itself** (see the native stack section above): PR 2 went `feat2 -> main` and `feat2` was
rebased server-side. Locally it takes a sync, and the worktree layout matters:

- **Default config, merged branch held by a worktree:** `repo sync` skips deleting `feat1` (`checked out in another
  worktree, skipping deletion`) and **doesn't retarget `feat2` locally**. `git-spice branch submit` then refuses:
  `Branch has stale base branch=feat2 base=feat1 ... run 'gs repo sync' first`. Restacking at this point is wrong, since
  it rebases onto the merged `feat1`.
- **With `spice.repoSync.detachWorktrees=true`:** sync detaches `wt-feat1` (it keeps its files and changes), deletes
  `feat1`, and retargets `feat2` onto `main`. This works.
- **One worktree per stack:** the worktree sits on the top branch, so the merged branch isn't held anywhere and the
  default sync works.

After the sync, the local branch and GitHub's server-side rebase have the same tree but different SHAs. Either:

- `git fetch`, then `git-spice branch restack && git-spice branch submit`. This overwrites GitHub's rebase with an
  equivalent one. Skip the fetch and git-spice's lease-protected push is rejected with `(stale info)`.
- Or `git reset --hard origin/feat2` to take GitHub's version, then `git-spice upstack restack` above it.

"Auto-merge" ends in the same merge event, so it wasn't tested separately. `gh pr merge --auto` would hit the same
GraphQL refusal; with stacks, auto-merging goes through `merge-async` with `merge_action=merge_queue` or `default`.

```sh
gh api -X PUT repos/OWNER/REPO/pulls/1/merge-async -f merge_method=squash -f merge_action=direct_merge
gh api repos/OWNER/REPO/pulls/1/merge-async/UUID --jq .status        # pending -> merged
gh api repos/OWNER/REPO/pulls/2 --jq '{base: .base.ref, stack}'        # base is now main
git config spice.repoSync.detachWorktrees true && git-spice repo sync
```

### 5. The `gs` / Ghostscript name clash on macOS

**Already solved upstream: the binary is called `git-spice`.** Homebrew's formula says: "The 'gs' executable has been
renamed to 'git-spice'. If you prefer to use 'gs', add an alias to your shell configuration: alias gs='git-spice'". The
GitHub release archive (and so mise's `aqua:abhinav/git-spice`) ships only `git-spice` too. Since it's on `PATH` as
`git-spice`, `git spice ...` also works as a git subcommand. Don't alias `gs` on machines that also need Ghostscript
(not installed on this Mac). In tower's own docs and scripts, always write `git-spice`.

```sh
brew install git-spice          # or: mise use aqua:abhinav/git-spice@0.33.0
git-spice --version             # git-spice 0.33.0
git spice --version             # same binary, as a git subcommand
```

## Rerun

```sh
git fetch origin prototype/30-git-spice
git worktree add /tmp/gs-spike origin/prototype/30-git-spice
export PATH="$(mise where aqua:abhinav/git-spice@0.33.0):$PATH"   # after: mise install aqua:abhinav/git-spice@0.33.0
bash /tmp/gs-spike/docs/research/git-spice-spike.PROTOTYPE/local.sh
bash /tmp/gs-spike/docs/research/git-spice-spike.PROTOTYPE/remote.sh OWNER/new-scratch-repo   # creates it private
gh repo archive -y OWNER/new-scratch-repo
```
