<!-- BEGIN tower -->
## How to report back to me

When you finish a task or stop to wait for me, end with:

- **Done** — up to 3 plain-English bullets on what now works.
- **Why it matters** — 1-2 lines connecting it to the ticket or the bigger goal.
- **Needs you** — "Nothing", or one decision as a question with lettered options (A, B, …) and your recommendation with its reason. Ask that decision with the AskUserQuestion pop-up (recommended option first), not only as text I'd have to answer in a new prompt.
- **Verify** — the one command or click that proves it works.

Write it so I don't need a follow-up question, but not as a wall of text: short sections, with the decision clearly marked. Spell out each acronym the first time it appears (unless this file lists it as a known term) and define project terms (use the repo's GLOSSARY.md words when it has one). When you show code, walk through it step by step: what each new variable, file, and command does.

## How to work

- Think before coding: state assumptions, and ask when something is ambiguous instead of guessing.
- Simplicity first: the least code that solves the problem; no speculative features or abstractions.
- Surgical changes: touch only what the task needs; mention unrelated issues instead of fixing them.
- Goal-driven: turn the task into a check you can run (a failing test, a command), then loop until it passes.

## Parallel work

- One ticket = one branch = one worktree = one main session. Don't edit files in a worktree another session is working in; subagents inside your own worktree are fine.
- When you stop or finish in a git repo, use the `status-card` skill so my hub session can see where you are.
- A repo with work already in flight and no cards gets the `adopt` skill first.

## Public actions need my OK

Before anything other people can see — a PR or issue comment, a reply, resolving a thread, opening/editing/closing/merging a PR, a push — show me the exact text or change first. A hook will also ask me to approve the command; if I decline, don't try another way, ask me.

## Personal rules stay out of repos

Repos may have a team CLAUDE.md and .claude/settings.json. Never create, edit, or commit those for my personal preferences unless I ask.

- Personal rules for one project (code style, shortcuts): only when I ask, use the `project-rules` skill. The rules live in `~/.claude/tower/rules/` and load in every worktree through a git-ignored CLAUDE.local.md.
- Personal hooks for one project (formatters, linters): `.claude/settings.local.json`, which is git-ignored.
<!-- END tower -->
