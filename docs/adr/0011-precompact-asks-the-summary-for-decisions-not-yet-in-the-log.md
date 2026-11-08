# PreCompact asks the summary for decisions not yet in the log, and leaves the /compact focus to Claude Code

ADR 0008 gives a worker its decisions log back after compaction, but a decision it made and never logged is only
summarised with the rest of the conversation, and can be lost (#97). A PreCompact hook (`precompact-decisions.py`, on
`manual` and `auto`) now prints instructions for the compaction summary: keep, word for word under a heading "Not yet
in the decisions log", every decision or discovery in the conversation that isn't in the log, and the exact next step.
The log follows the request (its newest lines under the 10,000-character cap, naming the file for the rest) so the
summary can tell what's already kept; the kickoff and card aren't sent, since they come back whole either way. It
prints nothing in the main checkout, for a worktree with no log written since it was made (a card isn't needed: a
worker can log before its first card), or with a log it can't read, and any error exits 0 quietly: a blocked compaction can fail the request (docs/research/precompact-hook.md).

**The user's `/compact <focus>` isn't repeated.** The hooks docs don't say whether PreCompact stdout replaces that focus
or joins it. Claude Code 2.1.304 (the version mise pins) joins them: its compaction code builds the summary
instructions as `focus + "\n\n" + hooks' stdout`, every PreCompact hook's stdout joined with blank lines, so the focus
always comes first and the hook's text adds to it. Repeating the focus would only say it twice. The request says
"besides any focus the user gave" so the summary doesn't read it as replacing the focus. This is Claude Code's
behaviour, not its documented contract, so recheck it when mise's Claude Code pin moves.

After compaction, the worker-memory hook tells the worker to add what the summary kept under that heading to the log
with `tower decisions add`, so it's kept word for word at the next compaction too, not only summarised once.

## Considered options

- **Repeat `custom_instructions` at the top anyway**, in case a later Claude Code replaces the focus: safe either way,
  but the summary sees the focus twice on every `/compact`. A release that replaces the focus reopens this choice.
- **Read the unlogged decisions out of `transcript_path` and append them to the log**: no reliance on the summary, but
  it means judging free text for decisions, and the ticket keeps automatic logging out of scope.
- **Speak even with no log**: a worker that never logged has the most to lose, but the ticket asks for the
  worker-memory hook's quiet cases, and without a log there's nothing for "not yet in the log" to compare against.

## Consequences

- Unlogged decisions come back as part of the summary, not the log, until the worker adds them as it's told to.
- The log shown before compaction can be longer than what the worker-memory hook gives back after it (that output also
  holds the kickoff and card), so lines between the two cuts are only in the file, which the hook names.
