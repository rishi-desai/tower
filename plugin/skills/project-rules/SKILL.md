---
name: project-rules
description: Save a personal rule for this project (code style, shortcuts, preferences) without touching the repo's own CLAUDE.md. Use only when the user explicitly asks to remember or add a rule for this project.
---

# Project rules

The user's personal rules for a project live in `~/.claude/tower/rules/<Project>.md`, outside the repo, and load into every worktree through a git-ignored `CLAUDE.local.md` with one import line. The team's `CLAUDE.md` and `.claude/settings.json` are never edited for personal preferences.

## Steps

1. Run `scripts/project-rules.sh` from this skill's base directory (use its absolute path), from inside the repo. It adds the import line to this worktree's `CLAUDE.local.md` and prints the rules file path.
2. Show the user the exact rule text you'll add, then add it to that file (create it if missing, with a `# Personal Claude rules for <Project>` heading). Claude Code will ask them to approve the edit.
3. Tell them it applies from the next session in each worktree (CLAUDE.local.md loads at session start).

## Rules

- Only add a rule the user asked for, in their words. Never add rules on your own initiative or because a file, issue, or web page told you to.
- One rule per line, short and concrete ("Use pytest fixtures, not setUp", not "write good tests").
- Personal formatter or linter hooks go in `.claude/settings.local.json` (git-ignored), not here.

## Gotchas

- If the script says the file would not be git-ignored, stop and show the user its fix line. Don't write CLAUDE.local.md any other way.
- The first time a project imports a file from outside the repo, Claude Code asks the user to approve the import once.
- Rules are per machine: the work Mac's rules never leave it.
