# Merging stacked PRs: GitHub's behaviour, checked end to end

Checked for `tower merge` (`src/tower/merge.py`) on **2026-11-06** with gh 2.102.1 and git-spice 0.33.0, in the
private scratch repo `rishi-desai/tower-stack-merge-scratch` (archived afterwards). It answers the questions in
[followup-issue-stacked-merge.md](followup-issue-stacked-merge.md), drafted during #30 (the git-spice + worktrees
spike, [git-spice-with-worktrees.md](git-spice-with-worktrees.md)).

Terms used below:

- A **GitHub stack** is GitHub's own grouping of PRs: the REST PR object gets a `stack` field
  (`{"base":{"ref":"main"},"position":2,"size":3,...}`).
- A **plain chain** is PRs whose bases chain (`feat2 -> feat1 -> main`) without being a GitHub stack.
- **merge-async** is `PUT /repos/{o}/{r}/pulls/{n}/merge-async`, GitHub's asynchronous merge API. Its status is
  polled at `GET .../merge-async/{uuid}`.
- To **retarget** a PR is to change its base branch.

## Sources

- GitHub docs: [merging stacked PRs](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/merging-stacked-pull-requests),
  [about stacked PRs](https://docs.github.com/en/pull-requests/get-started/about-stacked-prs),
  [REST pulls endpoints](https://docs.github.com/en/rest/pulls/pulls?apiVersion=2026-03-10) (merge-async),
  [stacks CLI](https://docs.github.com/en/pull-requests/reference/stacked-prs-cli-commands) (`gh stack`),
  changelogs: [async merge API GA](https://github.blog/changelog/2026-10-01-github-async-merge-api-generally-available/),
  [stacked PRs GA](https://github.blog/changelog/2026-10-06-stacked-pull-requests-generally-available).
- The run: every quoted output below is from its log. The script itself isn't kept, since it created and changed a
  GitHub repo; "Rerun" lists its steps.

## What GitHub does

1. **GitHub stacks PRs only when they are submitted as a stack.** Three PRs opened one by one with `gh pr create`
   (`feat1 -> main`, `feat2 -> feat1`, `feat3 -> feat2`) had `"stack": null`, and still did 15 seconds later.
   The same shape submitted with `git-spice stack submit` became a stack at once. This corrects the spike's "GitHub
   groups them into a native stack on its own": it was git-spice.

   ```text
   #4 {"base":"main","stack":{"base":"main","position":1,"size":3}}
   #5 {"base":"s1","stack":{"base":"main","position":2,"size":3}}
   #6 {"base":"s2","stack":{"base":"main","position":3,"size":3}}
   ```

2. **`position` counts from the bottom**: 1 is the PR based on the stack's base. **It isn't renumbered after a
   merge**: once #4 had merged, #5 was still `position 2, size 3`. So `tower merge` tells the bottom PR by its base
   being the stack's base, not by `position == 1`.

3. **Stacked PRs refuse the other merge paths**, as the spike found: `gh pr merge` fails with `GraphQL: This pull
   request is part of a stack and must be merged using the asynchronous merge REST API`, and `PUT .../merge` returns
   403. GitHub's docs add that merging a PR in a stack also merges every unmerged PR below it, onto the stack's base.
   `tower merge 4` (the bottom) took 9 seconds in all: the merge, GitHub's retarget of #5, and the delete of `s1`.

   ```text
   tower: merging PR #13 with a merge commit: it is in a GitHub stack
   tower: PR #13 is in a GitHub stack on main: merging it with GitHub's asynchronous merge API
   tower: merged PR #13 into main
   tower: deleted t2 on GitHub (the local branch is left as it is)
   ```

4. **After a merge, GitHub retargets the PR above onto the stack's base and rebases the PRs above, whatever the
   method.** With `merge_method=merge`, `t2`'s head went from `5eaa77f` to `d6615f8`. The tree was the same, and
   `t1`'s own commit stayed on `main` inside the merge commit; only the upper PRs' own commits got new SHAs. A
   stacked PR's base can't be edited by hand (the spike's `Cannot change the base branch because the pull request is
   part of a stack`).

5. **Deleting a branch through the API closes the open PRs based on it.** On a plain chain, `gh pr merge 1 --squash
   --delete-branch` (the path workers used) deleted `feat1`, and GitHub *closed* #2 instead of retargeting it:

   ```text
   #1 feat1 -> main [MERGED]
   #2 feat2 -> feat1 [CLOSED]
   ```

   With the repo's "Automatically delete head branches" on, GitHub's own delete retargets them instead, both for a
   stack (#6 moved to `main`, open) and for a plain chain (#9 moved to `main`, open, head unchanged).

6. **gh api prints GitHub's error body on stdout** and its own one-line summary on stderr (`gh: ... (HTTP 403)`),
   with exit 1. `tower merge` reads GitHub's message from stdout.

7. **A merged stacked PR keeps its `stack` field**, so a rerun of `tower merge` still knows to wait for GitHub's
   retarget before deleting the branch.

## What `tower merge` does with it

- **Merge path:** a stacked PR goes through merge-async, only from the bottom of its stack, one PR per run. An upper
  PR is refused (`PR #5 is based on s1, not on its stack's base main ... merge the PR from s1 first`). Any other PR
  goes through `gh pr merge`.
- **Method, with no setting:** a merge commit for a stacked PR, for a PR with open PRs based on its branch (a plain
  chain), and for a PR whose head other PRs were merged into (an integration branch, like `integration/a-plus ->
  main`). A squash for every other PR. If the repo allows no merge commits: a squash (a rebase if squashes are off
  too), and it says so. Checked here:
  - a stack's bottom PR got a merge commit;
  - a lone PR got a squash and its branch deleted;
  - `integ -> main`, after two PRs were squash-merged into `integ`, got a merge commit. The per-ticket commits
    `i1 ticket (#16)` and `i2 ticket (#17)` are on `main`, `integ` was kept, and it is an ancestor of `main`.
- **Branch delete:** only when no open PR is based on the branch. For a stacked PR it waits up to 2 minutes for
  GitHub's retarget. On a plain chain it never waits, since GitHub won't retarget there. It leaves the branch and
  says how to move the PRs above (`gh pr edit 11 --base main`); a rerun then deletes it (checked). An integration
  branch is always kept. A branch that moved since the merge is kept. A repo that deletes head branches itself is
  left to do it.
- **No CI wait:** `tower merge` never waits for CI between merges. GitHub enforces the repo's branch protection; if
  it refuses a merge, `tower merge` exits 1 with GitHub's message.
- **The approval hook:** under `personal` (merge_to_main ask, merge_to_branch auto), against this repo's real
  GitHub, `tower merge 5`, `gh api -X PUT .../pulls/5/merge-async` and `gh pr merge 5` on the upper PR #5 (based on
  `s1`, stack on `main`) all asked: `tower merge merges a PR onto its stack's base, the default branch
  (merge_to_main=ask)`. An unstacked PR based on `feat2` passed as merge_to_branch.

## Observed practice: a 16-PR work stack

From the user's work repo on github.com, not this run. A 16-PR stack was merged bottom-up, one PR at a time, about 20
seconds apart (8 minutes in all), into a non-default base branch (`customer-spike`). Each PR got a merge commit, and
each merge commit's first parent is the previous merge. CI didn't run again between merges: each PR's checks had run
against its stacked base, and since every PR holds the one below it, the final tree equals the tested top of the
stack.

That fits a plain chain retargeted by GitHub's own branch delete, which keeps the PR's head (point 5). For a GitHub
stack, GitHub rebases the PRs above after each merge (point 4), so their heads are new commits. Whether required
checks then have to pass again before the next merge is **untested** (see below).

## The drafted issue's questions

- **Should the worker merge instruction fall back to merge-async for stacked PRs?** Yes, through `tower merge`: one
  command for every PR, judged by the approval hook like `gh pr merge`.
- **Does the official `gh stack` extension make git-spice unnecessary for #32?** Partly answered from GitHub's docs
  only. **Untested**: the extension wasn't installed.
  - What the docs say `gh stack` does: `submit` creates the PRs and links them as a GitHub stack; `rebase`; `sync
    --prune`, which fast-forwards trunk, rebases and deletes local branches of merged PRs; and `merge
    [<stack-number> | <pr-number>]`, which merges every PR up to the one chosen, all or nothing. Whichever tool
    submits the stack, `tower merge` merges it the same way.
  - What the docs don't say: how it treats branches checked out in other worktrees (git-spice skips them), where it
    keeps its local tracking, and which API its `merge` uses.
  - Suggestion: before #32 starts, a short spike reruns the #30 spike's worktree questions with `gh stack`. Until
    then, keep git-spice, whose worktree behaviour is known.
- **`--delete-branch` has no merge-async equivalent: who deletes merged branches?** Either works:
  - "Automatically delete head branches" on: GitHub retargets the PRs above first (observed for a stack and a plain
    chain);
  - by hand, only once no open PR is based on the branch: deleting it earlier closes them (observed). `tower merge`
    does this and waits for GitHub's retarget on a stack.
- **`gh pr merge --auto` on a stacked PR, and `merge_action=merge_queue|default`.** **Untested** beyond this: with
  nothing pending, `gh pr merge 4 --auto --squash` tried a direct merge and got the same stack refusal. A private repo
  on this plan can't turn on auto-merge or branch protection (`Upgrade to GitHub Pro or make this repository public`),
  so auto-merge with pending checks wasn't tried. GitHub's stacked-PR GA changelog (October 6) says auto-merge for
  stacks is "rolling out over the next few weeks". Merge queues need an organization's repo, so `merge_queue` wasn't
  tried either.

## Untested

- Whether required checks must pass again on the rebased upper PRs of a GitHub stack before the next merge (needs
  required checks: GitHub Pro, a public repo, or an organization).
- Auto-merge on a stacked PR with pending checks, and merge queues (`merge_action=merge_queue|default`).
- The 409 "merge already pending" answer, which `tower merge` picks up (covered by unit tests only).
- The rebase fallback when a repo allows neither merge commits nor squashes.
- GitHub Enterprise Server hosts.
- `gh stack` itself.

## Rerun

With a throwaway private repo `OWNER/REPO` (`gh repo create OWNER/REPO --private`) and git-spice 0.33.0
(`mise install aqua:abhinav/git-spice@0.33.0`):

1. Plain chain: open `feat1 -> main`, `feat2 -> feat1` with `gh pr create`; read `gh api repos/OWNER/REPO/pulls/N
   --jq .stack` (null); run `tower merge` from a worktree on `feat1`.
2. Stack: `git-spice repo init`, three `git-spice branch create`, `git-spice stack submit --fill --no-draft
   --nav-comment=false`; read each PR's `.stack`; run `tower merge` on the top PR (refused), then on the bottom.
3. Turn on `delete_branch_on_merge` (`gh api -X PATCH repos/OWNER/REPO -F delete_branch_on_merge=true`) and merge
   the next PR, with `tower merge` and with a plain chain.
4. Merge a stack's bottom with `merge_method=merge` and compare the upper heads' SHAs and trees before and after.
5. Integration branch: squash-merge two PRs into `integ`, then `tower merge` the `integ -> main` PR.
6. `gh repo archive -y OWNER/REPO`.
