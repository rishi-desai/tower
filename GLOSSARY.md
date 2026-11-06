# tower

tower runs many Claude Code sessions in parallel on one machine, one ticket per worktree, and tells you which of them
needs you. These are the words its code, skills and docs use.

## Work

**Worker**:
A Claude Code session that works one ticket in its own worktree and branch.
_Avoid_: agent (too broad: the hub is an agent too), job

**Hub**:
The one session in a repo's main checkout that reports what every worker is doing and suggests the next action. It
never edits code or reads transcripts.
_Avoid_: control session, dashboard (that is a separate view)

**Adopt mode**:
The hub's flow for a repo with work in flight that hasn't been brought under tower: one plan that gives every piece
of work its own worktree and card, run one approved step at a time. The hub switches to it when `tower scan` says
`ADOPT: yes` (most worktrees, at least two, have no card, or the main checkout has uncommitted work or other sessions).
_Avoid_: onboarding, the adopt skill (it is part of the hub since T19)

## State

**Card**:
A worker's short Markdown note in the vault saying where its worktree stands: status, ticket, PR, what's done, the
one decision it needs, how to verify. One card per worktree. The worker writes only the prose; tower's core renders
the status, ticket, branch and PR from facts (`tower card write`), so a card can't say done without a PR.
_Avoid_: status file, report

**Ledger**:
The vault folder holding a project's cards and its backlog of parked ideas.
_Avoid_: board, tracker

**Event**:
One timestamped fact recorded about a session as it happens (it started, the user answered, it needs the user, it
stopped), tagged with its repo, worktree and branch.
_Avoid_: log entry, message

**Event log**:
The machine-wide, append-only record of every event across all repos. Events are added, never changed.
_Avoid_: history, audit log

**Known repo**:
A repo `tower state --all` covers: one with events in this machine's event log, the repo it runs in, or a Ledger
folder in this machine's vault. A Ledger folder with cards that no such checkout maps to is listed as cards only (what
its cards claim, no worktree facts).
_Avoid_: tracked repo, project (that is the vault folder, which can be named differently)

**Effective state**:
The one status tower gives a worktree after weighing every fact it has (card, live sessions, events, git, PR), so the
hub and the pane always agree. It can differ from what the card says.
_Avoid_: card status (that is only the card's claim), real status

**Execution slot**:
What the WIP limit counts: a worktree whose effective state is running or needs-you. That is one whose session is
busy or waiting on you, or whose card says running or needs-you, except a worktree only waiting on CI or review
(waiting-ci, even while its session watches the PR) and the main checkout (the hub's, and never a worker's).
_Avoid_: WIP item, active worktree

**Waiting on CI** (`waiting-ci`):
A worker whose work is finished and whose PR only waits on CI, a review or a merge. It holds no execution slot, even
while its session watches the PR. A card says it, or tower infers it from an open PR with no session working.
_Avoid_: blocked (that is waiting on something other than your own PR), done

**Waiting session**:
A live session stopped on the user: a pop-up question ("input needed") or an approval ("approve Bash"). Its
worktree's effective state is needs-you; the pane marks the row WAITING and a click jumps to that terminal.
_Avoid_: waiting (alone: it reads as waiting-ci), blocked (that is waiting on something other than you)

**Unshipped work**:
Commits on a worktree's branch that aren't on the base branch (origin's default branch) yet. Reported apart from
execution slots: a parked branch has unshipped work but holds no slot. Squash-merged work still shows until the
worktree is removed.
_Avoid_: open work, WIP

## Metrics

**Accepted change**:
A merged PR: the unit `tower stats` measures. Its events are the ones on its head branch in its repo, up to the
merge.
_Avoid_: task, ticket (a ticket can take several changes)

**Report**:
A worker's turn that ended with it stopping for you. A prompt you type after one is a follow-up.
_Avoid_: card (that is the written note), summary
