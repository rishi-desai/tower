# Drafted follow-up issue (not filed yet)

Drafted during #30 (T12a, the git-spice + worktrees spike). File it with:

```sh
gh issue create --label needs-triage \
  --title "Stacked PRs: GitHub's native stacks refuse gh pr merge; tower's merge path needs merge-async (and consider gh-stack)" \
  --body-file <(sed -n '/^<!-- body -->$/,$p' docs/research/followup-issue-stacked-merge.md | tail -n +2)
```

**Title:** Stacked PRs: GitHub's native stacks refuse gh pr merge; tower's merge path needs merge-async (and consider gh-stack)

<!-- body -->
Found during #30 (T12a, the git-spice + worktrees spike). Details and rerunnable evidence: `docs/research/git-spice-with-worktrees.md` (PR for #30) and branch `prototype/30-git-spice` (`remote.sh`, `remote-stack.sh` and their `*-out.txt`).

## What happens
When PRs chain their bases (`feat2 -> feat1 -> main`, which is what `git-spice stack submit` creates), GitHub groups them into a native stack on its own. The REST PR object gets a `stack` field. Then:

- `gh pr merge` fails: `GraphQL: This pull request is part of a stack and must be merged using the asynchronous merge REST API.`
- `PUT /repos/{o}/{r}/pulls/{n}/merge` returns 403: `Merging stacked PRs via this endpoint is not supported.`
- Only `PUT /repos/{o}/{r}/pulls/{n}/merge-async` works (then poll `GET .../merge-async/{uuid}` until `status` is `merged`).
- GitHub retargets and rebases the upper PRs server-side after a merge, and refuses base-branch edits on stacked PRs.

## Why it matters for tower
`plugin/skills/hub/SKILL.md:102` tells workers (under `pr_approval=none`) to merge with `gh pr merge --squash --delete-branch`. That breaks for any PR in a stack, and so does `babysit-pr` if it merges the same way. #32 (T12, stacked PRs with git-spice) will hit this.

## Questions
- Should the worker merge instruction fall back to `merge-async` for stacked PRs (detect via `gh api repos/{o}/{r}/pulls/{n} --jq .stack`)?
- `gh help` now advertises an official `gh stack` extension (`gh extension install github/gh-stack`). Does it make git-spice unnecessary for #32? Worth a short spike before #32 starts.
- Untested: `gh pr merge --auto` on a stacked PR, and `merge_action=merge_queue|default` on `merge-async` as the auto-merge path.
