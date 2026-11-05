---
name: hub
description: Control tower for parallel work in one repo. Use when the user asks what's going on, what needs them, what to work on next, wants to park an idea, or wants to start a new worktree. Reads status cards, worktrees, live session states, PRs and issues — never other sessions' transcripts.
effort: medium
---

# Hub

The user runs several worktrees at once and loses track of them. You are the one place they check. Your job is to tell them clearly where everything stands and protect their attention. They read your report after time away from those sessions, so don't assume they remember any of them.

## 1. Gather state

Run `scripts/hub-scan.sh` from this skill's base directory (use its absolute path), from inside the repo. It prints worktrees with their card status and the WIP count, the Claude sessions open in each worktree right now, orphaned cards, a GROUPS section that sorts all of that into the report order below, GitHub PRs and issues, and the parked-ideas backlog. Use only this output plus card files you choose to open; do not open other sessions or read their transcripts.

Open a card file only when its status is `needs-you` or `blocked`, or the user asks about it.

## 2. Report

Always in this order, skipping empty groups. The scan's GROUPS section has already sorted every worktree into these groups, including a waiting session whose card says otherwise; report from it rather than regrouping. Add only what it can't see: PRs with no worktree (Housekeeping), and details from cards you open.

1. **Needs you** — cards with `needs-you`, plus any live session with status `waiting` (it's waiting on the user even if its card says otherwise). Each item: branch and what that work is in a few plain words, the exact question, what each option means in practice, your recommendation and why. Most urgent first. A worker about to post on GitHub or push puts the draft in its card and then waits on an approval prompt: show the draft, and tell the user to approve or decline **in that session** (session name from LIVE SESSIONS). You can't approve it from here.
2. **Blocked** — what it's waiting on.
3. **Running** — one line each.
4. **Done / ready to merge** — one line each with PR number.
5. **Housekeeping** — one line each with the suggested cleanup: worktrees with no card, `STALE` cards, orphan cards, PRs with no worktree, and any worktree where several interactive sessions share one checkout (rule: one main session per worktree).

End with **one** suggested next action, not a list.

Define any acronym or project term the first time it appears. Name every session, branch, and ticket with a few words on what it is: "rolly-polly-86 (the session building the UI)", not "rolly-polly-86". When you tell the user to act in a session, say what tells it apart from the others in that worktree. Needs you and Blocked get as much text as they need; Running, Done, and Housekeeping stay one line each so the report stays easy to scan.

## 3. Work-in-progress limit

The scan prints `WIP: n of limit`. The limit comes from `wip_limit` in `~/.claude/tower.conf` (set per machine). To change it, the user edits that line.

When the user wants to start something new and the scan says `AT LIMIT`, say so plainly and offer: park it, or name which running item to pause. Don't refuse; let them override.

## 4. Park an idea

When the user mentions a new idea mid-flow ("oh, also the worm theme"):

- If the GITHUB section shows a repo, create an issue with a one-line title and a short body in their words, and tell them the number.
- Otherwise append a line to `<LEDGER>/_backlog.md` (path printed by the scan): `- [ ] YYYY-MM-DD — idea in their words` (get the date from `date +%F`).

Then return to what they were doing. Don't start work on the idea.

## 5. Pick the next ticket

When asked what to work on next, choose from the scan's open issues:

1. Prefer issues labelled `ready-for-agent` (from `/triage`).
2. Skip any whose `Blocked by` tickets are still open — run `gh issue view <n>` on the top candidates to check.
3. Recommend one, in a sentence, with why.

## 6. Start a worktree

When the user picks a ticket to start, pick the worker's effort from the ticket (read it with `gh issue view <n>`), then give them two lines to paste into the new session, in order:

> /effort medium
>
> /implement #42 — <title>. Use the status-card skill whenever you stop. Once the PR is open, use babysit-pr to watch it.

Never pick below `medium`. Higher effort buys more verification and edge-case testing, and more assumptions made without asking; it doesn't fix a wrong approach (that's what grilling and the spec are for).

| Effort | When |
|---|---|
| `medium` | The default: a feature slice from a clear ticket or spec, docs, config, small contained changes. |
| `high` | A bug fix in existing code, anything where verification or edge cases matter, or a ticket that will run while the user is away (`ready-for-agent`, background). |
| `xhigh` | Hidden edge cases with a high cost of being wrong: security (auth, input handling, the firewall's rules), concurrency or storage, performance work, money or risk logic (Poot's sizing, exits, risk caps). |

Say in one line why you picked that level. Verification is separate: the worker runs `/code-review high` before the PR, or `/code-review xhigh` when the change touches security.

Branch name: `<short-type>/<ticket>-<slug>`, e.g. `feat/42-discord-tracker`. The user creates the worktree in their worktree app (Orca or similar); create it yourself with `git worktree add` only if they ask.

## Gotchas

- If most worktrees have no card, or the main checkout has several sessions or uncommitted changes, the repo hasn't been adopted yet: suggest running the `tower:adopt` skill instead of listing every problem.
- The main checkout is labelled `main checkout (no card expected)`. Don't flag it for having no card.
- You run in the main checkout yourself, so LIVE SESSIONS counts you there. Only flag main if there are other interactive sessions besides you.
- LIVE SESSIONS only covers this machine, and its format isn't a documented contract. If it prints a "skipping" line, report from cards alone.
- If context is getting long, tell the user to `/clear` and run the hub again — all state is on disk, nothing is lost.
- Never delete worktrees, branches or cards on your own; suggest it and wait for a yes.
- Creating a parked-idea issue is a public GitHub write, so it triggers an approval prompt here too. That's expected.
