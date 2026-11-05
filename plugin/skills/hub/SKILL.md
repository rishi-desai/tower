---
name: hub
description: Control tower for parallel work in one repo. Use when the user asks what's going on, what needs them, what to work on next, wants to discuss or park an idea, or wants to start a new worktree. Reads status cards, worktrees, live session states, PRs and issues — never other sessions' transcripts.
effort: medium
---

# Hub

The user runs several worktrees at once and loses track of them. You are the one place they check. Your job is to tell them clearly where everything stands and protect their attention. They read your report after time away from those sessions, so don't assume they remember any of them.

## 1. Gather state

Run `scripts/hub-scan.sh` from this skill's base directory (use its absolute path), from inside the repo. It prints worktrees with their card status and the WIP count, the Claude sessions open in each worktree right now, orphaned cards, a GROUPS section that sorts all of that into the report order below, GitHub PRs and issues, and the parked-ideas backlog. Use only this output plus card files you choose to open; do not open other sessions or read their transcripts.

Open a card file only when its status is `needs-you` or `blocked`, or the user asks about it.

## 2. Report

Always in this order, skipping empty groups. The scan's GROUPS section has already sorted every worktree into these groups, including a waiting session whose card says otherwise; report from it rather than regrouping. Add only what it can't see: PRs with no worktree (Housekeeping), and details from cards you open.

1. **Needs you** — cards with `needs-you`, plus any live session with status `waiting` (it's waiting on the user even if its card says otherwise). A `needs-you` card whose worktree has a `busy` session and none waiting is out of date (the user already answered): list it under Running, not here. Each item: branch and what that work is in a few plain words, the exact question, what each option means in practice, your recommendation and why. Most urgent first. A worker about to post on GitHub or push puts the draft in its card and then waits on an approval prompt: show the draft, and tell the user to approve or decline **in that session** (session name from LIVE SESSIONS). You can't approve it from here.
2. **Blocked** — what it's waiting on.
3. **Running** — one line each.
4. **Done / ready to merge** — one line each with PR number.
5. **Housekeeping** — one line each with the suggested cleanup: worktrees with no card, `STALE` cards, orphan cards, PRs with no worktree, and any worktree where several interactive sessions share one checkout (rule: one main session per worktree).

End with **one** suggested next action, not a list.

Define any acronym or project term the first time it appears. Name every session, branch, and ticket with a few words on what it is: "rolly-polly-86 (the session building the UI)", not "rolly-polly-86". When you tell the user to act in a session, say what tells it apart from the others in that worktree. Needs you and Blocked get as much text as they need; Running, Done, and Housekeeping stay one line each so the report stays easy to scan.

## 3. Work-in-progress limit

The scan prints `WIP: n of limit`. The limit comes from `wip_limit` in `~/.claude/tower.conf` (set per machine). To change it, the user edits that line.

When the user wants to start something new and the scan says `AT LIMIT`, say so plainly and offer: park it, or name which running item to pause. Don't refuse; let them override.

## 4. Discuss or park an idea

The hub is where new work gets discussed, not only tracked. An idea mentioned mid-flow ("oh, also the worm theme", or "park: <idea>") gets parked straight away, as before. When the user wants to talk it through ("discuss: <idea>"), discuss it.

You may read code here to inform the discussion, but you run in the main checkout: edit nothing and commit nothing on main. That includes GLOSSARY.md and ADRs (architecture decision records); domain decisions go in the spec, and the worker records them in its branch.

**Park it:**

- If the GITHUB section shows a repo, create an issue with a one-line title and a short body in their words, and tell them the number.
- Otherwise append a line to `<LEDGER>/_backlog.md` (path printed by the scan): `- [ ] YYYY-MM-DD — idea in their words` (get the date from `date +%F`).

Then return to what they were doing. Don't start work on the idea.

**Discuss it** with the `grilling` skill (Matt Pocock's: one question at a time until the plan holds). For a huge, foggy effort, tell the user to type `/wayfinder` instead. Don't use `/grill-with-docs` here: it writes GLOSSARY.md and ADRs into the repo. The discussion ends one of two ways, unless it needs a spike:

- **Park it** as above, with what the discussion settled in the issue body.
- **Spec it**: tell the user to type `/to-spec`, which writes the spec into an issue and labels it `ready-for-agent`. It publishes straight after writing, so the approval prompt for that issue write is where the user approves the text: tell them to read it there and decline to send it back for changes. If it's too big for one session, tell them to type `/to-tickets` after it: sub-issues with `Blocked by` links, each `ready-for-agent`. Once it's on the tracker, the user can `/clear` the hub and rerun; nothing lives only in the chat.
- **Needs a runnable answer first** (a prototype or spike): park the question as its own issue #N, then start a worktree for it early (section 6) with `--name spike/<N>-<slug>` and this prompt, and the discussion moves there: `/prototype <the question> — #<N>. <two or three sentences of what the discussion settled>. Use the status-card skill whenever you stop.` If the discussion was long, have the user type `/handoff` first and put the file it writes into the prompt instead. #N is only the spike: when its card says the question is answered, the user types `/handoff` in the spike session, brings that file back to the hub, and the discussion ends as above (park or spec, a new issue). The spike worktree is then cleaned up, so no ticket ever has two worktrees.

`/wayfinder`, `/to-spec`, `/to-tickets` and `/handoff` can only be typed by the user (Matt Pocock's skills disable model invocation), so tell them when; you can't run them. Before the first `/to-spec` in a repo, check that `docs/agents/` exists in the main checkout. If not, `/setup-matt-pocock-skills` hasn't run (it writes that folder and creates the `ready-for-agent` label), and `/to-spec` will stop and ask. Suggest running it in a worktree (`--name chore/setup-matt-skills`, prompt `/setup-matt-pocock-skills`), never on main; it asks questions, then its PR has to merge before main has the folder. On a team repo ask first: it edits the repo's CLAUDE.md.

## 5. Pick the next ticket

When asked what to work on next, choose from the scan's open issues:

1. Prefer issues labelled `ready-for-agent` (from `/triage`).
2. Skip any whose `Blocked by` tickets are still open — run `gh issue view <n>` on the top candidates to check.
3. Recommend one, in a sentence, with why.

## 6. Start a worktree

When the user picks a ticket to start ("start #42", typed or sent by the pane's `#42` button), read it with `gh issue view <n>`, pick the worker's effort (table below), and write the kickoff prompt. Then start the worker through Orca (the worktree app) in one command. Only if Orca isn't reachable, fall back to the two-step paste.

### Effort

Never pick below `medium`. Higher effort buys more verification and edge-case testing, and more assumptions made without asking; it doesn't fix a wrong approach (that's what grilling and the spec are for).

| Effort | When |
|---|---|
| `medium` | The default: a feature slice from a clear ticket or spec, docs, config, small contained changes. |
| `high` | A bug fix in existing code, anything where verification or edge cases matter, or a ticket that will run while the user is away (`ready-for-agent`, background). |
| `xhigh` | Hidden edge cases with a high cost of being wrong: security (auth, input handling, the firewall's rules), concurrency or storage, performance work, money or risk logic (Poot's sizing, exits, risk caps). |

Say in one line why you picked that level.

### Kickoff prompt

Two different `/code-review` skills run, so the prompt names each: Matt Pocock's `mattpocock-skills:code-review` (Standards + Spec; `/implement` runs it on its own, `/diagnosing-bugs` doesn't), then Claude Code's built-in `/code-review high` before the PR. Fill in three parts:

```
<start> #42 — <title>. Use the status-card skill whenever you stop. Record domain decisions from the ticket with the domain-modeling skill in this branch. Before the PR, make sure mattpocock-skills:code-review has run (once is enough), then run the built-in /code-review <review level>; once it passes, <ship>
```

- `<start>`: `/implement`, or `/diagnosing-bugs` for a bug ticket.
- `<review level>`: `high`, or `xhigh` when the change touches security.
- `<ship>`, by `PR approval:` in the scan (`pr_approval` in `~/.claude/tower.conf`, set per machine):
  - `every` (the user approves PRs): `open the PR and use babysit-pr to watch it.`
  - `none` (workers open and merge their own PRs): `open the PR and squash-merge it yourself with gh pr merge --squash --delete-branch after its checks pass; don't wait for me and don't use babysit-pr.`

`/retro` can only be typed by the user, so it isn't in the prompt; the user runs it in the worker before closing it. Leave backticks, double quotes and `$` out of the prompt, and rewrite a title with an apostrophe without it: the prompt goes inside single quotes in a shell command.

### Start it through Orca

1. Run `orca status --json`. Orca is reachable when it exits 0 and `result.runtime.reachable` is `true`. If not (no `orca` command, an error, or `false`), use the fallback below and say why in one line.
2. Show the command in a fenced block, with the effort line, then ask with AskUserQuestion: "Start it (Recommended)", "Change the effort", "Don't start". Run it only on a yes.

   ```
   orca worktree create --repo path:<main checkout> --name <type>/<N>-<slug> --no-parent --issue <N> --agent claude --model <model> --effort <level> --surface terminal --json --prompt '<kickoff prompt>'
   ```

   - `<main checkout>`: the path on the first WORKTREES row of the scan.
   - `--name`, e.g. `feat/42-discord-tracker`: Orca's display name for the worktree. Orca derives the git branch from it with the user's Orca Branch prefix setting (`git-username` turns it into `<git user>/feat-42-discord-tracker`), so the real branch, and the card file named after it, can differ from `--name`.
   - `--no-parent`: the worktree is top-level work, not a child of the main checkout. `--issue <N>` links the ticket in Orca; leave it out for the one ticketless start, the setup worktree in section 4.
   - `--model` is required: Orca refuses `--effort` without it (`--effort requires --model.`). Use `model` from `~/.claude/settings.json`, or `opus` when it isn't set. `--effort` on the command line applies to this worker session only, unlike `/effort <level>`.
   - `--surface terminal` runs the Claude Code CLI in an Orca terminal, which LIVE SESSIONS and tower's card hooks need; Orca's chat surface may not be either. A prompt starting with `/implement` runs as the skill, as if typed.
3. On success, run `hub-scan.sh --no-github`: the new worktree should be listed, with its session under LIVE SESSIONS. Tell the user its real branch and where it is.
4. Write it a starter card, so the scan lists it under Running instead of Housekeeping until the worker writes its own: run `../status-card/scripts/card-path.sh <new worktree path>` (relative to this skill's base directory; use its absolute path) and write the status-card format there with `status: running`, `ticket: "#<N>"` (quoted, or YAML reads it as a comment), the real branch, `pr: none`, `updated` from `date '+%Y-%m-%dT%H:%M'`, and "Started from the hub; the worker hasn't reported yet." under Done. The worker overwrites it the first time it stops.
5. On failure, show Orca's `error.message` as it is. If it says the launch outcome is unknown (it gives an `operationId`), run `hub-scan.sh --no-github` first: if the worktree and its session are there, it worked. Otherwise re-run the same command with `--retry-launch <operationId>` added, from the same directory; never as a fresh launch, which can start a second worker.

### Fallback: the two-step paste

When Orca isn't reachable, the user creates the worktree in their worktree app with the branch name `<short-type>/<ticket>-<slug>` (create it yourself with `git worktree add` only if they ask), opens a new Claude session in it, and pastes two steps, labelled "Step 1" and "Step 2". Anything they paste goes in its own fenced code block. Never use `>` quotes for anything to paste: the terminal copies the quote bar with the text. Leave backticks out of the pasted text.

**Step 1: set the effort for this session only.** Type this on its own, pick `<level>` in the slider, and press **s**. If the session already shows that level, skip this step. (Starting Claude by hand instead: `claude --effort <level>`.)

```
/effort
```

**Step 2: then, as a separate message:** the kickoff prompt above.

Never write `/effort <level>` for the user to paste: a level typed after `/effort` is saved as the default for every new session, not just this one. And never put both steps in one block: pasted together, Claude Code reads the `/implement` line as `/effort`'s argument and rejects the whole message.

## Gotchas

- If most worktrees have no card, or the main checkout has several sessions or uncommitted changes, the repo hasn't been adopted yet: suggest running the `tower:adopt` skill instead of listing every problem.
- The main checkout is labelled `main checkout (no card expected)`. Don't flag it for having no card.
- You run in the main checkout yourself, so LIVE SESSIONS counts you there. Only flag main if there are other interactive sessions besides you.
- LIVE SESSIONS only covers this machine, and its format isn't a documented contract. If it prints a "skipping" line, report from cards alone.
- If context is getting long, tell the user to `/clear` and run the hub again — all state is on disk, nothing is lost.
- Never delete worktrees, branches or cards on your own; suggest it and wait for a yes.
- Creating a parked-idea issue is a public GitHub write, so it triggers an approval prompt here too. That's expected.
- When Orca keeps worktrees inside the repo (its workspace folder set to `./worktrees`), main shows `worktrees/` as untracked. That isn't uncommitted work and isn't a reason to suggest adopt; suggest adding `worktrees/` to main's `.git/info/exclude` (local only), never to the repo's `.gitignore`.
