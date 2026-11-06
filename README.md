# tower

A way to run many Claude Code agents in parallel, on personal and work projects alike, without losing track of
which session owns what, what needs you, and how the pieces fit together.

Each agent works one ticket in its own git worktree and leaves a short **status card** in your Obsidian vault.
One **hub** session reads only those cards plus git, GitHub, and Claude Code's live session list, and tells you
on one screen what needs you and what to do next. Nothing it uses is committed to any repo, so it is safe on a
shared work repo. Already have a messy repo? The hub's **adopt mode** brings it under tower without losing a change.

tower ships as a **Claude Code plugin** (skills + an approval hook) plus a small per-machine `setup.sh`.

---

## The four rules

1. **One ticket = one branch = one worktree = one main session.** Subagents inside a worktree are fine; two sessions editing the same worktree are not.
2. **WIP limit** (work in progress): at most N execution slots in use (3 personal, 4 work, set per machine). A worktree holds a slot when its session is busy or waiting on you, or its card says `running` or `needs-you`; one only waiting on CI or review doesn't. New ideas get parked as tickets, not started.
3. **The hub never reads transcripts.** All state is on disk, so the hub is disposable: `/clear` it and run it again any time.
4. **Nothing public without your OK, unless your policy says so.** Comments, PR changes, merges and pushes stop for your approval as this machine's profile (`work`, `personal`, `sandbox`) says; edits to your standing rules and to the policy itself always do.

## How it works

```
 worktree A ──┐  status-card skill writes          ┌─> Vault/Projects/<Project>/Ledger/feat__42-x.md
 worktree B ──┼─ a card when it stops ─────────────┼─> Vault/Projects/<Project>/Ledger/fix__51-y.md
 worktree C ──┘                                    └─> ...
                                                          │
 main checkout: /tower:hub ── tower scan ── tower state ───┤ cards
                                                           ├ git worktree list, commits not on main
                                                           ├ claude agents --json  (live: busy / idle / waiting)
                                                           ├ the event log (stands in when agent view can't be read)
                                     tower scan also reads └ gh: your PRs, open issues
                     └─> one screen: Needs you → Blocked → Running → Waiting on CI → Done → Housekeeping → ONE next action
```

- **Status card**: a ~25-line Markdown note per worktree: status (`running`, `needs-you`, `waiting-ci`, `blocked`, `done`), ticket, PR, what's done, why it matters, the one decision it needs from you, how to verify. The worker writes only the prose; tower's core (`tower card write`) fills the top block (YAML frontmatter: `status`, `ticket`, `updated`, `branch`, `pr`, `base`, and `reason` on `needs-you` and `blocked`) from git and GitHub, so Obsidian shows the fields as properties and a card can't say `done` without a PR.
- **Hub**: a session in the repo's main checkout that you ask "what's going on?" or "what's next?". It reports in a fixed order and ends with one suggested action.
- **Live sessions**: the hub also reads Claude Code's agent view (`claude agents --json`), so it sees a session waiting on you even if its card is stale, and flags a worktree with several sessions in it.
- **Effective state**: `tower state` (tower's core) weighs the card, live sessions, the event log and git into one status per worktree, with the reason: `needs-you`, `running`, `waiting-ci`, `blocked`, `done`, `ready-to-clean`, `no-card` or `main`. A session waiting on you beats any card; a busy session beats a `needs-you` card (you already answered). The hub, the pane and the WIP count all read it, so they never disagree. From this checkout: `uv run tower state` (add `--json` for the machine-readable form, `--pr` to also ask GitHub about each branch's PR, `--all` for every repo on this machine: each one in the event log, this one, and any with a Ledger folder in the vault).
- **File overlaps**: `tower state` also checks every pair of worktrees (not the main checkout) for files both changed, from git, never from cards: each branch's own commits (not on the other branch, not on the base; a file only while it still differs from the base, so a squash-merged worktree stops counting) plus its uncommitted and untracked files. The hub and the pane list each pair under Housekeeping with the shared files, so two workers editing one file hear about it before merge time. Branches stacked on each other, or cut from the same integration branch, only overlap on files each changed itself.

## Install

On each Mac (after Claude Code, `gh`, and git are set up):

```bash
gh repo clone rishi-desai/tower ~/Developer/personal/tower
~/Developer/personal/tower/setup.sh --dry-run     # shows every change; changes nothing
~/Developer/personal/tower/setup.sh               # asks: projects folder, WIP limit, this machine's profile, worktrees folder
```

`setup.sh` writes `~/.claude/tower.conf` and the default profile in `~/.claude/tower/policy.toml` (see
[Policy profiles](#policy-profiles-claudetowerpolicytoml)), creates the worktrees folder and `~/.claude/tower/rules/`, adds `CLAUDE.local.md` to your
global git ignore, refreshes the tower block in `~/.claude/CLAUDE.md`, and installs the one plugin:

```bash
claude plugin marketplace add rishi-desai/tower
claude plugin install tower@tower          # skills, approval hook, card hooks, and the live pane
# or, in one step inside Claude Code 2.1.275+:  /plugin install tower --marketplace rishi-desai/tower
```

The pane is a mod (a hooks module) inside the same plugin. Claude Code admits or refuses each hooks module on its
own (`plugin.register` in its mod docs), so a policy that refuses mods should cost only the pane, with the skills and
the approval and card hooks still loading; that case hasn't been tried under a real policy.

### Upgrading from two plugins (tower 0.10.0 and earlier)

Before 0.11.0 the pane was a second plugin, `tower-pane@tower`. It's no longer in the marketplace: once the
marketplace is updated, an installed copy stays in `claude plugin list` as `✘ failed to load` (`Plugin tower-pane
not found in marketplace tower`) and `claude plugin update` can't move it. Re-running `setup.sh` does the upgrade
when it finds `tower-pane@tower`: it uninstalls it, updates the marketplace and updates `tower@tower`. An install
that never had the pane updates the usual way (see "Updating and uninstalling"). By hand:

```bash
claude plugin uninstall tower-pane@tower
claude plugin marketplace update tower
claude plugin update tower@tower           # 0.11.0 or later carries the pane
```

Then restart Claude Code. The pane starts with an empty cache (its saved state moved from `tower-pane` to
`tower`), so the first scan fills it.

Restart Claude Code afterwards. Re-run `setup.sh` any time to change settings; it keeps what it doesn't own.

tower is independent of your dotfiles: on a new Mac, after your usual setup, clone this repo and run `setup.sh`.
If the plugin is already installed (for example from a restored `settings.json`), step 5 notices and skips it.

## How to use it

### First time in a repo

1. Open one Claude Code session in the repo's **main checkout** (in Orca: the main worktree). This is your **hub**. Use it only to check status and start work, never to write code.
2. Type `/tower:hub`. If the repo already has work in flight (most worktrees with no card, uncommitted changes in main, several sessions in it), the hub says so and switches to its adopt mode: follow its plan. See [Adopting an existing repo](#adopting-an-existing-repo).
3. If the project's vault folder isn't named like the repo folder, add `project.<repo>=<Folder>` to `~/.claude/tower.conf` (e.g. `project.poot=Poot`).

### Starting a ticket

1. In the hub: **"what's next?"** It recommends one unblocked ticket and says why.
2. **"start #42"** (or click `#42` in the pane). It picks an effort level with its reason, writes the kickoff prompt, and
   shows one `orca worktree create …` command that creates the worktree and starts Claude in it with that effort
   (this session only) and prompt. Say yes and it runs it, checks the new session shows up, and writes the worktree a
   starter `running` card (unless the worker already wrote its own).
3. If Orca isn't reachable, it falls back to the old way: create the worktree in your worktree app, open a **new** Claude
   session in it, then **Step 1:** type `/effort` on its own, pick the level in the slider, press **s**; **Step 2:** send
   the `/implement #42 — …` line as its own message (pasted together, Claude Code rejects both).
4. Leave it alone. Whenever it stops, it writes its card and ends its message with **What happened / Done / Why it matters / Needs you / Verify**, written so you can follow it without having watched the session. When it needs a decision, it asks in Claude Code's question pop-up (recommended option first), so you answer by picking, not by typing a new prompt.

If the hub says `AT LIMIT`, finish or pause something first, or park the new ticket. It won't refuse if you insist.

### Checking in

- In the hub: **"what needs me?"** (or `/tower:hub`). You get one screen, ordered Needs you → Blocked → Running → Waiting on CI or review → Done → Housekeeping, and one suggested next action.
- Each "Needs you" item names the session to go to. Answer there, not in the hub.
- Or open the live pane with `/tower` (see below): the same picture, refreshed every 30 seconds, where a click
  jumps to the session waiting on you in Orca, and 0 there brings you back.
- In Obsidian: `Projects/<Project>/Ledger/` has one note per worktree, with `status`, `ticket`, `pr` and `updated` as properties.
- If the hub's context gets long, `/clear` it and ask again. Everything it reads is on disk.

### The tower pane

The pane is a mod inside the tower plugin: a panel inside Claude Code, beside the chat. Run `/tower` in the hub
session to open it.
It never reads transcripts either: it draws `tower state --json`, the same effective state the hub reports from,
so the two can't disagree.

- **Status line:** `tower · work · 2/3 · 1 needs you` (the repo's profile, then the WIP count) in every session of
  the repo, and a toast when something new needs you.
- **Needs you:** worktrees whose effective state is needs-you. A session waiting on you shows **WAITING** with what
  it needs (`input needed` for a question, `approve Bash` for an approval); a needs-you card nothing contradicts
  shows **NEEDS YOU**. A card whose session is working again (you already answered) shows as running under
  Worktrees, with the reason.
- **Click a worktree name** (or press 1–9 on a Needs you row): Orca switches to the terminal that is waiting on
  you (Orca's own agent state says which; without it, the Claude terminal that printed last, and the toast says
  so) and comes to the front. Answer there: the pop-up has every option, typed answers included. **card** opens
  the status card in Obsidian.
- **Back to the hub:** in a worker's session inside Orca, a `0: back to the tower hub` line sits above the prompt.
  Press 0 in the empty prompt, or click it, and Orca switches back to the hub's terminal. It steps aside while a
  question is open.
- **Approvals never go through the pane.** A push or a GitHub comment is approved only in the session doing it,
  so nothing else can approve on your behalf.
- **Buttons:** Refresh (r), Refresh GitHub (g: PRs and every open issue: each ready one, then the newest others
  up to ten, and "+ N more";
  `start #N` on a `ready-for-agent` issue starts it, `discuss #N` on any other asks the hub to discuss it first),
  What's next? (n), Nudge (asks a cardless session to write its card), and Park an idea.

Anything that changes something (starting a ticket, parking an idea) is sent to the hub's Claude as a request,
so the usual approval prompts still apply.

### When an agent wants to post, push, or change a rule

The agent shows you the exact text in its chat, puts the draft in its card, and Claude Code shows an approval
prompt in **that** session. Approve or decline there; the hub also lists it under "Needs you". What asks is this
repo's profile: to be asked less, switch it in your own terminal (`tower policy use personal`, or `--repo` for this
repo only), or change one key in `~/.claude/tower/policy.toml` (see
[Policy profiles](#policy-profiles-claudetowerpolicytoml)).

### When you think of something else mid-flow

Tell the hub **"park: worm theme for the dashboard"**. It files a GitHub issue (after you approve) or adds a line
to `Ledger/_backlog.md`, then goes back to what you were doing. Nothing starts until you pick it.

### Discussing an idea into a spec

Tell the hub **"discuss: worm theme for the dashboard"**. It reads code if that helps but never edits or commits on
main. It grills the idea one question at a time, and the discussion ends one of two ways: **park it** (an issue with what was settled), or **spec it**: you type `/to-spec`,
which writes the spec into an issue labelled `ready-for-agent` (read it in the approval prompt), then `/to-tickets`
if it's too big for one session. Then "start #N". If the discussion needs a prototype or spike to answer, the hub
parks the question as its own issue and starts a worktree for it early; the discussion moves there, and its answer
comes back to the hub with `/handoff`.

For a huge, foggy effort, the hub hands off to `/wayfinder` instead of grilling: it starts a worktree named
`chore/wayfinder-<slug>` with `/wayfinder` as the prompt, never in the hub session, because wayfinder writes
GLOSSARY.md and ADRs into the repo. Before starting it, the hub creates any of the five `wayfinder:*` labels a GitHub
repo is missing (after you approve), since wayfinder fails without them; decline and it shows you the
`gh label create` commands and waits until you've run them. You go back to that worktree for each wayfinder ticket,
with the line the hub gives you.

The first `/to-spec` in a repo needs `/setup-matt-pocock-skills` to have run; the hub suggests running it in a
worktree, never on main.

### Shipping

1. The worker reviews its own work before the PR, because the kickoff prompt tells it to: Matt Pocock's
   `mattpocock-skills:code-review` (named in full: `/implement` asks for plain "code-review", which can resolve to the built-in one), then the built-in `/code-review high` (`xhigh` if it
   touches security). Without the kickoff, type those yourself.
2. It opens the PR: it drafts the title and description and asks before creating it.
3. It babysits the PR (the kickoff says so; otherwise say **"babysit the PR"**). It watches your repo's checks and review comments, fixes real failures, and asks before every reply or push.
4. When checks are green, its card says `done` with the PR number. You merge.

When the repo's policy lets the merge through without asking (`merge_to_main = "auto"`, or `merge_to_branch` for a
PR into a stack or integration branch), steps 2–4 collapse: once `/code-review` passes, the worker opens the PR and
squash-merges it itself after the checks pass, with no prompts and no babysitting. The hub's kickoff line says so.
Under `personal`, PRs into branches merge themselves and PRs into `main` wait for you; under `work` you approve every
PR change.

### Finishing up

- After merging, remove the worktree in Orca. The hub will list its card as an orphan; say "yes, delete it" when it suggests that.
- Stopping for the day: `/handoff` in any session you'll resume. The cards stay either way.
- Want an agent to remember a preference for this project? Say **"remember for this project: use pytest fixtures"**. It asks before saving it to `~/.claude/tower/rules/`.

### Things to say to the hub

| Say | You get |
|---|---|
| "what needs me?" | the one-screen report |
| "what's next?" | one unblocked ticket, preferring `ready-for-agent`, with a reason |
| "start #42" | effort level and the Orca command that starts the worker; runs it on your yes (two paste steps if Orca is down) |
| "discuss: <idea>" | grilling in the hub, ending in a parked issue or a spec (`/to-spec`) |
| "park: <idea>" | a GitHub issue (asks first) or a backlog line |
| "what should I pause?" | which running item to pause to get under the WIP limit |
| "clean up" | housekeeping suggestions: stale cards, orphans, merged worktrees. Nothing is deleted without your yes |

### If something looks wrong

| You see | Do |
|---|---|
| "no projects_dir … run setup.sh" | run `setup.sh` on this machine |
| `STALE` on a card | go to that session and ask it to update its card, or check whether it has quietly died |
| "skipping live sessions" | fine; the hub falls back to cards only |
| a worktree with `NO CARD` | ask that session to "write your status card", or ask the hub to adopt the repo |
| no approval prompt before a comment or push | run `tower policy`: the profile may let it through. Otherwise check that the plugin is enabled (`claude plugin list`) and tell Claude; don't rely on it until fixed |
| prompts for everything, `tower policy: work` | `tower policy` says why: no policy file, or a problem in it (each listed with its line) |

## Daily loop

| Step | Where | Command | Effort |
|---|---|---|---|
| 1. Sharpen the idea | hub | "discuss: <idea>" (grilling; not `/grill-with-docs`, which writes into the repo) | medium (the hub's) |
| 2. Write it down | hub | `/to-spec`, then `/to-tickets` | medium |
| 3. Pick a ticket | hub | "what's next?" | — |
| 4. Build it | new worktree, new session | "start #42": the hub runs `orca worktree create … --effort <level> --prompt <the /implement #42 … kickoff>` | hub picks: medium / high / xhigh |
| 5. Check status | hub | `/tower:hub` | — |
| 6. Verify | the worktree's session | the kickoff has it run `mattpocock-skills:code-review`, then `/code-review high` (`xhigh` if it touches security) | high / xhigh |
| 7. Ship | same session | open the PR (asks you unless the policy says auto), then "babysit the PR"; when the policy lets the merge through, the worker merges it itself | — |
| 8. Stop for the day | each session | `/handoff` | — |
| Weekly | any | `/retro` to improve skills, checks, and gotchas from real failures | — |

"Effort" is Claude Code's reasoning effort: spend less to build, more to verify. tower never goes below `medium`.

### How effort is chosen

Based on Thariq's [Spending your effort](https://claude.dev/blog/spending-your-effort/): higher effort buys more
verification and edge-case testing, and more decisions made without asking you. It doesn't fix a wrong
approach, which is what grilling and the spec are for.

| Where | Effort | Why |
|---|---|---|
| `status-card`, `project-rules`, `hub` (adopt mode included) | medium | in-the-loop, quick work; in adopt mode you approve each step, so it shouldn't assume more |
| `babysit-pr` | high | verifying bot findings and fixing CI in existing code, often while you're away |
| worker: feature slice from a clear ticket | medium | "most regular software engineering work" |
| worker: bug fix in existing code, or runs while you're away | high | "verification is important or there are edge cases" |
| worker: security, concurrency/storage, performance, money/risk logic | xhigh | the benchmark tasks that only passed at xhigh were edge-case-heavy; security gained most |
| verification | high, or xhigh for security | "code review and security" benefit most |

- **Skills set their own level** with `effort:` in their frontmatter, overriding the session while they run. Edit that line to change one.
- **Worker sessions:** a skill can't set a whole session, so the hub starts the worker with `orca worktree create … --model <model> --effort <level>`, which applies to that session only (Orca refuses `--effort` without `--model`; the hub uses `model` from `~/.claude/settings.json`). Without Orca, the kickoff starts with the `/effort` slider, then **s**. Not `/effort <level>`: a level typed after `/effort` (or picked with Enter) is saved as the default for every new session. One `/effort xhigh` for a risky ticket once left every later session at `xhigh`.
- **Your default** for new sessions is the level you last saved, kept per model under `modelSettings` in `~/.claude/settings.json`. tower assumes `high`. To check it, open `/effort` in a fresh session; to change it on purpose, pick a level and press Enter.
- `CLAUDE_CODE_EFFORT_LEVEL`, if you ever set it, overrides all of the above. `max` isn't used by default.

## Adopting an existing repo

The hub's adopt mode is for a repo where work is already in flight and you've lost track. `/tower:hub` (run from the main checkout) switches to it by itself when `tower scan`'s `ADOPT:` line says `yes`: more than half the worktrees (and at least two) have no card, the main checkout has uncommitted changes (a folder that only holds worktrees doesn't count), or more than one interactive session is open in it (the hub is one). Ask the hub to adopt the repo to run it anyway. `/tower:adopt` still works for one release and does the same.

1. **Scan (read-only).** `tower adopt-scan` lists, per worktree: card or not, uncommitted files, commits ahead/behind `origin/<default>`, last commit, PR, ticket guess; the live sessions in each worktree; and branches with unmerged work but no worktree (with their PR state). An untracked folder holding only worktrees (Orca's `./worktrees`) isn't counted as uncommitted; a note says how to exclude it instead.
2. **One plan, which you approve.** In order:
   - a **paste-in prompt for every live session**: it writes its own first card and, if it's in the main checkout, lists exactly which uncommitted files are its work;
   - **untangling the main checkout**: each session's files move to their own branch + worktree (`git stash push -- <its files>` → `git stash pop` in the new worktree; never `reset`, `checkout .`, or `clean`);
   - **draft cards** from git facts for worktrees with no live session, marked `needs-you` until you confirm;
   - **WIP**: what to park first (merged or untouched longest), and cleanup suggestions;
   - **tickets** for work with no issue (creating one asks you).
3. **Steps run one at a time**, only the ones you approve. Nothing is deleted. It ends with the hub's usual report.

## Skills

All skills are namespaced by the plugin: `tower:hub`, `tower:status-card`, and so on. Claude picks them by
description, or you type `/tower:<name>`.

### Run automatically (Claude decides from the skill's description)

| Skill | Fires when | What it does |
|---|---|---|
| `tower:status-card` | an agent finishes a chunk, stops for you, gets blocked, or opens/updates a PR | Pipes the card's sections (Done, Why it matters, Needs you, Verify, Words used) into `tower card write`, which picks the status from the branch's PR (none → running, open → waiting-ci, merged → done; the agent may ask for needs-you or blocked with a reason, or done while its PR waits on your merge, never without a PR) and overwrites the card in the vault. The card is the short version for the hub; the final chat message is the full version for you. |
| `tower:hub` | you ask what's going on, what needs you, what's next, to park an idea, or to start a worktree | Runs `tower scan` (which sorts worktrees into the report groups itself), reports in the fixed order, enforces the WIP limit, discusses ideas into specs (grilling on main, committing nothing) or parks them as GitHub issues (or `Ledger/_backlog.md` without GitHub), picks the next unblocked `ready-for-agent` ticket, and starts it with one `orca worktree create` command on your yes (two paste steps without Orca). Switches to adopt mode when the repo hasn't been brought under tower yet ([above](#adopting-an-existing-repo)). |
| `tower:adopt` | you type `/tower:adopt` | The old name for the hub's adopt mode, kept for one release. |
| `tower:project-rules` | you explicitly ask to remember a rule for this project | Adds your rule to `~/.claude/tower/rules/<Project>.md` (asks you) and links it into the worktree's git-ignored `CLAUDE.local.md`. |
| `tower:babysit-pr` | you say "babysit / watch / monitor the PR" | Runs `pr-wait` (one line per real event: check failed, comment, review, base moved, merged) and fixes real CI failures and review findings. Drafts every public action and waits for your approval. Waits on your repo's own required checks (`--until green`). |
| `tower:leaving-pr-comments` | any agent comment on GitHub | Keeps each comment short and direct, checks it for secrets, previews it, and confirms GitHub posted it before resolving a thread. |
| `tdd`, `code-review`, `grilling`, `diagnosing-bugs`, `domain-modeling`, `research`, `prototype` (Matt Pocock) | their descriptions match what you're doing | `/implement` calls `tdd` and `code-review` itself. |

### Run only when you type them (Matt Pocock, `disable-model-invocation`)

| Command | When to use | What it does |
|---|---|---|
| `/grill-me`, `/grill-with-docs` | an idea is fuzzy | Interviews you one round at a time with a recommended answer per question; `-with-docs` also writes ADRs (architecture decision records) and the glossary. |
| `/to-spec` | the idea is clear | Turns the conversation into a spec on the issue tracker. |
| `/to-tickets` | a spec exists | Splits it into tracer-bullet tickets (thin end-to-end slices), each listing the tickets that block it. |
| `/implement #42` | starting a ticket | Reads the ticket, builds it test-first, runs `code-review`, commits. |
| `/triage` | issues pile up | Labels issues (`needs-triage`, `ready-for-agent`, `ready-for-human`, …). The hub prefers `ready-for-agent`. |
| `/wayfinder` | work too big for one session | Charts a map of decision tickets and resolves them one at a time. |
| `/handoff` | stopping a session | Writes a handoff doc for the next session (in `$TMPDIR`; your card is the durable record). |
| `/wait-what` | an answer didn't land | Makes the agent re-pitch it in plain English using the repo's `GLOSSARY.md` words. |
| `/retro` | weekly | Suggests environment fixes: checks, coding standards, gotchas. |
| `/setup-matt-pocock-skills` | once per repo | Configures tracker and labels. **Writes files into the repo — ask first on work repos.** |

**Which `/code-review`?** Several are installed. `/code-review high` (with an effort level) is Claude Code's built-in reviewer, the one the daily loop means. Matt's is `mattpocock-skills:code-review`; `/implement` asks for plain "code-review", which can resolve to the built-in one, so the hub's kickoff names Matt's in full.

### Always on (not skills)

- **The CLAUDE.md block** (between `<!-- BEGIN tower -->` and `<!-- END tower -->` in `~/.claude/CLAUDE.md`): how agents report back (What happened / Done / Why it matters / Needs you / Verify, every name and term explained on first use), the four Karpathy working principles, the parallel-work rules, the approval rule, keeping personal rules out of repos, and no AI attribution (no co-author trailers, "Generated with" lines, "on behalf of" notes or 🤖 in commits, PRs, comments, issues or branch names).
- **The approval hook** (`plugin/hooks/hooks.json` → `confirm-public.py`), active while the plugin is enabled. See below.
- **The policy line** (`policy-context.py`, a SessionStart hook): one line added to each session's context saying
  which profile applies to its repo, what it may do without asking, and what asks you first, so a worker doesn't
  stop to ask about what the policy allows. It comes back after `/clear`, `/resume` and compaction.
- **The card reminder** (`card-reminder.py`, a Stop hook): when a session tries to stop and files in its worktree changed during the session after its card was last written, it sends the session back once to update the card. The second stop always goes through. Quiet outside git repos, without tower set up, and for sessions that changed nothing (like the hub).
- **The card resume** (`card-resume.py`, UserPromptSubmit and PostToolUse on AskUserQuestion): the moment you
  answer a session, by typing or in its pop-up, a card that says `needs-you` is re-rendered by tower's core as `running` (the
  answer itself is recorded by the event log hook). The body and the other properties stay; the session rewrites the
  rest when it next stops. Without it, a card stayed
  needs-you for the whole stretch between your answer and the session's next stop.
- **The needs-you notification** (`notify-needs-you.py`, a Notification hook): a macOS notification titled with the project and branch ("tower · poot" / "rolly-polly") when a session waits on an approval, a reply you haven't read, or input. The first one may ask you to allow notifications from Script Editor, which is what `osascript` shows up as.
- **The event log** (`record-event.py`, on SessionStart, UserPromptSubmit, Notification, PostToolUse on
  AskUserQuestion, Stop and SessionEnd): appends one line per event (`session-start`, `prompt`, `needs-you`,
  `answer`, `stop`, `session-end`) to `~/.claude/tower/events.jsonl`, one file for every repo, tagged with repo,
  worktree and branch. It records the length of your prompts, never their text. `tower state` reads its last 24h
  when agent view can't be read, and `tower stats` computes metrics from it. It never blocks or fails a session:
  problems go to `~/.claude/tower/errors.log`. To look at it from this checkout: `uv run tower events tail -n 20`.
- **Runtime variables** (`session-env.py`, a SessionStart hook): when the project has a runtime config, sets the
  worktree's `TOWER_SLOT`, `PORT` and the rest for every Bash command in the session (through Claude Code's
  `CLAUDE_ENV_FILE`) and tells Claude its port and seed command. Quiet for projects without one. See "Running several
  dev servers at once".
- **Metrics** (`uv run tower stats [--since 14d] [--repo [PATH]]`): one row per PR merged in the window, from the
  event log and GitHub (through `gh`): start → merged, your active minutes, time waiting on you, interruptions and
  the median time to resume, follow-up prompts per report, and rework (later PRs for the same ticket or branch, and
  reopened tickets). Every repo in the log by default; `--repo` limits it to one. Each metric's exact definition is
  in `src/tower/stats.py`.

## Approvals: public actions and rule changes

The plugin's hook runs before every Bash, Edit, Write, and MultiEdit call. When the call would post or change
something on GitHub, push, or change your standing rules, and this repo's policy says that asks, Claude Code shows
you an approval prompt, even if your allow list (`Bash(git *)`) would normally let it through. Decline, and the
agent must ask you instead of trying another way. The prompt names the profile: `Public action or rule change
(tower policy: work) — approve?`.

| Action | Policy key | work | personal | sandbox |
|---|---|---|---|---|
| `git push` | `push` | every push | force pushes, deletes, `+refspec` | never |
| `gh pr merge` into the repo's default branch | `merge_to_main` | asks | asks | — |
| `gh pr merge` into any other branch (a stack, an integration branch) | `merge_to_branch` | asks | — | — |
| `gh pr` / `gh issue` comment, `gh pr review` | `comments` | asks | asks | — |
| `gh issue` create, edit, close, reopen, delete, transfer, … | `issue_writes` | asks | — | — |
| `gh pr` create, edit, ready, close, reopen, update-branch (an edit with `--base` also needs `merge_to_main`) | `pr_writes` | asks | — | — |
| Removing finished worktrees (`tower clean`, coming in T10) | `cleanup` | asks | asks | — |
| `gh api` writes, `gh release` / `gh label` writes, `gh pr lock`/`unlock`, a gh subcommand from a variable | none: ask unless every GitHub key is `auto` | asks | asks | — |
| Commands it can't check: an unclosed quote or `$(`, a shell reading code it can't see (a pipe, `<( )`, xargs) | none: ask unless every profile a repo can get lets everything through | asks | asks | — (asks if a `[repos]` entry is stricter) |
| **Safety floor:** any edit to the policy file, `tower.conf`, a rule file or a `CLAUDE.local.md`; Bash commands naming a rule file, writing `tower.conf` or anything in `~/.claude/tower/` (reading with `cat`, `grep`, `tail` is fine), running `tower policy use` (or code using tower's policy module) or tower's `setup.sh` (other than `--dry-run`), which rewrites `tower.conf` and sets the profile | always | asks | asks | asks |

For a merge, the hook asks `gh` which branch the PR goes into (`gh pr view`, then `gh repo view` for the default
branch), with the same PR and `-R` the merge uses, but only when the two merge keys differ and the merge is the
whole command (`gh pr merge 5 --squash --delete-branch`, nothing chained before it). Otherwise, or when `gh` can't
say, it asks unless both keys are `auto`. A repo is judged by every remote it has (fetch and push URLs; the strictest
profile wins), so re-pointing or adding a remote can only tighten it. A command that reaches another repo gets the
stricter of that repo's policy and this one's, key by key: a `gh` command naming one (`-R owner/repo`, a PR or issue
URL, a `gh api repos/OWNER/REPO/...` endpoint), a `git push` to a URL, `git -C`, or a `cd` into another checkout. A
repo or folder from a variable gets `work`. Reads (`gh pr view`, `gh pr checks`, `gh api` GETs,
GraphQL queries) never ask.

The hub can't approve for you: the prompt appears in the worker's session. The worker puts its draft in its card
first, and the hub lists it under **Needs you** with the session's name.

The hook reads a command the way the shell would: quotes, `&&`/`;`/`|`/newlines, `$( )`, heredocs, global options
(`git -C <path>`, `gh pr -R owner/repo`), attached flags (`gh api -XPOST`), and wrappers (`bash -c`, `env`,
`xargs`, `eval`, `sudo`, `ssh`, `watch`, ...). Text that only mentions a command (`git commit -m "fix git push"`) doesn't ask.
Why it has its own parser instead of `shlex`: `docs/adr/0003-approval-hook-splits-commands-itself.md`.

**Limits:** the hook sees the command an agent runs, not what happens inside a script the agent wrote. A Python
script calling the GitHub API directly isn't caught, and neither are commands built at run time (`eval "$(...)"`,
`$CMD push`), git/gh aliases, or deliberately disguised names. A plain push straight to `main` is judged by `push`,
not `merge_to_main` (GitHub branch protection refuses it, T03). The safety floor matches text too: a protected file
reached by a path built at run time (pieces joined in a script, a glob) isn't caught, so Claude Code's own permission
rules or sandbox are the backstop there. The full list is in `confirm-public.py`'s docstring.
`pr-wait` itself only reads.

## Policy profiles: `~/.claude/tower/policy.toml`

Every gate above is a key of a **profile**, and the policy file says which profile each repo gets. Built in:

- **`work`**: everything asks. The strictest, used whenever nothing else applies or something can't be read.
- **`personal`**: only force pushes, merges into `main`, comments and cleanup ask. Plain pushes, merges into other
  branches, PR changes and issue writes go through.
- **`sandbox`**: nothing asks (a throwaway VM or repo), except the safety floor.

```toml
default_profile = "personal"          # this machine's profile

[profiles.personal]                   # change some keys of a built-in profile...
wip_limit = 4

[profiles.client]                     # ...or define your own; keys it leaves out are the strictest value
push = "force"
issue_writes = "auto"

[repos]                               # per-repo overrides: "owner/repo" (from the repo's origin) or a glob on it
"accenture/*" = "work"
"rishi-desai/scratch" = "sandbox"
```

Keys: `push` (`every` | `force` | `none`), `merge_to_main`, `merge_to_branch`, `comments`, `issue_writes`,
`pr_writes`, `cleanup` (each `ask` | `auto`), and `wip_limit` (a whole number; without one, `tower.conf`'s
`wip_limit` applies, else 3).

Which profile a repo gets: the `[repos]` pattern matching its `owner/repo`, read from every remote's URL (with several
repos, the stricter value of each key) (an exact name beats a glob; the glob with
more literal characters beats the others; a tie takes the stricter value of each key), else `default_profile`, else
`work`. An unknown profile name gives `work`, an invalid value the strictest value of its key, and a file that can't
be read gives `work` everywhere; `tower policy` lists each problem with its line (and `tower doctor`, T20, will too).
The file is read with a small reader for the part of TOML it needs (the hooks run on macOS's Python 3.9, which has no
TOML library): tables, quoted keys, strings, whole numbers and comments; arrays, inline tables and dotted keys are
reported, not read.

- **`tower policy`** shows the profile for the repo you're in, why it applies, every key, and the problems.
- **`tower policy use <profile> [--repo]`** switches this machine's default (or, with `--repo`, this repo's line in
  `[repos]`), changing only that one line. Run it in your own terminal: the hook asks before an agent runs it, and
  before an agent edits the file in any way, in every profile.
- **Moving from `tower.conf`**: `push_approval` and `pr_approval` used to live in `tower.conf`. The first time tower
  runs without a policy file, it writes one with a `migrated` profile that does exactly what they did (or `work`, if
  both were `every`), and from then on they're ignored. `setup.sh` does the same before it rewrites `tower.conf`.

From this checkout, before the plugin is updated: `uv run tower policy`.

## Security model

- **Instructions and code live where agents can't silently change them.** The plugin is installed from GitHub into `~/.claude/plugins/cache/`, and your rules are in `~/.claude/tower/rules/`; Claude Code protects `~/.claude`. This matches the dotfiles' rule of copying, never symlinking, Claude config.
- **Don't run the plugin from a local folder day to day.** `setup.sh --plugin-source <path>` makes Claude Code read the plugin straight from that folder, which agents *can* edit. It's for testing changes before pushing them.
- **Cards are data, not instructions.** They live in the vault and agents write them; the hub only reports what they say.

## Personal project rules (never committed)

Team repos often have their own `CLAUDE.md` and `.claude/settings.json`. tower never edits those.

- **Rules** (code style, shortcuts): ask "remember for this project: …". `tower:project-rules` adds one line to the worktree's `CLAUDE.local.md` (`@~/.claude/tower/rules/<Project>.md`) and adds your rule to that file after you approve. `CLAUDE.local.md` is git-ignored (setup adds it to your global git ignore), and the script refuses to write if it isn't.
- **Hooks** (formatters, linters): `.claude/settings.local.json` in the repo, which Claude Code git-ignores.
- Rules stay on the machine they were made on: work-project rules never leave the work Mac.
- The first time a project imports a file from outside the repo, Claude Code asks you to approve the import once.

## Running several dev servers at once

Worktrees share one machine: every worker's dev server wants the same port, and they all point at the same database.
Declare a project's resources once, outside the repo, in `~/.claude/tower/projects/<Project>.toml` (`<Project>` is
the project folder name the cards use: the repo's name, or its `project.<repo>=` mapping):

```toml
port_base = 3000                  # PORT = port_base + slot
db_name = "poot_{slot}"           # DB_NAME
seed = 'cp -R {main}/data data'   # how a new worktree gets what only the main checkout has (printed, never run)

[ports]                           # more ports, each base + slot
API_PORT = 8000

[env]                             # any other variables
API_URL = "http://localhost:{port}/api"
```

- **Runtime slot**: each worktree's stable number, 0 for the main checkout and the smallest free one (1, 2, …) for
  every other worktree. It's kept in `~/.claude/tower/runtime-slots/<Project>.json` for as long as the worktree's folder
  exists, so a worker's port never changes under it; removing a worktree frees its slot, and the next new worktree
  takes it, with the same `DB_NAME` (drop or reset that database if the old worker's data shouldn't carry over). Not
  to be confused with an execution slot, which is what the WIP limit counts. In a bare-repo layout, the bare folder
  counts as the main checkout (as everywhere in tower), so no worktree gets slot 0.
- **In every Claude session**, the plugin's SessionStart hook (`session-env.py`) sets `TOWER_SLOT`, `PORT` and the
  rest for all of that session's Bash commands, and tells Claude its port and the seed command. Nothing to put in the
  kickoff.
- **In your own terminal**: `eval "$(uv run tower env)"` from this checkout, or
  `eval "$(python3 -I <plugin>/scripts/run-tower.py env)"` anywhere, in the worktree.
- Placeholders: `{slot}`, `{port}` (needs `port_base`), `{main}` and `{worktree}` (paths; shell-quoted for you in
  `seed`). The file is a small subset of TOML (whole numbers, "basic" or 'literal' strings, `#` comments, the
  `[ports]` and `[env]` tables) because the hooks run under macOS's Python 3.9; anything else is an error naming the
  line. Your app still has to read `PORT` and `DB_NAME` (most dev servers read `PORT` already).
- **Poot**: `data/` exists only in the main checkout. Set `seed` to whatever copies or links it into a worktree;
  tower prints it (`tower env`, and the session's start) and never runs it, because only you know whether a copy or a
  shared link is safe. A starting point for `~/.claude/tower/projects/Poot.toml` (a copy, so a worker can't change
  main's data; adjust the names to what Poot's dev server reads):

  ```toml
  port_base = 3000
  db_name = "poot_{slot}"
  seed = 'test -e data || cp -R {main}/data data'
  ```

## Configuration: `~/.claude/tower.conf` (one per machine)

```ini
projects_dir=/Users/rishi/Library/Mobile Documents/com~apple~CloudDocs/Vault/Projects
wip_limit=3                # unless the repo's profile sets its own
# every project's worktrees, one subfolder per repo (match Orca's); comments go on their own line, as here
worktrees_dir=/Users/rishi/worktrees
project.poot=Poot          # optional: repo folder name -> project folder name
```

Plain `key=value`, read as data (never executed). Edit by hand or re-run `setup.sh`, which keeps your
`project.*` lines. Approvals moved to the policy file (above); `push_approval` and `pr_approval` here are migrated
once and then ignored.

## Where everything lives

| Path | What |
|---|---|
| `<projects_dir>/<Project>/Ledger/<branch>.md` | status cards (`/` in branch names becomes `__`; detached HEAD → `detached-<sha>`) |
| `<projects_dir>/<Project>/Ledger/_backlog.md` | parked ideas, only for repos without GitHub |
| `<worktrees_dir>/<repo>/<worktree>` | every project's worktrees, outside the repos (Orca: Workspace Directory, Nest Workspaces on) |
| `~/.claude/tower.conf` | per-machine settings |
| `~/.claude/tower/policy.toml` | which approvals ask, per profile and per repo |
| `~/.claude/tower/rules/<Project>.md` | your personal rules for that project |
| `~/.claude/tower/projects/<Project>.toml` | that project's runtime config: ports, database name, seed command (optional) |
| `~/.claude/tower/runtime-slots/<Project>.json` | each worktree's runtime slot (written by tower; delete it to hand them out again) |
| `~/.claude/plugins/cache/tower/…` | the installed plugins (skills, scripts, hooks; the pane) |
| `~/.claude/tower/events.jsonl` | the event log, this month (UTC); earlier months are `events-YYYY-MM.jsonl` beside it |
| `~/.claude/tower/errors.log` | anything the event-log hook couldn't do (it never fails the session instead) |
| `~/.claude/CLAUDE.md` | the tower block; your original is in `CLAUDE.md.pre-tower.bak` |
| `~/.local/state/pr-wait/` | `pr-wait`'s lock and event memory |

Repo layout:

```
tower/
  .claude-plugin/marketplace.json     this repo is its own marketplace, with one plugin: tower
  plugin/
    .claude-plugin/plugin.json        its `types` names types/index.d.ts, the pane's saved state
    hooks/hooks.json                  command hooks (approval, card reminder, card resume, needs-you notification,
                                      event log, runtime variables) and, under `modules`, the pane mod
    hooks/register.tsx                the pane mod: the pane, status line, toasts, Orca jump, the back-to-hub line
    types/index.d.ts                  the pane's saved state, as Claude Code checks it
    tests/                            claude plugin test plugin; fixtures/ holds `tower state --json` output tests/test_state.py checks
    scripts/                          confirm-public.py, card-reminder.py, card-resume.py, notify-needs-you.py, record-event.py, session-env.py, run-tower.py (the core's CLI, for the skills and the pane)
    lib/tower -> ../../src/tower      symlink: how hooks import the core (a marketplace install copies its target)
    skills/                           hub/ (ADOPT.md: its adopt mode) status-card/ project-rules/ babysit-pr/
                                      leaving-pr-comments/ adopt/ (an alias for the hub's adopt mode, for one release)
  src/tower/                          tower's core (standard library only): the event log, effective state, the scans, the `tower` CLI
  tests/                              pytest: the approval hook's cases, the event log, effective state, the scans, the CLI, setup.sh's install
  GLOSSARY.md  docs/adr/              tower's terms, and the decisions behind its design
  mise.toml  pyproject.toml  uv.lock  tool versions and the `ci` task; ruff, mypy and pytest settings
  .github/workflows/ci.yml            calls rishi-desai/ci's shared workflow, which runs `mise run ci`
  setup.sh  CLAUDE.global.md  README.md  HANDOFF.md
```

## Checks and CI

`mise run ci` runs every check, and GitHub runs the same task on each pull request through
[rishi-desai/ci](https://github.com/rishi-desai/ci)'s shared workflow, so a local pass means CI passes. It runs ruff
(lint and format), mypy (strict), pytest, shellcheck on every `.sh`, gitleaks (secret scan),
`claude plugin validate --strict` on the marketplace and the plugin, and the pane's tests (`claude plugin test plugin`,
which needs no login). Run `mise install` once first.

- **The approval hook's tests** live in `tests/test_confirm_public.py`. A known bypass goes in as an `xfail` test:
  pytest fails the run as soon as one starts passing, so whoever fixes a bypass removes its marker.
- **The pane's type check isn't in `ci`.** Its type stubs (`plugin/.claude-plugin/types/`) are written by Claude Code
  only when a logged-in session loads the plugin, which CI can't do. Start `claude --plugin-dir plugin` once, then run
  `mise run pane-typecheck`.

## Using it on the work laptop

- Run `setup.sh --profile work` there and point `projects_dir` at the work vault (`~/Vault` → OneDrive). Work notes stay in company storage.
- On a team repo the hub lists only issues assigned to you, and nothing is written into the repo.
- `babysit-pr` waits on the repo's required checks. Every comment or push stops for your approval, and comments are labelled as AI-written.
- The repo is on your personal GitHub account: `gh auth switch` to it for `marketplace add` / updates.

## Gotchas

- **Cards are only as fresh as the last time an agent wrote one.** The hub flags `running` cards untouched for 24h as `STALE`, and `tower state` trusts live session status over a card.
- **Agent view's JSON format isn't documented.** If it changes, the hub prints one "skipping live sessions" line and works from cards alone.
- **Worktrees share runtime state:** ports, databases, environment variables, and git-ignored folders. A project's runtime config gives each worktree its own port and database name (see "Running several dev servers at once"); without one, they still clash. In Poot, `data/` exists only in the main checkout, so worktrees don't have it until you run the config's `seed` command.
- **"Commits not on main" isn't proof work is missing from main** (squash merges, cherry-picks). Adopt mode checks the actual diff before calling a branch unshipped.
- **Orca names the branch, not the hub.** `--name feat/42-x` is Orca's display name; with Orca's Branch prefix set to
  your git username the branch is `rishi-desai/feat-42-x`, and the card is named after that branch. Set the prefix to
  none in Orca's settings if you want the two to match.
- **Keep worktrees out of the repo.** With Orca's workspace folder set to `./worktrees`, every worktree sits inside
  the main checkout, which then shows `worktrees/` as untracked. `setup.sh` asks for one global worktrees folder
  instead (`worktrees_dir` in `tower.conf`; `--worktrees-dir` for scripted runs; Tab completes the path) and creates
  it. Orca's CLI can't set its own folder, so set it once in Orca: Settings > General > Workspace > **Workspace
  Directory**, with **Nest Workspaces** on so each repo gets its own subfolder. For worktrees already inside a repo,
  add `worktrees/` to main's `.git/info/exclude` (local, never committed), not to `.gitignore`.
- **Background sessions** (`claude --bg`) create their own worktrees under `.claude/worktrees/`. Add that path to the repo's ignore file if it shows up in `git status`.
- **Project names can't contain spaces** (the rules import path would break). Map to a name without spaces with `project.<repo>=`.
- Card time comes from `date`, never from the model's memory, because models often get today's date wrong.

## Updating and uninstalling

- **Update tower:** commit and push changes here, then on each Mac `claude plugin marketplace update tower && claude plugin update tower@tower`. Bump `version` in `plugin/.claude-plugin/plugin.json` so installs notice. Re-run `setup.sh` if `CLAUDE.global.md` changed.
- **Change the pane:** edit `plugin/hooks/register.tsx`, run `claude plugin validate --strict plugin && claude plugin test plugin`, and try it with `claude --plugin-dir plugin` before pushing: for that session the checkout replaces the installed
  tower (Claude Code logs `Plugin "tower" from --plugin-dir overrides installed version`), so nothing runs twice.
- **Update babysit-pr from upstream:** see `plugin/skills/babysit-pr/SAFETY.md`: diff against the pinned commit, re-read, re-apply the local patches, update the fingerprints.
- **Uninstall:** `claude plugin uninstall tower@tower` and `claude plugin marketplace remove tower`; delete `~/.claude/tower.conf` (and `~/.claude/tower/rules/` if you don't want your rules); remove the marked block from `~/.claude/CLAUDE.md` (or restore `CLAUDE.md.pre-tower.bak`). Your cards stay in the vault.

## Credits

- Skill base: [Matt Pocock's skills](https://github.com/mattpocock/skills) (Claude Code plugin `mattpocock-skills`).
- `babysit-pr`, `leaving-pr-comments`: [t3dotgg/tstack](https://github.com/t3dotgg/tstack), MIT, vendored at `1085050` with local patches.
- Working principles: [Karpathy-inspired guidelines](https://github.com/multica-ai/andrej-karpathy-skills).
- Design notes and the decisions behind them: `HANDOFF.md`.
