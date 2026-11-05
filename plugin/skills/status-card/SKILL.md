---
name: status-card
description: Write or update this worktree's status card in the ledger. Use whenever you finish a chunk of work, stop to wait on the user, get blocked, or open/update a PR — in any repo. The hub reads these cards instead of transcripts.
effort: medium
---

# Status card

Every worktree keeps one short card describing where its work stands. A separate hub session reads all cards to tell the user what needs them, so the card is the only thing that leaves this session. Write it for someone who has not seen this conversation.

## Where it goes

Run `scripts/card-path.sh` from this skill's base directory (use its absolute path), from inside the repo. It prints the card's path (in the user's notes vault, outside the repo). Overwrite that file each time; it is the current state, not a log.

Get the timestamp from `date '+%Y-%m-%dT%H:%M'`. Don't write a time from memory.

## Format

The frontmatter block at the top is parsed by the hub's script and shown as properties in Obsidian. Keep its keys and shape exactly; quote values that start with `#`.

```
---
status: running | needs-you | blocked | done
ticket: "#42"            # or none
updated: 2026-11-04T14:30
branch: feat/42-discord-tracker
pr: "#57"                # or none
---

## Done
- Up to 3 bullets in plain English: what now works that didn't before.

## Why it matters
- 1-2 lines on how this fits the bigger system or the ticket's goal.

## Needs you
- "Nothing." — or one decision, phrased as a question, with options:
  A) ... (recommended: why)
  B) ... (trade-off)

## Verify it yourself
- The one command, URL, or click that proves it works.

## Words used
- Define any acronym or project term above that isn't in the repo's GLOSSARY.md (skip if none).

## Files touched
- path/one, path/two
```

## Rules

- Under ~30 lines. If you need more, the detail belongs in the PR body; link it.
- `needs-you` only when work truly cannot continue without the user. Otherwise keep working and leave `running`.
- When you need a decision: write the card first (status `needs-you`, the question and options under Needs you), then ask it with the AskUserQuestion tool (the pop-up), recommended option first and labelled "(Recommended)". Don't leave the question only in your final text: the user answers in the pop-up, and the tower pane shows the card meanwhile.
- `blocked` means waiting on something other than the user (CI, another ticket, a teammate). Say what in Needs you.
- `done` means merged or ready for the user to merge, with the PR number in `pr:`.
- Write the same content as your final chat message too, so the user sees it in this session as well.

## Gotchas

- Run the script from inside the repo; outside git it fails.
- If the script says `projects_dir` is missing, tell the user to run tower's `setup.sh` on this machine. Don't write the card anywhere else; the hub wouldn't find it.
- Don't commit the card. It lives outside the repo on purpose.
- `ticket: #42` without quotes is a YAML comment, which leaves the field empty in Obsidian. Always quote it.
