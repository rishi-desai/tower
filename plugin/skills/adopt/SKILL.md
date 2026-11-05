---
name: adopt
description: Bring a repo that already has work in flight under tower — several sessions in one checkout, worktrees without status cards, unclear ownership. Use when the user wants to start tower on an existing, messy project. Run from the repo's main checkout.
effort: high
---

# Adopt

The user already has sessions, worktrees and uncommitted changes in this repo and has lost track of them. Goal: every piece of in-flight work ends up in its own worktree with a status card, without losing a single change. You plan; the user approves; nothing is deleted or moved without a yes.

## 1. Gather facts

Run `scripts/adopt-scan.sh` from this skill's base directory (use its absolute path), from inside the repo. It is read-only. It prints, per worktree: card or not, uncommitted files, commits ahead/behind the default branch, last commit, PR, ticket guess; the live Claude sessions in each worktree; and branches with unmerged work but no worktree.

## 2. Present one plan

Show the current state in a few lines, then a numbered plan, in this order, skipping what doesn't apply:

1. **Sessions write their own cards.** For every worktree with a live session, give the user this prompt to paste into that session (name the session from LIVE SESSIONS):
   > tower is now on in this repo. Stop after your current step. Use the status-card skill to write your card: what you're working on, what's left, and what you need from me. If you're in the main checkout, also list exactly which uncommitted files are your work, and don't move or commit anything yet.
2. **Untangle the main checkout** (if it has uncommitted changes or more than one session besides you). Once the sessions there have listed their files, propose a branch name per piece of work (`<type>/<ticket>-<slug>`) and the move steps below. If two sessions claim the same file, ask the user who owns it.
3. **Draft cards for worktrees with no live session.** Status `needs-you`; fill Done from the last commits, Needs you = "Drafted by adopt from git — is this still wanted?"; ticket from the PR or branch name. Show the drafts before writing them.
4. **WIP.** If more worktrees are active than `WIP limit`, recommend which to park, in this order: PR merged or branch fully on the default branch, then longest untouched. Parking = card `blocked` with "parked by user"; the worktree stays.
5. **Cleanup suggestions** (one line each, never done by you): worktrees whose branch is merged, branches with unmerged work and no worktree (offer to make them a worktree or a parked ticket).
6. **Tickets.** Work with no issue: offer to create one (a public action, so it will ask) or leave `ticket: none`.

Then ask which steps to do. Do only the approved ones, one at a time, and report after each.

## Moving work out of the main checkout

For one piece of work owned by one session, with the user's yes for each step:

1. `git worktree add -b <branch> <worktrees-dir>/<slug>` — use the folder where the user's other worktrees live (in Orca, they may prefer to create it there; ask).
2. `git stash push -m "adopt: <branch>" -- <only that work's files>` in the main checkout.
3. In the new worktree: `git stash pop`. If it conflicts, stop and show the user; the stash is kept until a clean pop.
4. Tell the user to continue that work in a new session in the new worktree (give the kickoff prompt), and to end the old session in main.

Untracked files: include them with `git stash push -u -- <files>`. Never use `git checkout .`, `git restore .`, `git reset --hard`, or `git clean` here.

## 3. Finish

Run the hub (`tower:hub`) and show its report, so the user sees the clean state.

## Gotchas

- You run in the main checkout yourself; LIVE SESSIONS counts you there.
- Ahead/behind compares with `origin/<default>` when it exists, so "ahead" means "not yet on the remote".
- "Commits not on main" doesn't prove the work is missing from main: it may have been squash-merged (the scan shows the PR state) or landed another way. Before suggesting a branch is unshipped, run `git diff --stat origin/<default>...<branch>` and say whether its changes are already present; ask the user either way.
- A ticket guess is just the first number in the branch name. Confirm it against the PR or issue before writing it to a card.
- Draft cards are the user's call; never mark adopted work `running` or `done` yourself.
- Git-ignored data folders (Poot's `data/`) exist only in the main checkout; a new worktree won't have them. Mention it when moving work that needs them.
