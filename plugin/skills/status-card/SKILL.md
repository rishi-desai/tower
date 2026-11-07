---
name: status-card
description: Write or update this worktree's status card in the ledger. Use whenever you finish a chunk of work, stop to wait on the user, get blocked, or open/update a PR — in any repo. The hub reads these cards instead of transcripts.
effort: medium
---

# Status card

Every worktree keeps one short card describing where its work stands. A separate hub session reads all cards to tell the user what needs them, so the card is the only thing that leaves this session. Write it for someone who has not seen this conversation.

## How to write it

You write only the prose. tower's core fills in the rest (status, ticket, branch, PR, base branch, time) from git and GitHub and writes the card into the user's notes vault, outside the repo. Pipe the sections into the core, from inside the repo; `<plugin>` is two folders up from this skill's base directory (use its absolute path):

```
python3 -I <plugin>/scripts/run-tower.py card write [--status done|running|needs-you|blocked] [--reason "<one line>"] <<'EOF'
## Done
- Up to 3 bullets in plain English: what now works that didn't before.

## Why it matters
- 1-2 lines on how this fits the bigger system or the ticket's goal.

## Needs you
- "Nothing." — or one decision, phrased as a question, with options:
  A) ... (recommended: why)
  B) ... (trade-off)

## Verify
- The one command, URL, or click that proves it works.

## Words used
- Define any acronym or project term above that isn't in the repo's GLOSSARY.md (leave the section out if none).
EOF
```

It prints the card's path and the status it chose, with why. If it exits 1, it wrote nothing: fix what it says and run it again.

## Status

- Without `--status`, the core decides from this branch's PR: no PR → `running`, open → `waiting-ci`, merged → `done`.
- `--status done` when the PR is open and only waits on the user's merge, with nothing left for you. Without a PR it refuses.
- `--status needs-you --reason "..."` only when work truly cannot continue without the user. Then ask the question with the AskUserQuestion tool (the pop-up), recommended option first and labelled "(Recommended)". Don't leave it only in your final text: the user answers in the pop-up, and the tower pane shows the card meanwhile. When they answer, a hook switches the card back to `running`.
- `--status blocked --reason "..."` when waiting on something other than the user or your own PR (another ticket, a teammate, an outage).
- `--status running` while you fix a failed check or review on an open PR, so the card doesn't say `waiting-ci` while you work.
- The ticket is guessed from the branch (`feat-42-x` → #42) or kept from the last card. Pass `--ticket 42` (or `--ticket none`) when that's wrong.

## Decisions log

Your context can be compacted or cleared mid-ticket. Afterwards, tower gives you back the hub's kickoff, your card and your decisions log, and nothing else from this conversation. So whenever you decide something a later you would otherwise redo or undo, or find out something that took digging, add one line right then, not when you stop:

- a choice between real alternatives: "chose SQLite FTS over a LIKE scan because the feed has 40k rows"
- a discovery: "the auth middleware already exists in src/auth; reuse it"
- an answer from the user: "user picked B: keep the old endpoint until v2"

```
python3 -I <plugin>/scripts/run-tower.py decisions add "chose X over Y because Z"
```

One line, at most 300 characters. Lines are only ever added: if a decision changes, add a new line saying so ("switched from X to Y: Z broke"). Skip what the code, a commit message or the card already says.

The card's `## Kickoff` section is the hub's kickoff prompt word for word. Leave it out of what you send: `card write` keeps it, and refuses a `## Kickoff` section from you.

## Rules

- Under ~30 lines. If you need more, the detail belongs in the PR body; link it.
- Name things so a reader of the card alone can follow it: "#44 (the theme and mascot ticket)", not "#44". The hub repeats card text to the user.
- The card is the short version for the hub. Your final chat message is the full version for the user: same facts, written out in the report format from your CLAUDE.md (What happened, Done, Why it matters, Needs you, Verify), without the card's length limit.

## Gotchas

- If it says `projects_dir` is missing, tell the user to run tower's `setup.sh` on this machine. Don't write the card anywhere else; the hub wouldn't find it.
- If it warns that GitHub couldn't be asked, the card says `pr: unknown` and stays `running`; mention it in your final message.
- Don't commit the card. It lives outside the repo on purpose.
