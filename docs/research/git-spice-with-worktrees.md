# git-spice with Orca worktrees (#30)

Spike for #30 (T12a: does git-spice work cleanly when each branch of a stack lives in its own Orca worktree?), feeding #32
(T12, stacked PRs with git-spice). Run **2026-11-06** on macOS with git-spice 0.33.0, git 2.56.0, gh 2.102.1 and Orca
1.4.237.

Terms used below:

- **git-spice** is a CLI that tracks a *stack*: branches each based on the one below. It rebases, submits and syncs them
  together.
- **Orca** is the desktop app that creates a git worktree per task.
- **Trunk** is the branch stacks start from (`main`).
- **Restack** means rebasing a branch onto the current tip of its base.
- **Sync** (`git-spice repo sync`) pulls trunk, deletes branches whose PRs merged, and moves the branches above them onto
  the next base.

## Sources

- git-spice docs: [install](https://abhinav.github.io/git-spice/start/install/),
  [changelog](https://abhinav.github.io/git-spice/changelog/) (v0.16.0 worktree skips, v0.24.0/v0.25.0 `gs` rename),
  [config](https://abhinav.github.io/git-spice/cli/config/) (`spice.repoSync.detachWorktrees`),
  [CLI reference](https://abhinav.github.io/git-spice/cli/reference/) (`repo sync`).
- GitHub REST docs: [merge a pull request asynchronously](https://docs.github.com/rest/pulls/pulls#merge-a-pull-request-asynchronously).
- Experiments: throwaway scripts and their full output on branch
  [`prototype/30-git-spice`](https://github.com/rishi-desai/tower/tree/prototype/30-git-spice/docs/research/git-spice-spike.PROTOTYPE).
  That branch is never merged. Every quoted output below comes from one of these files:
  - `local.sh`/`local-out.txt`: questions 1 and 2, no network.
  - `remote.sh`/`remote-out.txt`: questions 3 and 4, one worktree per branch.
  - `remote-stack.sh`/`remote-stack-out.txt`: the recommended layout, one worktree per stack.
  - `env-out.txt`: versions and install facts.

  The GitHub runs used private scratch repos `rishi-desai/gs-spike-30-prototype{,-2,-3,-4,-5}`, all archived afterwards
  (`-4` is empty, from a failed run).

## Recommendation

**One worktree per stack.** The whole stack is checked out in one worktree (one session). You move between its
branches with `git-spice up`/`down`/`top`/`bottom`. In `remote-stack-out.txt` this layout ran clean from start to finish
with default config:

- submit set every base right;
- restack moved every branch in one command;
- sync deleted each merged branch and moved the next one onto `main`. This held even when the worktree sat *on* the
  merged branch: sync switched it to `main`.

One worktree per branch works too, but every restack and every merge turns into manual, bottom-up steps across several
worktrees. And with default config, `repo sync` leaves the stack in a state `submit` refuses (question 4).

**Proposed** change to tower's rule (not made here; for #32 and the glossary to decide): "one ticket = one branch = one
worktree" would become "one *stack* = one worktree" for stacked tickets. A stack is sequential anyway: the upper branch
can't land before the lower one.

If per-branch worktrees are ever needed (say, two agents on two layers at once), set
`git config spice.repoSync.detachWorktrees true` and restack bottom-up from each worktree. Both are shown below.

## Unexpected: GitHub now stacks the PRs itself

When `git-spice stack submit` opens PRs whose bases chain (`feat2 -> feat1 -> main`), GitHub groups them into a native
stack. The REST PR object gets a `stack` field (`{"base":{"ref":"main"},"position":2,"size":3,...}`). We didn't enable
anything in the scratch repos; whether every account gets this is unknown. From then on:

- **`gh pr merge` fails**: `GraphQL: This pull request is part of a stack and must be merged using the asynchronous merge
  REST API.` The REST `PUT .../pulls/N/merge` returns 403 (`Merging stacked PRs via this endpoint is not supported`).
  What works is `PUT .../pulls/N/merge-async`, then polling `GET .../merge-async/{uuid}` until `status` is `merged`.
- **GitHub retargets and rebases the upper PRs itself** when a lower one merges. This happened with "delete head branch
  on merge" off (`{"delete_branch_on_merge":false}` in `remote-stack-out.txt`).
- **A stacked PR's base can't be edited.** git-spice's own retarget during sync logs
  `WRN Retarget failed ... Cannot change the base branch because the pull request is part of a stack`. It's harmless:
  the base is already right.

Tower's workers merge with `gh pr merge`, so this breaks them on stacked PRs. `gh help` also advertises an official
`gh stack` extension. Both points are written up as a drafted follow-up issue in
[`followup-issue-stacked-merge.md`](followup-issue-stacked-merge.md), not filed yet.

## Answers

### 1. Is git-spice's branch tracking shared across worktrees?

**Yes.** Tracking lives in one ref, `refs/spice/data`, in the shared `.git`. A branch created with
`git-spice branch create` in any worktree shows up straight away in `git-spice log short --all` from every other
worktree. `log` also tags each branch with the worktree holding it (`feat2 [wt: $ROOT/wt-feat2]`).

Two gotchas:

- `git-spice branch create` in a per-branch worktree **moves that worktree** to the new branch. After it, `wt-feat3`
  holds `feat4`, and `feat3` isn't checked out anywhere.
- A branch made outside git-spice is untracked. `orca worktree create --base-branch feat1` makes a plain new branch
  (per its `--help`). The script simulates this with `git worktree add -b`, without running Orca itself. Running
  `git-spice branch track` inside the worktree adopts it, and it inferred `base feat1` on its own.

```sh
git-spice log short --all            # from any worktree: whole stack, with [wt: path] per branch
git for-each-ref refs/spice          # the single shared tracking ref
git-spice branch track               # adopt a branch made by Orca or plain git; base is inferred
```

### 2. Can git-spice restack a branch that's checked out in another worktree?

**No, and it also skips everything above it.** Plain git refuses outright:
`fatal: 'feat2' is already used by worktree`. git-spice skips instead of failing (since v0.16.0), and the skip cascades
up the stack:

```text
$ git-spice stack restack            # from wt-feat1, after trunk moved
WRN feat2: checked out in another worktree ($ROOT/wt-feat2), skipping
WRN feat3: base branch feat2 was not restacked, skipping
WRN feat4: base branch feat3 was not restacked, skipping
INF feat1: restacked on main
```

From the main checkout, `git-spice repo restack` skipped `feat1` because `wt-feat1` holds it. Then it skipped
`feat2`–`feat4` because their base was skipped, and restacked nothing. In the per-branch layout the only fix is to go
bottom-up, one worktree at a time:

```sh
(cd wt-feat1 && git-spice branch restack)
(cd wt-feat2 && git-spice branch restack)
(cd wt-feat3 && git-spice upstack restack --branch feat3)   # this worktree holds feat4 now
```

With one worktree per stack, `git-spice stack restack` (or `upstack restack`) moved every branch in one go.

### 3. Does `git-spice stack submit` set each PR's base branch correctly?

**Yes, in both layouts.** That includes running it from one worktree while other worktrees hold the other branches:

```text
$ git-spice stack submit --fill --no-draft --nav-comment=false
#1 feat1 -> main
#2 feat2 -> feat1
#3 feat3 -> feat2
```

Amending an upper branch and running `git-spice branch submit` afterwards force-pushes a stacked branch fine.

### 4. After a lower PR merges, is the next PR retargeted?

**On GitHub, yes: GitHub retargets it itself** (see the section above). Locally it takes a `git-spice repo sync`, and
how that goes depends on the layout:

- **One worktree per stack:** default sync works. It deletes the merged branch and moves the next one onto `main`.
  If you're on the merged branch, it switches you to `main` first.
- **Per branch, default config:** sync skips deleting `feat1` (`checked out in another worktree, skipping deletion`)
  and doesn't move `feat2` locally. `git-spice branch submit` then refuses:
  `Branch has stale base branch=feat2 base=feat1 ... run 'gs repo sync' first`.
- **Per branch, with `spice.repoSync.detachWorktrees=true`:** sync detaches `wt-feat1`, deletes `feat1` and moves
  `feat2` onto `main`. Per the config docs, it leaves the worktree's changes in place.

After the sync, git-spice's restack and GitHub's server-side rebase give the same tree but different SHAs
(`local feat2 12cf72c, origin/feat2 ed0f3ea, same tree: yes`). So run `git fetch` before submitting. Without it, the
local copy of the remote branch is stale and git-spice's lease-protected push is rejected:
`! [rejected] ... -> feat2 (stale info)`. After the fetch, `git-spice stack submit` pushes the equivalent rebase.
Alternatively, take GitHub's version with `git reset --hard origin/feat2` and restack above it.

Auto-merge wasn't tested: GitHub's auto-merge needs required checks, and the scratch repos had none. Untested
inferences:

- `gh pr merge --auto` likely hits the same GraphQL refusal;
- `merge-async` with `merge_action=merge_queue` or `default` is the documented route through a merge queue.

Either way, the merge itself is the same event, so the retargeting above should apply.

```sh
gh api -X PUT repos/OWNER/REPO/pulls/1/merge-async -f merge_method=squash -f merge_action=direct_merge
gh api repos/OWNER/REPO/pulls/1/merge-async/UUID --jq .status        # pending, then merged
gh api repos/OWNER/REPO/pulls/2 --jq '{base: .base.ref, stack}'        # base is now main
git-spice repo sync && git fetch && git-spice stack restack && git-spice stack submit
```

### 5. The `gs` / Ghostscript name clash on macOS

**Fixed upstream: the binary is now called `git-spice`.** The changelog shows the rename:

- v0.24.0 deprecated `gs` ("renaming the 'gs' binary to 'git-spice' to avoid conflicting with other command line
  tools");
- v0.25.0 dropped it from GitHub Releases, the Homebrew Cask and the AUR package.

Current packages ship only `git-spice` (`env-out.txt`):

- homebrew-core's caveat reads "The 'gs' executable has been renamed to 'git-spice'";
- mise's `aqua:abhinav/git-spice` install directory contains only `git-spice`.

Being on `PATH` as `git-spice` also makes `git spice ...` work as a git subcommand. The docs suggest `alias
gs='git-spice'`, but skip that on any machine that needs Ghostscript (it isn't installed on this one).

**Note for #32:** its text says "submit with `gs`". Tower's docs, scripts and kickoffs should write `git-spice`.

```sh
brew install git-spice          # or: mise use aqua:abhinav/git-spice@0.33.0
git-spice --version             # git-spice 0.33.0
git spice --version             # same binary, as a git subcommand
```

## Rerun

```sh
mise install aqua:abhinav/git-spice@0.33.0
export PATH="$(mise where aqua:abhinav/git-spice@0.33.0):$PATH"
git fetch origin prototype/30-git-spice
SPIKE=$(mktemp -d) && git worktree add "$SPIKE" origin/prototype/30-git-spice
cd "$SPIKE/docs/research/git-spice-spike.PROTOTYPE"
bash local.sh
bash remote-stack.sh OWNER/new-scratch-repo     # creates it private; one repo per run
gh repo archive -y OWNER/new-scratch-repo
```
