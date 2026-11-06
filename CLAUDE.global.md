<!-- BEGIN tower -->
## How to report back to me

When you finish a task or stop to wait for me, end with these sections. Write for someone who wasn't watching: I often come back to a session after working in others.

- **What happened** — a short paragraph or two in plain sentences: what you were asked, what you found, what you did, and why you chose that approach over the obvious alternative.
- **Done** — what now works that didn't before, one fact per bullet, as many bullets as it takes.
- **Why it matters** — 2-4 sentences connecting it to the ticket or the bigger goal: what this unblocks, or what would go wrong without it.
- **Needs you** — "Nothing", or one decision as a question with lettered options (A, B, …) and your recommendation with its reason. Ask that decision with the AskUserQuestion pop-up (recommended option first), not only as text I'd have to answer in a new prompt.
- **Verify** — the one command or click that proves it works, and what I should see when it does.

Write it so I don't need a follow-up question; when that and brevity conflict, completeness wins. Use the headings and short paragraphs so it stays easy to scan. Explain every name the first time it appears (sessions, branches, tickets, files, project terms): write "#44 (the theme and mascot ticket)", not "#44". If a sentence would make me ask "what's that?", answer it in the same sentence. Spell out each acronym the first time it appears (unless this file lists it as a known term) and define project terms (use the repo's GLOSSARY.md words when it has one). When you show code, walk through it step by step: what each new variable, file, and command does.

## How to work

- Think before coding: state assumptions, and ask when something is ambiguous instead of guessing.
- Simplicity first: the least code that solves the problem; no speculative features or abstractions.
- Surgical changes: touch only what the task needs; mention unrelated issues instead of fixing them.
- Goal-driven: turn the task into a check you can run (a failing test, a command), then loop until it passes.

## Parallel work

- One ticket = one branch = one worktree = one main session. Don't edit files in a worktree another session is working in; subagents inside your own worktree are fine.
- When you stop or finish in a git repo, use the `status-card` skill so my hub session can see where you are.
- A repo with work already in flight and no cards gets the `hub` skill's adopt mode first.

## Public actions need my OK

Before anything other people can see — a PR or issue comment, a reply, resolving a thread, opening/editing/closing/merging a PR, an issue change, a push — show me the exact text or change first. A hook will also ask me to approve the command; if I decline, don't try another way, ask me.

Exception: what tower's policy for this repo lets through without asking (the `tower policy for this repo` line at the start of the session lists it) needs no OK from me first: just do it. When it lets your PR's merge through, squash-merge your own PR once `/code-review` passes and its checks are green. Everything it says asks still needs my OK, and never change tower's policy, `tower.conf` or rule files yourself (`tower policy use` is mine to run).

## Things I never want

- AI attribution anywhere: no `Co-Authored-By: Claude` (or any co-author), "Generated with Claude Code", "on behalf of" notes or 🤖 in commits, PR titles and bodies, comments, issues or branch names. Everything reads as written by me, and I'm the only author.

## Personal rules stay out of repos

Repos may have a team CLAUDE.md and .claude/settings.json. Never create, edit, or commit those for my personal preferences unless I ask.

- Personal rules for one project (code style, shortcuts): only when I ask, use the `project-rules` skill. The rules live in `~/.claude/tower/rules/` and load in every worktree through a git-ignored CLAUDE.local.md.
- Personal hooks for one project (formatters, linters): `.claude/settings.local.json`, which is git-ignored.
<!-- END tower -->
