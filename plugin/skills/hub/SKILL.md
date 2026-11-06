---
name: hub
description: Control tower for parallel work in one repo. Use when the user asks what's going on, what needs them, what to work on next, wants to discuss or park an idea, wants to start a new worktree, or wants to bring a repo with work already in flight under tower (adopt it). Reads status cards, worktrees, live session states, PRs and issues — never other sessions' transcripts.
effort: medium
---

# Hub

The user runs several worktrees at once and loses track of them. You are the one place they check. Your job is to tell them clearly where everything stands and protect their attention. They read your report after time away from those sessions, so don't assume they remember any of them.

## 1. Gather state

Run `python3 -I <plugin>/scripts/run-tower.py scan` from inside the repo, `<plugin>` being two folders up from this skill's base directory (use its absolute path). It prints every worktree with its effective state (`status:`, what tower decided from the card, live sessions, the event log and git; `card:` is what the card itself says; `reason:` is why), the WIP count and unshipped work, the Claude sessions open in each worktree right now, orphaned cards, a GROUPS section that sorts all of that into the report order below, GitHub PRs and issues, and the parked-ideas backlog. The pane draws the same statuses from `tower state --json`, so never re-decide a worktree's status yourself. Use only this output plus card files you choose to open; do not open other sessions or read their transcripts.

Open a card file only when its status is `needs-you` or `blocked`, or the user asks about it.

**Adopt mode.** The `ADOPT:` line after `PR approval:` says whether the repo has been brought under tower yet. If it says `yes` (most worktrees have no card, or the main checkout has uncommitted work or other interactive sessions), don't list every problem in a report: follow [ADOPT.md](ADOPT.md) (scan, one plan, approved steps one at a time) instead of section 2. Follow it too when the user asks to adopt the repo, whatever the line says. If they asked for something else (a ticket to start, an idea to park), say in one line that the repo isn't set up yet and offer adopt mode first; do what they asked if they decline.

## 2. Report

Always in this order, skipping empty groups. The scan's GROUPS section has already sorted every worktree into these groups by its effective state, each row with the reason (a waiting session whose card says otherwise is under Needs you; a `needs-you` card whose session is busy again is under Running, because the user already answered); report from it rather than regrouping. When a row's reason says the card is out of date, say so in a few words. Add only what it can't see: PRs with no worktree (Housekeeping), and details from cards you open.

1. **Needs you** — every row the scan puts under NEEDS YOU. Each item: branch and what that work is in a few plain words, the exact question, what each option means in practice, your recommendation and why. Most urgent first. A worker about to post on GitHub or push puts the draft in its card and then waits on an approval prompt: show the draft, and tell the user to approve or decline **in that session** (session name from LIVE SESSIONS). You can't approve it from here.
2. **Blocked** — what it's waiting on.
3. **Running** — one line each.
4. **Waiting on CI or review** — finished work only waiting on CI, a review or a merge, one line each with PR number. These don't hold a WIP slot.
5. **Done** — one line each with PR number.
6. **Housekeeping** — one line each with the suggested cleanup: worktrees ready to clean up (merged, or every commit on main, with nothing uncommitted: suggest removing the worktree, never do it), worktrees with no card, `STALE` cards, orphan cards, PRs with no worktree, and any worktree where several interactive sessions share one checkout (rule: one main session per worktree).

End with **one** suggested next action, not a list.

Define any acronym or project term the first time it appears. Name every session, branch, and ticket with a few words on what it is: "rolly-polly-86 (the session building the UI)", not "rolly-polly-86". When you tell the user to act in a session, say what tells it apart from the others in that worktree. Needs you and Blocked get as much text as they need; Running, Done, and Housekeeping stay one line each so the report stays easy to scan.

## 3. Work-in-progress limit

The scan prints `WIP: n of limit`: n is the execution slots in use, the worktrees (not the main checkout) whose effective state is running or needs-you. A busy worker with no card counts; one only waiting on CI or review doesn't. The `UNSHIPPED:` line after it lists worktrees with commits not on main, which is a different question (work not merged yet), not slots. The limit comes from `wip_limit` in `~/.claude/tower.conf` (set per machine). To change it, the user edits that line.

When the user wants to start something new and the scan says `AT LIMIT`, say so plainly and offer: park it, or name which running item to pause. Don't refuse; let them override.

## 4. Discuss or park an idea

The hub is where new work gets discussed, not only tracked. An idea mentioned mid-flow ("oh, also the worm theme", or "park: <idea>") gets parked straight away, as before. When the user wants to talk it through ("discuss: <idea>"), discuss it. "discuss #N" (typed, or sent by the pane's `discuss #N` button on an issue that isn't `ready-for-agent`) is the same discussion for an issue that already exists: read it with `gh issue view <n>` first, discuss it as below, then write what was settled back to #N (edit its body, or add a comment) and label it `ready-for-agent`, or leave it unlabelled if the discussion shows it isn't ready. Don't create a new issue for it. Each of those is an issue write, so it asks for approval.

You may read code here to inform the discussion, but you run in the main checkout: edit nothing and commit nothing on main. That includes GLOSSARY.md and ADRs (architecture decision records); domain decisions go in the spec, and the worker records them in its branch.

**Park it:**

- If the GITHUB section shows a repo, create an issue with a one-line title and a short body in their words, and tell them the number.
- Otherwise append a line to `<LEDGER>/_backlog.md` (path printed by the scan): `- [ ] YYYY-MM-DD — idea in their words` (get the date from `date +%F`).

Then return to what they were doing. Don't start work on the idea.

**Discuss it** with the `grilling` skill (Matt Pocock's: one question at a time until the plan holds). For a huge, foggy effort, hand it to `/wayfinder` in its own worktree instead (below), never in this session. Don't use `/grill-with-docs` here: it writes GLOSSARY.md and ADRs into the repo. The discussion ends one of two ways, unless it needs a spike:

- **Park it** as above, with what the discussion settled in the issue body.
- **Spec it**: tell the user to type `/to-spec`, which writes the spec into an issue and labels it `ready-for-agent`. It publishes straight after writing, so the approval prompt for that issue write is where the user approves the text: tell them to read it there and decline to send it back for changes. If it's too big for one session, tell them to type `/to-tickets` after it: sub-issues with `Blocked by` links, each `ready-for-agent`. Once it's on the tracker, the user can `/clear` the hub and rerun; nothing lives only in the chat.
- **Needs a runnable answer first** (a prototype or spike): park the question as its own issue #N, then start a worktree for it early (section 6) with `--name spike/<N>-<slug>` and this prompt, and the discussion moves there: `/prototype <the question> — #<N>. <two or three sentences of what the discussion settled>. Use the status-card skill whenever you stop.` If the discussion was long, have the user type `/handoff` first (it writes to `$TMPDIR`, outside the repo) and put the path of the file it writes into the prompt instead. #N is only the spike: when its card says the question is answered, the user types `/handoff` in the spike session, brings that file back to the hub, and the discussion ends as above (park or spec, a new issue). Close #N with a one-line answer (an issue write, so it asks) and suggest removing the spike worktree (wait for a yes; the user may want to keep the prototype code), so no ticket ever has two worktrees.

**Hand it to `/wayfinder`** (a huge, foggy effort): wayfinder resolves its tickets with `domain-modeling`, which writes GLOSSARY.md and ADRs, so it never runs in the main checkout, including this session. It gets its own worktree, started through section 6:

1. Like `/to-spec`, it reads `docs/agents/` for the tracker; if that's missing, set it up first (below). If a WORKTREES row already has `wayfinder-<slug>` in its branch, it's already started: say where and stop.
2. Prepare the section 6 command with `--name chore/wayfinder-<slug>` (the branch name too, in the fallback), no `--issue`, a kickoff file named `kickoff-wayfinder-<slug>.txt`, and this prompt: `/wayfinder <the effort, in their words> — <wayfinder rules>`, where `<wayfinder rules>` is `Use the status-card skill whenever you stop. Commit what it records in this branch. When the map reaches its destination, <ship>` (`<ship>` from section 6).
3. Once the user says start, and before running the command, make sure the repo has wayfinder's labels, if its tracker is GitHub (the GITHUB section shows a repo; other trackers have their own labels): run `scripts/wayfinder-labels.py` (relative to this skill's base directory; use its absolute path) from the main checkout. It prints one `gh label create` command per missing `wayfinder:*` label, and nothing when all five exist: then say nothing about labels. If it prints commands, run them as printed in one Bash call (a GitHub write, so the approval hook asks; that's expected). If the user declines, or the script fails, don't start the worktree: `/wayfinder` fails on its first issue without the labels. Show the error as it is, or the printed commands in a fenced block for the user to run, and start it once they say the labels exist.
4. Wayfinder resolves one ticket per session, so the user comes back to that worktree for each one, not to the hub. The kickoff only reaches the first session: tell the user to start each later one with `/wayfinder <map issue> — <wayfinder rules>`, given in its own fenced block.

`/wayfinder`, `/to-spec`, `/to-tickets` and `/handoff` can only be typed by the user (Matt Pocock's skills disable model invocation), so tell them when; you can't run them. A kickoff prompt counts as typed, which is how a wayfinder or setup worktree starts. Before the first `/to-spec` in a repo, check that `docs/agents/` exists in the main checkout. If not, `/setup-matt-pocock-skills` hasn't run (it writes that folder and creates the `ready-for-agent` label), and `/to-spec` will stop and ask. Suggest running it in a worktree (`--name chore/setup-matt-skills`), never on main, with the prompt `/setup-matt-pocock-skills — Use the status-card skill whenever you stop. When it's done, commit, then <ship>` (`<ship>` from section 6). It asks questions, and its PR has to merge; then run `git pull --ff-only` in the main checkout (syncing main isn't editing it) so the folder is there. On a team repo ask first: it edits the repo's CLAUDE.md.

## 5. Pick the next ticket

When asked what to work on next, choose from the scan's open issues:

1. Prefer issues labelled `ready-for-agent` (from `/to-spec`, `/to-tickets` or `/triage`).
2. Skip any whose `Blocked by` tickets are still open — run `gh issue view <n>` on the top candidates to check.
3. Recommend one, in a sentence, with why.

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

Two different `/code-review` skills run, so the prompt names each: Matt Pocock's `mattpocock-skills:code-review` (Standards + Spec), by that full name, because `/implement` asks for plain "code-review", which can resolve to the built-in one; then Claude Code's built-in `/code-review high` before the PR. Fill in three parts:

```
<start> #42 — <title>. Use the status-card skill whenever you stop. Record domain decisions from the ticket with the domain-modeling skill in this branch. Before the PR, run mattpocock-skills:code-review by that full name, then the built-in /code-review <review level>; once it passes, <ship>
```

- `<start>`: `/implement`, or `/diagnosing-bugs` for a bug ticket.
- `<review level>`: `high`, or `xhigh` when the change touches security.
- `<ship>`, by `PR approval:` in the scan (`pr_approval` in `~/.claude/tower.conf`, set per machine):
  - `every` (the user approves PRs): `open the PR and use babysit-pr to watch it.`
  - `none` (workers open and merge their own PRs): `open the PR and squash-merge it yourself with gh pr merge --squash --delete-branch after its checks pass; don't wait for me and don't use babysit-pr.`

`/retro` can only be typed by the user, so it isn't in the prompt; the user runs it in the worker before closing it.

### Start it through Orca

1. Check the scan first (the pane's `start #N` button skips no rules). If a WORKTREES row already shows this ticket, or `orca worktree list --json` has a worktree with `linkedIssue` N, it's already started: say where and stop. At the WIP limit, follow section 3 before starting anything.
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
3. On success, run the scan with `--no-github`: the new worktree should be listed, with its session under LIVE SESSIONS. Tell the user its real branch and where it is.
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
5. On failure, show Orca's `error.message` as it is. If it says the launch outcome is unknown (it gives an `operationId`), check first: find the worktree whose `displayName` is your `--name` in `orca worktree list --json`; if it's there, `orca terminal list --worktree path:<its path> --json` shows a connected Claude terminal if the launch worked (LIVE SESSIONS can lag or be skipped). Otherwise re-run the same command with `--retry-launch <operationId>` added, from the same directory; never as a fresh launch, which can start a second worker.

### Fallback: the two-step paste

When Orca isn't reachable, the user creates the worktree in their worktree app with the branch name `<short-type>/<ticket>-<slug>` (create it yourself with `git worktree add` only if they ask), opens a new Claude session in it, and pastes two steps, labelled "Step 1" and "Step 2". Anything they paste goes in its own fenced code block. Never use `>` quotes for anything to paste: the terminal copies the quote bar with the text. Leave backticks out of the pasted text.

**Step 1: set the effort for this session only.** Type this on its own, pick `<level>` in the slider, and press **s**. If the session already shows that level, skip this step. (Starting Claude by hand instead: `claude --effort <level>`.)

```
/effort
```

**Step 2: then, as a separate message:** the kickoff prompt above.

Never write `/effort <level>` for the user to paste: a level typed after `/effort` is saved as the default for every new session, not just this one. And never put both steps in one block: pasted together, Claude Code reads the `/implement` line as `/effort`'s argument and rejects the whole message.

## Gotchas

- The main checkout is labelled `main checkout (no card expected)`. Don't flag it for having no card.
- You run in the main checkout yourself, so LIVE SESSIONS counts you there. Only flag main if there are other interactive sessions besides you.
- If the scan exits non-zero saying `tower state failed`, WORKTREES, ORPHAN CARDS and GROUPS have no statuses: show the error as it is, and don't report "nothing needs you" from the empty groups.
- LIVE SESSIONS only covers this machine, and its format isn't a documented contract. If it prints a "skipping" line, the WORKTREES section's `NOTE:` lines say what tower used instead (the event log's last 24h, or cards alone): report from GROUPS as usual and mention it once.
- The scan's statuses never use PR state (`tower scan` doesn't pass `--pr`), so the pane's 30-second refresh and your report always agree. Don't re-decide a status from the GITHUB section; if an open PR looks merged or a Done worktree looks finished, say so as a Housekeeping suggestion.
- If context is getting long, tell the user to `/clear` and run the hub again — all state is on disk, nothing is lost.
- Never delete worktrees, branches or cards on your own; suggest it and wait for a yes.
- Creating a parked-idea issue is a public GitHub write, so it triggers an approval prompt here too. That's expected.
- When Orca keeps worktrees inside the repo (its workspace folder set to `./worktrees`), main shows `worktrees/` as untracked. That isn't uncommitted work, and the `ADOPT:` line doesn't count it; suggest adding `worktrees/` to main's `.git/info/exclude` (local only), never to the repo's `.gitignore`.
