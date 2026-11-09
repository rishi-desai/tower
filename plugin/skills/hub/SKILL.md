---
name: hub
description: Control tower for parallel work in one repo, or across every repo on this machine. Use when the user asks what's going on, what needs them (here or across everything), what to work on next, wants to discuss or park an idea, wants to start a new worktree, or wants to bring a repo with work already in flight under tower (adopt it). Reads status cards, worktrees, live session states, PRs and issues — never other sessions' transcripts.
effort: medium
---

# Hub

The user runs several worktrees at once and loses track of them. You are the one place they check. Your job is to tell them clearly where everything stands and protect their attention. They read your report after time away from those sessions, so don't assume they remember any of them.

## 1. Gather state

Run `python3 -I <plugin>/scripts/run-tower.py scan` from inside the repo, `<plugin>` being two folders up from this skill's base directory (use its absolute path). It prints every worktree with its effective state (`status:`, what tower decided from the card, live sessions, the event log and git; `card:` is what the card itself says; `reason:` is why), the WIP count and unshipped work, the Claude sessions open in each worktree right now, orphaned cards, a GROUPS section that sorts all of that into the report order below, GitHub PRs and issues, and the parked-ideas backlog. The pane draws the same statuses from `tower state --json`, so never re-decide a worktree's status yourself. Use only this output plus card files you choose to open; do not open other sessions or read their transcripts.

Open a card file only when its status is `needs-you` or `blocked`, or the user asks about it. Open it at `<LEDGER>/<card_file>`: `<LEDGER>` is the scan's `LEDGER:` line and `<card_file>` the row's `card_file:`; never build the name from the label.

**Adopt mode.** The `ADOPT:` line after `Policy:` says whether the repo has been brought under tower yet. If it says `yes` (most worktrees, at least two, have no card, or the main checkout has uncommitted work or other interactive sessions), don't list every problem in a report: give only the scan's NEEDS YOU and BLOCKED groups (section 2's first two; a waiting worker can't wait for adopt), then follow [ADOPT.md](ADOPT.md) (scan, one plan, approved steps one at a time) instead of the rest of section 2. Follow it too when the user asks to adopt the repo, whatever the line says. If they asked for something else (a ticket to start, an idea to park), say in one line that the repo isn't set up yet and offer adopt mode first; do what they asked if they decline. If they say the reasons are on purpose (a local config change kept on main, a second session they keep there), give the usual report from then on in this conversation, with the `ADOPT:` reasons as one Housekeeping line.

## 2. Report

Always in this order, skipping empty groups. The scan's GROUPS section has already sorted every worktree into these groups by its effective state, each row with the reason (a waiting session whose card says otherwise is under Needs you; a `needs-you` card whose session is busy again is under Running, because the user already answered); report from it rather than regrouping. When a row's reason says the card is out of date, say so in a few words. Add only what it can't see: PRs with no worktree (Housekeeping; a PR whose branch is in a STACKS tree belongs to that stack's worktree, even when it isn't checked out), and details from cards you open.

1. **Needs you** — every row the scan puts under NEEDS YOU. Each item: branch and what that work is in a few plain words, the exact question, what each option means in practice, your recommendation and why. Most urgent first. A worker about to post on GitHub or push puts the draft in its card and then waits on an approval prompt: show the draft, and tell the user to approve or decline **in that session** (session name from LIVE SESSIONS). You can't approve it from here.
2. **Blocked** — what it's waiting on.
3. **Running** — one line each.
4. **Waiting on CI or review** — finished work only waiting on CI, a review or a merge, one line each with PR number. These don't hold a WIP slot.
5. **Done** — one line each with PR number.
6. **Housekeeping** — one line each with the suggested cleanup: worktrees ready to clean up (merged with no commits past the PR's head, or every commit on main, with nothing uncommitted: offer to clean them up, below), worktrees with no card, worktrees whose card couldn't be checked (`card-unknown`: the fix is the NOTE's cause, usually tower.conf, not a new card), `STALE` cards, orphan cards, PRs with no worktree, any worktree where several interactive sessions share one checkout (rule: one main session per worktree), and overlaps: two worktrees changing the same files (`changes the same file(s) as <other>: <files>`, from git, not cards). For an overlap, name both worktrees and what each is for, and suggest who goes first: usually the one closer to merging, while the other waits or rebases after it. Never tell a worker to stop on your own.

**Stacks.** The scan's STACKS section draws each git-spice stack as a tree in merge order (bottom first, deeper branches indented), headed by its stack worktree's path and `next to merge: <branch>, PR #N`, with whether that merge waits for the user (`<key> is ask: it waits for you`) or the worker merges it itself. Under the stack worktree's line in its group, show the tree as the scan draws it, one line per branch with its PR and flags. A branch that `needs restack` (its parent moved or merged) also has a Housekeeping line from the scan with the command that fixes it, run by that worktree's worker; tell the user which session to ask, never run it yourself. When the next merge waits for the user and that PR's checks have passed, that merge is theirs to do: say so in the stack's line.

End with **one** suggested next action, not a list.

### Cleaning up finished workers

Offer cleanup for `ready-to-clean` rows, and for Done rows whose PR the GITHUB section shows merged (the scan's statuses don't read PR state, so a squash-merged worker often shows as Done). Run it only after the user says yes, from the main checkout, through `python3 -I <plugin>/scripts/run-tower.py clean` (`<plugin>` as in section 1), naming the worktrees by branch (`clean <branch> <branch>`) or `--all` (every worktree whose PR is merged). Before removing anything it checks, for each worktree, that its PR is merged at the worktree's HEAD, nothing is uncommitted or stashed on its branch, its card doesn't say work goes on, and no live session there is busy or waiting; a worktree that fails any check is left exactly as it was, with every failed check said. Then it closes the worktree's terminals, removes it (through Orca when Orca manages it), deletes its branch locally and on GitHub (when no open PR is based on it), archives its card to the Ledger's `_archive` folder, and runs `git pull --ff-only` in the main checkout. An orphan card whose branch's PR is merged is usually a cleanup that stopped after the worktree went: `clean <branch>` finishes it (same checks, minus the ones a missing worktree can't fail); `--all` doesn't pick these up.

By the scan's `Policy:` line, `cleanup`:
- `ask`: run it with `--dry-run` first (it only checks, touching nothing, and never asks), show the user what it says would go and what failed, and on their yes run the same command without `--dry-run`. The approval hook asks once; that prompt is their yes, so don't ask twice.
- `auto`: run it straight away once the user wants it cleaned up.

Show each failed check as it says it, with what would fix it (commit or remove the stray file, drop the stash, wait for the busy session). Exit 1 means a check failed or a step didn't work: say which, and never retry with git or Orca commands of your own.

### Across every repo

When the user asks about everything rather than this repo ("what needs me across everything?", "all repos", "anything else going on?"), run `python3 -I <plugin>/scripts/run-tower.py state --all` (`<plugin>` as in section 1). It covers every repo on this machine with events in tower's event log, the one you run in, and any with a Ledger folder in the vault, so work and personal stay apart: each machine has its own. It prints an ALL REPOS summary (one row per repo, most urgent first), then, in the same order, each repo's own WORKTREES, ORPHAN CARDS and GROUPS sections under `== REPO: <name> | <main checkout> | <ledger> ==`, or a `== CARDS ONLY: <name> | <ledger> ==` project's cards (a Ledger folder with no checkout tower could read, so its rows are only what the cards claim, ranked by those claims). Open a card by the same rule as section 1, at `<ledger>/<card_file>`, with `<ledger>` from that repo's heading (there is no `LEDGER:` line here); a heading that says `Ledger folder unknown` has no cards to open, and a NOTE says why (in that repo's section, or at the top when every repo shares it).

Report one group per repo, in the order printed, skipping repos with nothing to report beyond Housekeeping. Inside each, use the same groups as above from that repo's GROUPS section, skipping empty ones. A cards-only project gets one line in its place, with its card count and any card that says needs-you, marked as only the card's claim. There is no GITHUB or BACKLOG section here: say so if the user asks about PRs or issues, and offer to scan that repo. End with **one** next action across every repo, not one per repo.

Define any acronym or project term the first time it appears. Name every session, branch, and ticket with a few words on what it is: "rolly-polly-86 (the session building the UI)", not "rolly-polly-86". When you tell the user to act in a session, say what tells it apart from the others in that worktree. Needs you and Blocked get as much text as they need; Running, Done, and Housekeeping stay one line each so the report stays easy to scan.

## 3. Work-in-progress limit

The scan prints `WIP: n of limit`: n is the execution slots in use, the worktrees (not the main checkout) whose effective state is running or needs-you. A busy worker with no card counts; one only waiting on CI or review doesn't. The `UNSHIPPED:` line after it lists worktrees with commits not on main, which is a different question (work not merged yet), not slots. The limit comes from the repo's policy profile (`wip_limit` in `~/.claude/tower/policy.toml`), else `wip_limit` in `~/.claude/tower.conf` (set per machine). To change it, the user edits one of those lines; never edit them yourself.

When the user wants to start something new and the scan says `AT LIMIT`, say so plainly and offer: park it, or name which running item to pause. Don't refuse; let them override.

## 4. Discuss or park an idea

The hub is where new work gets discussed, not only tracked. An idea mentioned mid-flow ("oh, also the worm theme", or "park: <idea>") gets parked straight away, as before. When the user wants to talk it through ("discuss: <idea>"), discuss it. "discuss #N" (typed, or sent by the pane's `discuss #N` button on an issue that isn't `ready-for-agent`) is the same discussion for an issue that already exists: read it with `gh issue view <n>` first, discuss it as below, then write what was settled back to #N (edit its body, or add a comment) and label it `ready-for-agent`, or leave it unlabelled if the discussion shows it isn't ready. Don't create a new issue for it. Each of those is an issue write (a comment is under `comments`), so it asks for approval unless the scan's `Policy:` line has that key at `auto`.

You may read code here to inform the discussion, but you run in the main checkout: edit nothing and commit nothing on main. That includes GLOSSARY.md and ADRs (architecture decision records); domain decisions go in the spec, and the worker records them in its branch.

**Park it:**

- If the GITHUB section shows a repo, create an issue with a one-line title and a short body in their words, and tell them the number.
- Otherwise append a line to `<LEDGER>/_backlog.md` (path printed by the scan): `- [ ] YYYY-MM-DD — idea in their words` (get the date from `date +%F`).

Then return to what they were doing. Don't start work on the idea.

**Discuss it** with the `grilling` skill (Matt Pocock's: one question at a time until the plan holds). For a huge, foggy effort, hand it to `/wayfinder` in its own worktree instead (below), never in this session. Don't use `/grill-with-docs` here: it writes GLOSSARY.md and ADRs into the repo. The discussion ends one of two ways, unless it needs a spike:

- **Park it** as above, with what the discussion settled in the issue body.
- **Spec it**: tell the user to type `/to-spec`, which writes the spec into an issue and labels it `ready-for-agent`. It publishes straight after writing, so the approval prompt for that issue write is where the user approves the text: tell them to read it there and decline to send it back for changes. Under a profile with `issue_writes: auto` (personal) there is no prompt: tell the user to read the issue once it's filed. If it's too big for one session, tell them to type `/to-tickets` after it: sub-issues with `Blocked by` links, each `ready-for-agent`. Once it's on the tracker, the user can `/clear` the hub and rerun; nothing lives only in the chat.
- **Needs a runnable answer first** (a prototype or spike): park the question as its own issue #N, then start a worktree for it early (section 6) with `--name spike/<N>-<slug>` and this prompt, and the discussion moves there: `/prototype <the question> — #<N>. <two or three sentences of what the discussion settled>. Use the status-card skill whenever you stop.` If the discussion was long, have the user type `/handoff` first (it writes to `$TMPDIR`, outside the repo) and put the path of the file it writes into the prompt instead. #N is only the spike: when its card says the question is answered, the user types `/handoff` in the spike session, brings that file back to the hub, and the discussion ends as above (park or spec, a new issue). Close #N with a one-line answer (an issue write, so it asks) and suggest removing the spike worktree (wait for a yes; the user may want to keep the prototype code), so no ticket ever has two worktrees.

**Hand it to `/wayfinder`** (a huge, foggy effort): wayfinder resolves its tickets with `domain-modeling`, which writes GLOSSARY.md and ADRs, so it never runs in the main checkout, including this session. It gets its own worktree, started through section 6:

1. Like `/to-spec`, it reads `docs/agents/` for the tracker; if that's missing, set it up first (below). If a WORKTREES row already has `wayfinder-<slug>` in its branch, it's already started: say where and stop.
2. Prepare the section 6 command with `--name chore/wayfinder-<slug>` (the branch name too, in the fallback), no `--issue`, a kickoff file named `kickoff-wayfinder-<slug>.txt`, and this prompt: `/wayfinder <the effort, in their words> — <wayfinder rules>`, where `<wayfinder rules>` is `Use the status-card skill whenever you stop. Commit what it records in this branch. When the map reaches its destination, <ship>` (`<ship>` from section 6).
3. Once the user says start, and before running the command, make sure the repo has wayfinder's labels, if its tracker is GitHub (the GITHUB section shows a repo; other trackers have their own labels): run `scripts/wayfinder-labels.py` (relative to this skill's base directory; use its absolute path) from the main checkout. It prints one `gh label create` command per missing `wayfinder:*` label, and nothing when all five exist: then say nothing about labels. If it prints commands, run them as printed in one Bash call (a label write, so the approval hook asks unless the policy lets every GitHub write through; that's expected). If the user declines, or the script fails, don't start the worktree: `/wayfinder` fails on its first issue without the labels. Show the error as it is, or the printed commands in a fenced block for the user to run, and start it once they say the labels exist.
4. Wayfinder resolves one ticket per session, so the user comes back to that worktree for each one, not to the hub. The kickoff only reaches the first session: tell the user to start each later one with `/wayfinder <map issue> — <wayfinder rules>`, given in its own fenced block.

`/wayfinder`, `/to-spec`, `/to-tickets` and `/handoff` can only be typed by the user (Matt Pocock's skills disable model invocation), so tell them when; you can't run them. A kickoff prompt counts as typed, which is how a wayfinder or setup worktree starts. Before the first `/to-spec` in a repo, check that `docs/agents/` exists in the main checkout. If not, `/setup-matt-pocock-skills` hasn't run (it writes that folder and creates the `ready-for-agent` label), and `/to-spec` will stop and ask. Suggest running it in a worktree (`--name chore/setup-matt-skills`), never on main, with the prompt `/setup-matt-pocock-skills — Use the status-card skill whenever you stop. When it's done, commit, then <ship>` (`<ship>` from section 6). It asks questions, and its PR has to merge; then run `git pull --ff-only` in the main checkout (syncing main isn't editing it) so the folder is there. On a team repo ask first: it edits the repo's CLAUDE.md.

## 5. Pick the next ticket

When asked what to work on next, choose from the scan's open issues:

1. Skip any marked `[in progress: …]`: a worker's card or kickoff names it (or it's a branch of a stack), and the mark says where it runs. The pane shows the same issues without a start button.
2. Prefer issues labelled `ready-for-agent` (from `/to-spec`, `/to-tickets` or `/triage`).
3. Skip any whose `Blocked by` tickets are still open — run `gh issue view <n>` on the top candidates to check — unless they can ship together as a stack: when the blockers form a chain of `ready-for-agent` tickets none of which is started (each blocked only by the one below it), recommend starting the chain as one stack (section 6, "A stack of tickets"), bottom first.
4. Recommend one, in a sentence, with why.

## 6. Start a worktree

When the user picks a ticket to start ("start #42", typed or sent by the pane's `start #42` button), read it with `gh issue view <n>`, pick the worker's effort (table below), and write the kickoff prompt. Then start the worker through Orca (the worktree app) in one command. Only if Orca isn't reachable, fall back to the two-step paste.

### Effort

Never pick below `medium`. Higher effort buys more verification and edge-case testing, and more assumptions made without asking; it doesn't fix a wrong approach (that's what grilling and the spec are for).

| Effort | When |
|---|---|
| `medium` | The default: a feature slice from a clear ticket or spec, docs, config, small contained changes. |
| `high` | A bug fix in existing code, anything where verification or edge cases matter, or a ticket the user says will run while they're away. The `ready-for-agent` label alone doesn't mean that: every spec and sub-ticket gets it. |
| `xhigh` | Hidden edge cases with a high cost of being wrong: security (auth, input handling, the firewall's rules), concurrency or storage, performance work, money or risk logic (Poot's sizing, exits, risk caps). |

A spike (`/prototype`), the setup worktree or a wayfinder worktree gets `medium`. Say in one line why you picked that level.

### Kickoff prompt

Two reviews run before the PR, in this order, and the prompt gives each the range it covers, so neither can review the wrong commits. First Matt Pocock's `mattpocock-skills:code-review` (Standards + Spec), by that full name (`/implement` asks for plain "code-review", which can resolve to the built-in one), since `origin/<base>`. Then Claude Code's built-in `/code-review <level> origin/<base>...HEAD`. The built-in needs that range: with no target it reviews `@{upstream}...HEAD` (a worker's upstream can be `main` or its own pushed branch), and a branch name is the branch to review, not its base, so `/code-review high integration/a-plus` reviews the integration branch itself. Fill in four parts:

```
<start> #42 — <title>. Use the status-card skill whenever you stop. Record domain decisions from the ticket with the domain-modeling skill in this branch. Before the PR, run git fetch origin, then mattpocock-skills:code-review by that full name since origin/<base>, then the built-in /code-review <review level> origin/<base>...HEAD (exactly this branch's commits); once both pass, <ship>
```

- `<start>`: `/implement`, or `/diagnosing-bugs` for a bug ticket.
- `<base>`: the branch the PR will target: the default branch for most tickets, or the integration or stack branch the ticket builds on.
- `<review level>`: `high`, or `xhigh` when the change touches the approval hook, the policy or other security code.
- `<ship>`, by the scan's `Policy:` line (the repo's profile in `~/.claude/tower/policy.toml`; `tower policy` explains it): the key for the branch the PR lands on, `merge_to_main` for the default branch (most tickets, and every PR of a GitHub stack on it: merging a stacked PR merges the ones below it onto the stack's base), `merge_to_branch` for an integration branch or a stack on one:
  - `ask` (the user merges): `open the PR and use babysit-pr to watch it.`
  - `auto` (workers merge their own): `open the PR and, after its checks pass, merge it yourself with python3 -I <plugin>/scripts/run-tower.py merge <PR number>, run on its own (nothing chained before it); don't wait for me and don't use babysit-pr.` (`<plugin>` as in step 4 below, an absolute path.) `tower merge` squash-merges a ticket's PR and deletes its branch. It uses a merge commit for a PR in a GitHub stack, chained PRs (others based on its branch) and an integration branch's PR (other PRs were merged into its head, e.g. `integration/a-plus` -> `main`, whose branch it keeps), so commits other branches hold survive. It merges a GitHub stack, which `gh pr merge` can't, from the bottom, one PR per run, and deletes a branch only once no open PR is based on it. On chained PRs it prints the `gh pr edit` that moves the PR above; that move comes before merging that PR. Exit 3 means GitHub is still merging; running it again picks the merge up. The approval hook only lets a merge through without asking when it can look up where the PR lands, which it does only for a merge run as its own command.

`/retro` can only be typed by the user, so it isn't in the prompt; the user runs it in the worker before closing it.

### A stack of tickets

Tickets that build on each other (a `Blocked by` chain) can ship as a stack: one PR per ticket, each based on the one below, submitted with git-spice so GitHub groups them. A stack gets **one** worktree and one worker (a stack worktree), never one per ticket: git-spice skips any branch checked out in another worktree when it restacks, and everything above it. Start it when the user says "start #40 #41 #42 as a stack", or agrees to your suggestion from section 5. Start it like any ticket (below), named after the bottom ticket (`--name feat/40-<slug> --issue 40`), with this kickoff, the tickets listed bottom first:

```
/implement #40, #41 and #42 as one git-spice stack, bottom first, one branch per ticket. If git for-each-ref refs/spice prints nothing, first run git-spice repo init --trunk <base> --remote origin; then git-spice branch track on this worktree's branch for #40, and git-spice branch create <branch> for each next ticket. Use the status-card skill whenever you stop. Record domain decisions from the tickets with the domain-modeling skill in the stack. Before submitting, run git fetch origin, then mattpocock-skills:code-review by that full name since origin/<base>, then the built-in /code-review <review level> origin/<base>...HEAD from the top branch (the whole stack's commits); once both pass, <stack ship>
```

`<base>` and `<review level>` as above. `<stack ship>`, by the scan's `Policy:` line, the key for where the stack lands (`merge_to_main` for a stack on the default branch, since merging a stacked PR merges the ones below it onto the stack's base):
- `ask`: `submit it with git-spice stack submit and use babysit-pr on the bottom PR. After I merge a PR that isn't the last, go to the top branch (git-spice top) and run git-spice repo sync, git fetch origin and git-spice stack restack; git-spice stack submit only if git diff --quiet <next branch> origin/<next branch> shows its tree unchanged (else ask me), then babysit the next PR. After the last merge, don't sync.`
- `auto`: `submit it with git-spice stack submit and merge it from the bottom, one PR at a time: after a PR's checks pass, merge it yourself with python3 -I <plugin>/scripts/run-tower.py merge <PR number>, run on its own (nothing chained before it). Between merges, go to the top branch (git-spice top) and run git-spice repo sync, git fetch origin and git-spice stack restack; git-spice stack submit only if git diff --quiet <next branch> origin/<next branch> shows its tree unchanged (else ask me). After the last merge, don't sync. Don't wait for me and don't use babysit-pr.`

Why each step: `repo init` with its flags, because git-spice asks which branch is the trunk otherwise, and a worker has no terminal to answer. After a merge, GitHub moves the next PR onto the stack's base and rebases it itself, so its checks run again; locally, `repo sync` deletes the merged branch and `stack restack` rebases the rest the same way, giving the same tree under new commit ids. Submitting them only when the tree matches GitHub's makes each PR's head the worker's own commit (`tower clean` needs the worktree's HEAD to be its PR's head) without dropping anything someone else pushed. The worker syncs from the top branch, and never after the last merge: a sync deletes the branch the worktree is on when that branch merged, and can't check out the trunk (the main checkout has it), so the worktree would end on no branch, which `tower clean` refuses. A ticket added to a stack that's already running goes to that stack's worktree, not a new one: give the user a one-line prompt to paste into that worker's session (`Add #43 on top of the stack, the same way.`). Its kickoff stays the original (one kickoff per worktree), so the added ticket is kept in the worker's card and decisions log instead.

### Start it through Orca

1. Check the scan first (the pane's `start #N` button skips no rules). If a WORKTREES row already shows this ticket, a STACKS branch's name guesses it, the scan's open issues mark it `[in progress: …]` (a card, its kickoff or a stack's branch names it), or `orca worktree list --json` has a worktree with `linkedIssue` N, it's already started: say where and stop. At the WIP limit, follow section 3 before starting anything.
   Then run `orca status --json`. Orca is reachable when it exits 0 and `result.runtime.reachable` is `true`. If not (no `orca` command, an error, or `false`), use the fallback below and say why in one line.
2. Show the command in a fenced block, with the effort line, then ask with AskUserQuestion: "Start it (Recommended)", "Change the effort", "Don't start". Run it only on a yes.

   ```
   orca worktree create --repo path:<main checkout> --name <type>/<N>-<slug> --no-parent --issue <N> --agent claude --model '<model>' --effort <level> --surface terminal --json --prompt "$(cat '<kickoff file>')"
   ```

   - `<kickoff file>`: first write the kickoff prompt, one line, to a file named after the worktree (`kickoff-42.txt`, `kickoff-setup.txt`) in your scratchpad directory (or `$TMPDIR`) with the Write tool, and show the user its text. Read from a file, the prompt reaches Orca as one argument exactly as written in every shell: apostrophes ("don't"), quotes, backticks and `$` are all safe. Don't put the text in the command itself: `'...'` breaks on an apostrophe, and a heredoc inside `$(...)` breaks on one in macOS bash 3.2.

   - `<main checkout>`: the path on the first WORKTREES row of the scan.
   - `--name`, e.g. `feat/42-discord-tracker`: Orca's display name for the worktree. Orca derives the git branch from it with the user's Orca Branch prefix setting (`git-username` turns it into `<git user>/feat-42-discord-tracker`), so the real branch, and the card file named after it, can differ from `--name`.
   - `--no-parent`: the worktree is top-level work, not a child of the main checkout. `--issue <N>` links the ticket in Orca; leave it out for the ticketless starts in section 4, the setup and wayfinder worktrees.
   - `--model` is required: Orca refuses `--effort` without it (`--effort requires --model.`). Use `model` from `~/.claude/settings.json`, or `opus` when it isn't set, in single quotes (`opus[1m]` is a glob pattern to zsh); if Orca rejects that id, show the error and ask before retrying with `opus`. `--effort` on the command line applies to this worker session only, unlike `/effort <level>`.
   - `--surface terminal` runs the Claude Code CLI in an Orca terminal, which LIVE SESSIONS and tower's card hooks need; Orca's chat surface may not be either. A prompt starting with `/implement` runs as the skill, as if typed.
3. On success, run the scan with `--no-github`: the new worktree should be listed, with its session under LIVE SESSIONS. Tell the user its real branch and where it is. If the project has a runtime config (`~/.claude/tower/projects/<Project>.toml`), the worker's session gets its own `PORT` and the rest from tower's SessionStart hook, with no kickoff change; `python3 -I <plugin>/scripts/run-tower.py env --dir '<new worktree path>'` prints them (`<plugin>` as in step 4), so tell the user its port.
4. Give it a starter card, so the scan lists it under Running instead of Housekeeping until the worker writes its own. tower's core writes the card (it fills status, ticket, branch, PR and time from facts), and `--if-absent` makes it create the card only if there isn't one. `<plugin>` is two folders up from this skill's base directory (use its absolute path); use `--ticket none` for the setup and wayfinder worktrees:
   ```
   python3 -I <plugin>/scripts/run-tower.py card write --dir '<new worktree path>' --ticket <N> --if-absent <<'EOF'
   ## Done
   - Started from the hub; no report from the worker yet.

   ## Why it matters
   - <the ticket in a few words>.

   ## Needs you
   - Nothing yet.

   ## Verify
   - Nothing to verify yet.
   EOF
   ```
   If it exits 3, a card exists at the path it names: check whether it's newer than the kickoff file you wrote just before the launch (`find '<card path>' -newer '<kickoff file>'` prints it if so). Newer means the worker has written its own (maybe `needs-you` with a question): leave it. Older means it's an orphan from an earlier worktree on the same branch: run the same command without `--if-absent` to replace it.

   Then, whichever card is there now, fix the kickoff in it, so the worker gets it back after compaction or `/clear` (worker memory: tower's SessionStart hook re-reads the kickoff, the card and the worker's decisions log). The worker's `card write` keeps it word for word:
   ```
   python3 -I <plugin>/scripts/run-tower.py card kickoff --dir '<new worktree path>' < '<kickoff file>'
   ```
   Add `--replace` only when you just replaced an orphan card above: it replaces the earlier work's kickoff and archives its decisions log. Without it, a card that already has a different kickoff is left alone (exit 1, saying so); that card is from earlier work on the same branch name, since you write one kickoff per worktree, so run it again with `--replace`.
5. On failure, show Orca's `error.message` as it is. If it says the launch outcome is unknown (it gives an `operationId`), check first: find the worktree whose `displayName` is your `--name` in `orca worktree list --json`; if it's there, `orca terminal list --worktree path:<its path> --json` shows a connected Claude terminal if the launch worked (LIVE SESSIONS can lag or be skipped). Otherwise re-run the same command with `--retry-launch <operationId>` added, from the same directory; never as a fresh launch, which can start a second worker.

### Fallback: the two-step paste

When Orca isn't reachable, the user creates the worktree in their worktree app with the branch name `<short-type>/<ticket>-<slug>` (create it yourself with `git worktree add` only if they ask, under `<worktrees_dir>/<repo>/`, with `worktrees_dir` read from `~/.claude/tower.conf`; if it isn't set, ask where), opens a new Claude session in it, and pastes two steps, labelled "Step 1" and "Step 2". Anything they paste goes in its own fenced code block. Never use `>` quotes for anything to paste: the terminal copies the quote bar with the text. Leave backticks out of the pasted text.

**Step 1: set the effort for this session only.** Type this on its own, pick `<level>` in the slider, and press **s**. If the session already shows that level, skip this step. (Starting Claude by hand instead: `claude --effort <level>`.)

```
/effort
```

**Step 2: then, as a separate message:** the kickoff prompt above.

When the user says the session is running, ask for the worktree's path if you don't know it, and do step 4 of "Start it through Orca" with it: the starter card, then the kickoff, from a kickoff file holding the Step 2 text (write it with the Write tool as in step 2 there). Without the kickoff in its card, the worker loses it at compaction.

Never write `/effort <level>` for the user to paste: a level typed after `/effort` is saved as the default for every new session, not just this one. And never put both steps in one block: pasted together, Claude Code reads the `/implement` line as `/effort`'s argument and rejects the whole message.

## Gotchas

- The main checkout is labelled `main checkout (no card expected)`. Don't flag it for having no card.
- You run in the main checkout yourself, so LIVE SESSIONS counts you there. Only flag main if there are other interactive sessions besides you.
- If the scan exits non-zero saying `tower state failed`, WORKTREES, ORPHAN CARDS and GROUPS have no statuses: show the error as it is, and don't report "nothing needs you" from the empty groups.
- Whenever the scan fails (exits non-zero, or prints an error instead of its sections), also run `python3 -I <plugin>/scripts/run-tower.py doctor` (same `<plugin>`). It checks each thing tower depends on and prints a `fix:` line under each problem: give the user every `FAIL` line with its fix, then the `warn` lines in one sentence. It changes nothing, so run it without asking; leave the fixes (logins, installs, edits to tower.conf or the policy file) to the user.
- LIVE SESSIONS only covers this machine, and its format isn't a documented contract. If it prints a "skipping" line, the WORKTREES section's `NOTE:` lines say what tower used instead (the event log's last 24h, or cards alone): report from GROUPS as usual and mention it once.
- The scan's statuses never use PR state (`tower scan` doesn't pass `--pr`), so the pane's 30-second refresh and your report always agree. Don't re-decide a status from the GITHUB section; if an open PR looks merged or a Done worktree looks finished, say so as a Housekeeping suggestion.
- If context is getting long, tell the user to `/clear` and run the hub again — all state is on disk, nothing is lost.
- Never delete worktrees, branches or cards on your own; suggest it and wait for a yes. Clean up only through `tower clean` (above), never with `git worktree remove`, `orca worktree rm` or `git branch -D` yourself: they skip its checks.
- Creating a parked-idea issue is a public GitHub write, so it triggers an approval prompt here too unless the repo's policy has `issue_writes: auto`. That's expected.
- When Orca keeps worktrees inside the repo (its workspace folder set to `./worktrees`), main shows `worktrees/` as untracked. That isn't uncommitted work, and the `ADOPT:` line doesn't count it; suggest adding `worktrees/` to main's `.git/info/exclude` (local only), never to the repo's `.gitignore`. New worktrees belong in the one global folder instead: `worktrees_dir` in `~/.claude/tower.conf`, which re-running `setup.sh` asks for and which Orca's Workspace Directory should match.
