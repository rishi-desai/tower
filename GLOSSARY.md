# tower

tower runs many Claude Code sessions in parallel on one machine, one ticket per worktree, and tells you which of them
needs you. These are the words its code, skills and docs use.

## Work

**Worker**:
A Claude Code session that works one ticket in its own worktree and branch, or a whole stack of tickets in one
stack worktree, one branch per ticket.
_Avoid_: agent (too broad: the hub is an agent too), job

**Hub**:
The one session in a repo's main checkout that reports what every worker is doing and suggests the next action. It
never edits code or reads transcripts.
_Avoid_: control session, dashboard (that is a separate view)

**Stack**:
PRs whose bases chain (feat2 → feat1 → main), submitted as one stack (git-spice or `gh stack` do this; PRs created one
by one stay separate), which GitHub then groups into a stack of its own. GitHub merges a stacked PR only through its
asynchronous merge API, and merging one merges every unmerged PR below it too, onto the stack's
base (the branch the bottom PR targets). Workers merge a stack with `tower merge`, from the bottom, one PR at a time.
_Avoid_: train, dependent PRs, chain (that is chained PRs)

**Stack worktree**:
The one worktree that holds a whole stack, worked by one worker, who moves between the stack's branches in it. A stack
is sequential (an upper PR can't land before the one below it), so it is one stack = one worktree, not one per
branch.
_Avoid_: stack branch worktree, per-branch worktrees

**Parent**:
The branch a stacked branch is built on: the one below it in its stack, or the stack's base for the bottom branch.
It is where the branch's PR is based, and what the card's `base:` says before there is a PR.
_Avoid_: base (alone: it reads as the repo's base branch), upstream (git's word for the remote branch)

**Needs restack**:
A stacked branch whose parent moved under it: the parent was rebased or amended (it has commits the branch doesn't),
or the parent merged. The branch still holds the old parent until its worker restacks it. A bottom branch is never
flagged for the stack's base moving on: GitHub merges it all the same.
_Avoid_: stale, out of date, behind (that is unshipped work)

**Next to merge**:
The lowest branch of a stack that hasn't merged and sits on the stack's base or on a merged parent: the only PR of
the stack that may merge now. Its merge waits for you when the policy key for where it lands is ask.
_Avoid_: bottom PR (once lower PRs merge, the next one isn't the original bottom), head of the stack

**Chained PRs**:
PRs whose bases chain (feat2 → feat1 → main) without being a stack, as when each is opened on its own. GitHub moves
the PR above a merged one onto the next base only when it deletes the merged branch itself; deleting that branch
through the API (as `gh pr merge --delete-branch` does) closes the PR above instead. `tower merge` uses a merge commit
for them and keeps the branch until the PR above is moved.
_Avoid_: plain chain, stack (GitHub doesn't group them)

**Adopt mode**:
The hub's flow for a repo with work in flight that hasn't been brought under tower: one plan that gives every piece
of work its own worktree and card, run one approved step at a time. The hub switches to it when `tower scan` says
`ADOPT: yes` (most worktrees, at least two, have no card, or the main checkout has uncommitted work or other sessions).
_Avoid_: onboarding, the adopt skill (it is part of the hub since T19)

**Cleanup**:
Removing a finished worker once every check proves nothing would be lost: its PR merged at the worktree's HEAD,
nothing uncommitted or stashed, no session busy or waiting there. It closes the worktree's terminals, removes the
worktree and its branch (locally and on GitHub) and archives its card. A worktree that fails a check is left exactly
as it was.
_Avoid_: prune, teardown, delete (alone: the card is archived, not deleted)

**Half-done cleanup**:
A cleanup that stopped after its worktree was removed, leaving the branch, the card or the pull undone. Naming the
branch to clean up again finishes it, once the checks a missing worktree can still fail pass (its PR merged at the
branch's head, nothing stashed on it, its card not saying work goes on).
_Avoid_: partial cleanup, orphan card (an orphan card is any card without a worktree; a half-done cleanup is one cause)

## State

**Card**:
A worker's short Markdown note in the vault saying where its worktree stands: status, ticket, PR, what's done, the
one decision it needs, how to verify. One card per worktree. The worker writes only the prose; tower's core renders
the status, ticket, branch and PR from facts (`tower card write`), so a card can't say done without a PR.
A card is named after its worktree's branch, or its commit on a detached HEAD, when it is first written; that name
isn't the worktree's label. The card stays with its worktree when the worktree moves to another branch (a stack
worktree's worker moves between its branches), keeping its first name.
_Avoid_: status file, report

**Ticket guess**:
The ticket tower reads from a branch name (`feat-42-x` → #42), the same guess wherever tower shows it. It can miss or
be wrong, so it is a guess, never a fact.
_Avoid_: ticket number (a branch can have digits that aren't one)

**Issue in progress**:
An open issue that a worker's card names as its ticket. It stays open on GitHub until its PR merges, but it is no
longer there to start or discuss: the pane shows which worktree has it and in what effective state. A card's ticket
often starts as the branch's ticket guess, and the worker corrects it when the guess is wrong; a worktree without a
card is on no issue, whatever its branch name says.
_Avoid_: started issue, claimed issue, assigned (GitHub's own word for a person on an issue)

**Worktree's PR**:
A PR from this repo whose head branch has the worktree's branch name. A fork's PR is never it, even with the same
branch name (a stranger's patch-1 isn't this work).
_Avoid_: PR by branch name (a fork's PR can have the same branch name)

**Kickoff**:
The hub's first prompt to a worker: which skill to start with, the two reviews and their ranges, and how to ship. Kept
word for word as a fixed part of the worker's card, so it comes back after compaction; the worker can't reword or drop
it.
_Avoid_: prompt (alone: every message is one), instructions

**Decisions log**:
A worktree's append-only list of what its worker decided or found out, one dated line each ("chose X over Y because Z",
"the auth middleware already exists in src/auth"). Lines are added, never changed; unlike the card, it is never stale.
_Avoid_: notes, history (that is the event log), memory (that is worker memory, all three together)

**Worker memory**:
What a worker gets back when its context restarts (compaction, `/clear`, a new session on its worktree): its kickoff,
its current card and its decisions log.
_Avoid_: context (Claude Code's word for the whole window), state (that is the effective state)

**Ledger**:
The vault folder holding a project's cards, their decisions logs and its backlog of parked ideas.
_Avoid_: board, tracker

**Machine config** (`tower.conf`):
This machine's own settings, written by setup.sh: where the vault's projects live, which project folder a repo maps
to, the worktrees folder, the WIP limit. One that can't be read, or isn't UTF-8 text, is broken, never mistaken for a
machine that was never set up: tower says why wherever it needed it.
_Avoid_: config (alone: it reads as runtime config), settings

**Archived card**:
The card of a worktree that cleanup removed, kept in the Ledger's `_archive` folder so the history stays. It is no
longer any worktree's card and is never an orphan card.
_Avoid_: old card, deleted card

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

**Overlap**:
Two worktrees that changed the same file, found from git: each branch's own commits (not on the other branch, not on
the base branch) plus its uncommitted and untracked files. The hub lists each overlap under Housekeeping with the
files, so the two workers can coordinate before either merges.
_Avoid_: conflict (git's word for a merge that failed; an overlap only says one might)

## Runtime

**Runtime slot**:
A worktree's stable number for sharing one machine: 0 for the main checkout, the smallest free number for any other
worktree, kept until its folder is gone. A project's runtime config turns it into ports and names (`PORT` =
`port_base` + slot), so two workers' dev servers never clash.
_Avoid_: slot (alone: it reads as execution slot, what the WIP limit counts), port number

**Runtime config**:
A project's resources, declared once outside the repo in `~/.claude/tower/projects/<Project>.toml`: base ports, a
database name, other variables, and the seed command for what only the main checkout has.
_Avoid_: env file, .env (those live in the repo)

## Metrics

**Accepted change**:
A merged PR: the unit `tower stats` measures. Its events are the ones on its head branch in its repo, up to the
merge.
_Avoid_: task, ticket (a ticket can take several changes)

**Report**:
A worker's turn that ended with it stopping for you. A prompt you type after one is a follow-up.
_Avoid_: card (that is the written note), summary

## Approvals

**Policy**:
Which of an agent's actions ask you first, repo by repo: the profile each repo gets, from `~/.claude/tower/policy.toml`
(`tower policy` shows it). The approval hook enforces it.
_Avoid_: settings, permissions (Claude Code's own allow rules are a separate thing)

**Gate**:
One kind of action the approval hook may stop for your approval (a push, a merge, a comment, an issue write); each is
judged by one key of the repo's profile, or, for what no key covers, asks unless every GitHub key is auto.
_Avoid_: check, permission

**Known read**:
A `gh` command the approval hook knows only reads GitHub or works locally (viewing, listing, cloning, checking out a
PR). Every other `gh` command counts as a GitHub write, including one a newer `gh` adds.
_Avoid_: safe command, read-only command

**Lands on**:
The branch a merge puts a PR's commits on, which merge_to_main and merge_to_branch judge: the PR's base, or for a
stacked PR the stack's base. A PR based on feat1 in a stack on main lands on main.
_Avoid_: target, into (alone: a stacked PR's base isn't where it lands)

**Profile**:
A named set of the policy's keys (push, merge_to_main, merge_to_branch, comments, issue_writes, pr_writes, cleanup,
wip_limit): the built-in work, personal or sandbox, or one the policy file defines. Each repo gets exactly one.
_Avoid_: mode, level, preset

**Safety floor**:
What asks in every profile, sandbox included: an agent's change to the policy file, tower.conf, a rule file, a
CLAUDE.local.md or Claude Code's settings, running `tower policy use` or tower's setup.sh, writing setup.sh or the
policy module, or turning tower's plugin off. It is how an agent is kept from loosening its own rules. It covers the
files tower really reads, wherever a path to them leads, and only those: a mention of a name (in a commit message) or
a file that only shares the name (a test fixture) isn't a change to them, and plainly reading setup.sh isn't running
it.
_Avoid_: hard rule, override

## Health

**Doctor check**:
One thing tower depends on, checked by `tower doctor` and reported with the fix when it isn't right. A failing check
is a **problem** (tower can't work as it should); one is only a **warning** when the hub has a fallback (Orca down) or
the fact can't be known for sure (no event in the last hour, no policy file).
_Avoid_: health check, test, diagnostic

**Right account**:
The GitHub account gh is logged in as, when it can push to the repo's origin: the one workers push, open PRs and
merge as.
_Avoid_: active account (gh's word: active can still be the wrong one), user
