# Vendored: babysit-pr (+ leaving-pr-comments)

Source: https://github.com/t3dotgg/tstack at commit `1085050f3baed1ac724caa4a8166145fb2ac88af` (MIT, see LICENSE).
Reviewed 2026-11-04 by reading every line.

SHA-256 of the upstream files as reviewed:
- `babysit-pr/SKILL.md` fbe43ba3ab76a466a88ef8b46dca1b41fb8101347d24a396903849865173a6ac
- `babysit-pr/scripts/pr-wait` 90393f85b95b78bff1b039744bdfb09dc88ff2b430e1c3df687ee6f449c97214
- `leaving-pr-comments/SKILL.md` 1d110343022d0514c101dd942286cce10d6b9f5bd5b899f5a0530620d34cd295

## Local patches (2026-11-04)

- `SKILL.md`: added a "Local rules" section at the top (show drafts and set the card to `needs-you`
  before any public action; wait with `--until green`), and swapped which mode is called the default.
- `scripts/pr-wait`: default `--until` changed from `ready` to `green` (one argument default and its
  docstring line). Reason: `ready` waits for the Greptile/Macroscope review bots, which neither the
  personal nor the work repos use, so it would never finish.
- `SKILL.md`: added `effort: high` to the frontmatter (tower sets effort per skill).
- `SKILL.md` (T08, #50; recorded here later): the approval rule writes the status card with `tower card write
  --status needs-you --reason "approve: <action>"` instead of setting it by hand.
- `SKILL.md` (stacked PRs, 2026-11-06): a "Merge with `tower merge`" local rule. `gh pr merge` can't merge a PR in a
  GitHub stack; `tower merge` (src/tower/merge.py) merges stacked and unstacked PRs, and the approval hook judges it
  like `gh pr merge`.

## Local patches (2026-11-06)

- `leaving-pr-comments/SKILL.md`: dropped the "🤖 <model> responding on behalf of <user>" note callout and its
  format rules, and added a rule against any attribution, signature or robot emoji. Reason: comments read as
  written by the user, the only author. Don't re-apply the note when pulling a newer upstream.

SHA-256 after patching:
- `babysit-pr/SKILL.md` 5af187ec41e1fc58b15384508c9a4ce7fd9f100f2009654138f3417129ee7cf5
- `babysit-pr/scripts/pr-wait` 6ae4ef3f9761a5223e454d0f32a174919bd72711bc315992c5e842485009f229
- `leaving-pr-comments/SKILL.md` cc1996158a8349cd153d03fa4c55faeb69eeb8825c5e9dcef312f2daa6840b90

Re-check after any update: `shasum -a 256 plugin/skills/babysit-pr/SKILL.md plugin/skills/babysit-pr/scripts/pr-wait plugin/skills/leaving-pr-comments/SKILL.md`
To pull a newer upstream: diff it against the commit above, re-read the changes, re-apply the local patches.

## Approval gate

Public actions are also gated outside this skill: tower's `plugin/scripts/confirm-public.py` is a Claude Code
PreToolUse hook that forces an approval prompt for GitHub writes and pushes (see the README).

## What `pr-wait` (the script) does

- Only runs `gh`, with arguments as a list (no shell, so no injection). All GitHub calls are reads:
  one GraphQL query for the PR, `gh pr checks`, `gh pr diff --name-only`, compare/branch-protection REST reads.
- No other network access, no `eval`/`exec`, no downloads.
- Writes only `~/.local/state/pr-wait/` (a lock file per PR and a JSON file of already-reported events).
- Polls every 20 s (min 5). Each poll spends GitHub API rate limit shared with every other `gh` use on the account.

## What the skill tells the agent to do on your behalf (outward-facing)

- **Push** fixes for real CI failures and review findings, and **rebase + push** when the branch falls behind.
- **Reply to and resolve** review-bot comments it judges not worth fixing, with a written reason.
  `leaving-pr-comments` keeps each one short and direct and checks it for secrets.
- **Merge only if you explicitly asked**, with `tower merge`; otherwise it reports "ready". Asks before closing an
  obsolete PR.

On the work repo, those replies and pushes are visible to your team. With the approval hook installed, each one
needs your OK first.

## Assumptions that may not fit

- The T3 Code `watch_pull_request` sections don't apply in Claude Code; it falls back to `pr-wait` with the Monitor tool.
