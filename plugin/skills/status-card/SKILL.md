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
status: running | needs-you | waiting-ci | blocked | done
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
- `waiting-ci` means your work is finished and the PR only waits on CI, a review or a merge, with nothing else for you to do, even while you watch it (babysit-pr, `pr-wait`). Put the PR in `pr:`. It doesn't take a WIP slot, so don't leave such a card on `running`. If CI fails or a review asks for changes, go back to `running` while you fix it.
- `blocked` means waiting on something other than the user that isn't your own PR's CI or review (another ticket, a teammate, an outage). Say what in Needs you.
- `done` means merged, or ready for the user to merge with nothing left to wait for, with the PR number in `pr:`.
- The hub doesn't take a card at its word: a session waiting on the user shows as needs-you, and a busy session beats a needs-you card (the user already answered). Keep the card current anyway; it's what the hub repeats to the user.
- Name things so a reader of the card alone can follow it: "#44 (the theme and mascot ticket)", not "#44". The hub repeats card text to the user.
- The card is the short version for the hub. Your final chat message is the full version for the user: same facts, written out in the report format from your CLAUDE.md (What happened, Done, Why it matters, Needs you, Verify), without the card's length limit.

## Gotchas

- Run the script from inside the repo; outside git it fails.
- If the script says `projects_dir` is missing, tell the user to run tower's `setup.sh` on this machine. Don't write the card anywhere else; the hub wouldn't find it.
- Don't commit the card. It lives outside the repo on purpose.
- `ticket: #42` without quotes is a YAML comment, which leaves the field empty in Obsidian. Always quote it.
