# tower

A way to run many Claude Code agents in parallel, on personal and work projects alike, without losing track of
which session owns what, what needs you, and how the pieces fit together.

Each agent works one ticket in its own git worktree and leaves a short **status card** in your Obsidian vault.
One **hub** session reads only those cards plus git, GitHub, and Claude Code's live session list, and tells you
on one screen what needs you and what to do next. Nothing it uses is committed to any repo, so it is safe on a
shared work repo. Already have a messy repo? **adopt** brings it under tower without losing a change.

tower ships as a **Claude Code plugin** (skills + an approval hook) plus a small per-machine `setup.sh`.

---

## The four rules

1. **One ticket = one branch = one worktree = one main session.** Subagents inside a worktree are fine; two sessions editing the same worktree are not.
2. **WIP limit** (work in progress): at most N worktrees running or waiting on you (3 personal, 4 work, set per machine). New ideas get parked as tickets, not started.
3. **The hub never reads transcripts.** All state is on disk, so the hub is disposable: `/clear` it and run it again any time.
4. **Nothing public without your OK.** Comments, PR changes, pushes, and edits to your standing rules stop for your approval.

## How it works

```
 worktree A ──┐  status-card skill writes          ┌─> Vault/Projects/<Project>/Ledger/feat__42-x.md
 worktree B ──┼─ a card when it stops ─────────────┼─> Vault/Projects/<Project>/Ledger/fix__51-y.md
 worktree C ──┘                                    └─> ...
                                                          │
 main checkout: /tower:hub ── hub-scan.sh reads ───────────┤ cards
                                                           ├ git worktree list
                                                           ├ claude agents --json  (live: busy / idle / waiting)
                                                           └ gh: your PRs, open issues
                     └─> one screen: Needs you → Blocked → Running → Done → Housekeeping → ONE next action
```

- **Status card**: a ~25-line Markdown note per worktree: status (`running`, `needs-you`, `blocked`, `done`), ticket, PR, what's done, why it matters, the one decision it needs from you, how to verify. Its top block is YAML frontmatter, so Obsidian shows the fields as properties.
- **Hub**: a session in the repo's main checkout that you ask "what's going on?" or "what's next?". It reports in a fixed order and ends with one suggested action.
- **Live sessions**: the hub also reads Claude Code's agent view (`claude agents --json`), so it sees a session waiting on you even if its card is stale, and flags a worktree with several sessions in it.

## Install

On each Mac (after Claude Code, `gh`, and git are set up):

```bash
gh repo clone rishi-desai/tower ~/Developer/personal/tower
~/Developer/personal/tower/setup.sh --dry-run     # shows every change; changes nothing
~/Developer/personal/tower/setup.sh               # asks: projects folder, WIP limit, push and PR approval
```

`setup.sh` writes `~/.claude/tower.conf`, creates `~/.claude/tower/rules/`, adds `CLAUDE.local.md` to your
global git ignore, refreshes the tower block in `~/.claude/CLAUDE.md`, and installs the two plugins:

```bash
claude plugin marketplace add rishi-desai/tower
claude plugin install tower@tower          # skills, approval hook, card hooks
claude plugin install tower-pane@tower     # optional: the live pane (see "The tower pane")
# or, in one step inside Claude Code 2.1.275+:  /plugin install tower --marketplace rishi-desai/tower
```

If an organization's policy refuses mods, the pane install fails and `setup.sh` says so and carries on: everything
else works without it.

Restart Claude Code afterwards. Re-run `setup.sh` any time to change settings; it keeps what it doesn't own.

tower is independent of your dotfiles: on a new Mac, after your usual setup, clone this repo and run `setup.sh`.
If the plugin is already installed (for example from a restored `settings.json`), step 5 notices and skips it.

## How to use it

### First time in a repo

1. Open one Claude Code session in the repo's **main checkout** (in Orca: the main worktree). This is your **hub**. Use it only to check status and start work, never to write code.
2. If the repo already has work in flight (several sessions in one checkout, worktrees with no cards, uncommitted changes in main), type `/tower:adopt` and follow its plan. See [Adopting an existing repo](#adopting-an-existing-repo). Otherwise type `/tower:hub`.
3. If the project's vault folder isn't named like the repo folder, add `project.<repo>=<Folder>` to `~/.claude/tower.conf` (e.g. `project.poot=Poot`).

### Starting a ticket

1. In the hub: **"what's next?"** It recommends one unblocked ticket and says why.
2. **"start #42"**. It replies with a branch name (`feat/42-discord-tracker`), an effort level with its reason, and two lines to paste.
3. Create the worktree with that branch name in Orca, and open a **new** Claude session in it.
4. Paste the two lines: `/effort medium` (or whatever the hub picked), then `/implement #42 — …`.
5. Leave it alone. Whenever it stops, it writes its card and ends its message with **What happened / Done / Why it matters / Needs you / Verify**, written so you can follow it without having watched the session. When it needs a decision, it asks in Claude Code's question pop-up (recommended option first), so you answer by picking, not by typing a new prompt.

If the hub says `AT LIMIT`, finish or pause something first, or park the new ticket. It won't refuse if you insist.

### Checking in

- In the hub: **"what needs me?"** (or `/tower:hub`). You get one screen, ordered Needs you → Blocked → Running → Done → Housekeeping, and one suggested next action.
- Each "Needs you" item names the session to go to. Answer there, not in the hub.
- Or open the live pane with `/tower` (see below): the same picture, refreshed every 30 seconds, where a click
  jumps to the session in Orca and pop-up questions can be answered in place.
- In Obsidian: `Projects/<Project>/Ledger/` has one note per worktree, with `status`, `ticket`, `pr` and `updated` as properties.
- If the hub's context gets long, `/clear` it and ask again. Everything it reads is on disk.

### The tower pane

`tower-pane` is a mod: a panel inside Claude Code, beside the chat. Run `/tower` in the hub session to open it.
It never reads transcripts either; it runs the hub's own scan.

- **Status line:** `tower WIP 2/3 · 1 needs you` in every session of the repo, and a toast when something new needs you.
- **Needs you:** cards that say needs-you, and sessions waiting on you. A card whose session is working again
  (you already answered) moves to Worktrees and says "still says needs you".
- **Click a worktree name** (or press 1–9 on a Needs you row): Orca switches to that worktree's Claude tab,
  the one that printed last, and comes to the front. **card** opens the status card in Obsidian.
- **Pop-up questions:** when a session running the pane asks you something in its pop-up, the question shows up
  in the pane within about 3 seconds, with one button per option. Click one and the answer goes back to that
  session, as if you'd picked it there. The first answer wins, the pane's or the pop-up's. Questions that allow
  several answers, or a typed "Other", are answered in the session.
- **Approvals never go through the pane.** A push or a GitHub comment is approved only in the session doing it,
  so nothing else can approve on your behalf.
- **Buttons:** Refresh (r), Refresh GitHub (g, which loads PRs and ready-for-agent issues; click an issue's number
  to start it), What's next? (n), Nudge (asks a cardless session to write its card), and Park an idea.

Anything that changes something (starting a ticket, parking an idea) is sent to the hub's Claude as a request,
so the usual approval prompts still apply.

### When an agent wants to post, push, or change a rule

The agent shows you the exact text in its chat, puts the draft in its card, and Claude Code shows an approval
prompt in **that** session. Approve or decline there; the hub also lists it under "Needs you". To stop being
asked about pushes, set `push_approval=force` (force pushes only) or `none` in `~/.claude/tower.conf`. To let
workers open and merge their own PRs on this machine, set `pr_approval=none` (see Shipping).

### When you think of something else mid-flow

Tell the hub **"park: worm theme for the dashboard"**. It files a GitHub issue (after you approve) or adds a line
to `Ledger/_backlog.md`, then goes back to what you were doing. Nothing starts until you pick it.

### Shipping

1. In the worker session: `/code-review high` (or `/code-review xhigh` if it touches security). Fix what it finds.
2. **"open the PR"**. It drafts the title and description and asks before creating it.
3. **"babysit the PR"**. It watches your repo's checks and review comments, fixes real failures, and asks before every reply or push.
4. When checks are green, its card says `done` with the PR number. You merge.

With `pr_approval=none` in `~/.claude/tower.conf` (per machine; e.g. personal Mac on, work laptop off), steps 2–4
collapse: once `/code-review` passes, the worker opens the PR and squash-merges it itself after the checks pass,
with no prompts and no babysitting. The hub's kickoff line says so. Comments, reviews and issue changes still ask.
Set it back to `every` to review PRs again.

### Finishing up

- After merging, remove the worktree in Orca. The hub will list its card as an orphan; say "yes, delete it" when it suggests that.
- Stopping for the day: `/handoff` in any session you'll resume. The cards stay either way.
- Want an agent to remember a preference for this project? Say **"remember for this project: use pytest fixtures"**. It asks before saving it to `~/.claude/tower/rules/`.

### Things to say to the hub

| Say | You get |
|---|---|
| "what needs me?" | the one-screen report |
| "what's next?" | one unblocked ticket, preferring `ready-for-agent`, with a reason |
| "start #42" | branch name, effort level, and the kickoff lines to paste |
| "park: <idea>" | a GitHub issue (asks first) or a backlog line |
| "what should I pause?" | which running item to pause to get under the WIP limit |
| "clean up" | housekeeping suggestions: stale cards, orphans, merged worktrees. Nothing is deleted without your yes |

### If something looks wrong

| You see | Do |
|---|---|
| "no projects_dir … run setup.sh" | run `setup.sh` on this machine |
| `STALE` on a card | go to that session and ask it to update its card, or check whether it has quietly died |
| "skipping live sessions" | fine; the hub falls back to cards only |
| a worktree with `NO CARD` | ask that session to "write your status card", or run adopt |
| no approval prompt before a comment or push | check that the plugin is enabled (`claude plugin list`) and tell Claude; don't rely on it until fixed |

## Daily loop

| Step | Where | Command | Effort |
|---|---|---|---|
| 1. Sharpen the idea | any session | `/grill-me` or `/grill-with-docs` | high |
| 2. Write it down | same session | `/to-spec`, then `/to-tickets` | medium |
| 3. Pick a ticket | hub | "what's next?" | — |
| 4. Build it | new worktree, new session | paste the hub's kickoff: `/effort <level>`, then `/implement #42` | hub picks: medium / high / xhigh |
| 5. Check status | hub | `/tower:hub` | — |
| 6. Verify | the worktree's session | `/code-review high` (`xhigh` if it touches security) | high / xhigh |
| 7. Ship | same session | open the PR (asks you), then "babysit the PR"; with `pr_approval=none` the worker merges it itself | — |
| 8. Stop for the day | each session | `/handoff` | — |
| Weekly | any | `/retro` to improve skills, checks, and gotchas from real failures | — |

"Effort" is Claude Code's reasoning effort: spend less to build, more to verify. tower never goes below `medium`.

### How effort is chosen

Based on Thariq's [Spending your effort](https://claude.dev/blog/spending-your-effort/): higher effort buys more
verification and edge-case testing, and more decisions made without asking you. It doesn't fix a wrong
approach, which is what grilling and the spec are for.

| Where | Effort | Why |
|---|---|---|
| `status-card`, `project-rules`, `hub` | medium | in-the-loop, quick work |
| `adopt` | high | must not lose a change, but you approve each step, so it shouldn't assume more |
| `babysit-pr` | high | verifying bot findings and fixing CI in existing code, often while you're away |
| worker: feature slice from a clear ticket | medium | "most regular software engineering work" |
| worker: bug fix in existing code, or runs while you're away | high | "verification is important or there are edge cases" |
| worker: security, concurrency/storage, performance, money/risk logic | xhigh | the benchmark tasks that only passed at xhigh were edge-case-heavy; security gained most |
| verification | high, or xhigh for security | "code review and security" benefit most |

- **Skills set their own level** with `effort:` in their frontmatter, overriding the session while they run. Edit that line to change one.
- **Worker sessions:** a skill can't set a whole session, so the hub puts `/effort <level>` first in the kickoff prompt, with a one-line reason.
- `CLAUDE_CODE_EFFORT_LEVEL`, if you ever set it, overrides all of the above. `max` isn't used by default.

## Adopting an existing repo

`/tower:adopt` (run from the main checkout) is for a repo where work is already in flight and you've lost track.

1. **Scan (read-only).** `adopt-scan.sh` lists, per worktree: card or not, uncommitted files, commits ahead/behind `origin/<default>`, last commit, PR, ticket guess; the live sessions in each worktree; and branches with unmerged work but no worktree (with their PR state).
2. **One plan, which you approve.** In order:
   - a **paste-in prompt for every live session**: it writes its own first card and, if it's in the main checkout, lists exactly which uncommitted files are its work;
   - **untangling the main checkout**: each session's files move to their own branch + worktree (`git stash push -- <its files>` → `git stash pop` in the new worktree; never `reset`, `checkout .`, or `clean`);
   - **draft cards** from git facts for worktrees with no live session, marked `needs-you` until you confirm;
   - **WIP**: what to park first (merged or untouched longest), and cleanup suggestions;
   - **tickets** for work with no issue (creating one asks you).
3. **Steps run one at a time**, only the ones you approve. Nothing is deleted. It ends by running the hub.

## Skills

All skills are namespaced by the plugin: `tower:hub`, `tower:status-card`, and so on. Claude picks them by
description, or you type `/tower:<name>`.

### Run automatically (Claude decides from the skill's description)

| Skill | Fires when | What it does |
|---|---|---|
| `tower:status-card` | an agent finishes a chunk, stops for you, gets blocked, or opens/updates a PR | Finds this worktree's card in the vault (`card-path.sh`) and overwrites it with the current state. The card is the short version for the hub; the final chat message is the full version for you. |
| `tower:hub` | you ask what's going on, what needs you, what's next, to park an idea, or to start a worktree | Runs `hub-scan.sh` (which sorts worktrees into the report groups itself), reports in the fixed order, enforces the WIP limit, parks ideas as GitHub issues (or `Ledger/_backlog.md` without GitHub), picks the next unblocked `ready-for-agent` ticket, writes kickoff prompts. Suggests `adopt` when the repo clearly hasn't been adopted. |
| `tower:adopt` | you want to start tower on a messy, in-flight repo | See [above](#adopting-an-existing-repo). |
| `tower:project-rules` | you explicitly ask to remember a rule for this project | Adds your rule to `~/.claude/tower/rules/<Project>.md` (asks you) and links it into the worktree's git-ignored `CLAUDE.local.md`. |
| `tower:babysit-pr` | you say "babysit / watch / monitor the PR" | Runs `pr-wait` (one line per real event: check failed, comment, review, base moved, merged) and fixes real CI failures and review findings. Drafts every public action and waits for your approval. Waits on your repo's own required checks (`--until green`). |
| `tower:leaving-pr-comments` | any agent comment on GitHub | Starts each comment with "🤖 <model> responding on behalf of <you>", keeps it short, checks for secrets. |
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

**Which `/code-review`?** Several are installed. `/code-review high` (with an effort level) is Claude Code's built-in reviewer, the one the daily loop means. Matt's is `mattpocock-skills:code-review`; `/implement` calls it for you.

### Always on (not skills)

- **The CLAUDE.md block** (between `<!-- BEGIN tower -->` and `<!-- END tower -->` in `~/.claude/CLAUDE.md`): how agents report back (What happened / Done / Why it matters / Needs you / Verify, every name and term explained on first use), the four Karpathy working principles, the parallel-work rules, the approval rule, and keeping personal rules out of repos.
- **The approval hook** (`plugin/hooks/hooks.json` → `confirm-public.py`), active while the plugin is enabled. See below.
- **The card reminder** (`card-reminder.py`, a Stop hook): when a session tries to stop and files in its worktree changed during the session after its card was last written, it sends the session back once to update the card. The second stop always goes through. Quiet outside git repos, without tower set up, and for sessions that changed nothing (like the hub).
- **The card resume** (`card-resume.py`, UserPromptSubmit and PostToolUse on AskUserQuestion): the moment you
  answer a session, by typing or in its pop-up, a card that says `needs-you` is switched to `running` (only the
  `status` and `updated` lines). The session rewrites the rest when it next stops. Without it, a card stayed
  needs-you for the whole stretch between your answer and the session's next stop.
- **The needs-you notification** (`notify-needs-you.py`, a Notification hook): a macOS notification titled with the project and branch ("tower · poot" / "rolly-polly") when a session waits on an approval, a reply you haven't read, or input. The first one may ask you to allow notifications from Script Editor, which is what `osascript` shows up as.

## Approvals: public actions and rule changes

The plugin's hook runs before every Bash, Edit, Write, and MultiEdit call. When the call would post or change
something on GitHub, or change your standing rules, Claude Code shows you an approval prompt, even if your
allow list (`Bash(git *)`) would normally let it through. Decline, and the agent must ask you instead of trying
another way.

| Always asks | Depends on `push_approval` |
|---|---|
| `gh pr` comment, review; `gh issue` comment, create, edit, close, reopen, … | `every` (default): every `git push` |
| `gh release` / `gh label` writes | `force`: only force pushes, `+refspec`, branch deletes |
| `gh api` writes: GraphQL mutations, `-X POST/PATCH/PUT/DELETE`, or fields without `-X GET` (gh defaults to POST) | `none`: pushes never ask |
| Any edit to a `CLAUDE.local.md` or `~/.claude/tower/rules/*`, and Bash commands naming them | |

`gh pr` create, edit, ready, merge, close, reopen and update-branch depend on `pr_approval`: `every` (default)
asks, `none` lets a worker open and merge its own PR. Missing or unknown values count as `every`.

Change either mode by editing `push_approval=` or `pr_approval=` in `~/.claude/tower.conf` (takes effect on the
next command) or by re-running `setup.sh`. Reads (`gh pr view`, `gh pr checks`, `gh api` GETs, GraphQL queries) never ask.

The hub can't approve for you: the prompt appears in the worker's session. The worker puts its draft in its card
first, and the hub lists it under **Needs you** with the session's name.

**Limits:** the hook sees the command an agent runs, not what happens inside a script the agent wrote. A Python
script calling the GitHub API directly isn't caught. `pr-wait` itself only reads.

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

## Configuration: `~/.claude/tower.conf` (one per machine)

```ini
projects_dir=/Users/rishi/Library/Mobile Documents/com~apple~CloudDocs/Vault/Projects
wip_limit=3
push_approval=every
pr_approval=every          # none: workers open and merge their own PRs on this machine
project.poot=Poot          # optional: repo folder name -> project folder name
```

Plain `key=value`, read as data (never executed). Edit by hand or re-run `setup.sh`, which keeps your
`project.*` lines.

## Where everything lives

| Path | What |
|---|---|
| `<projects_dir>/<Project>/Ledger/<branch>.md` | status cards (`/` in branch names becomes `__`; detached HEAD → `detached-<sha>`) |
| `<projects_dir>/<Project>/Ledger/_backlog.md` | parked ideas, only for repos without GitHub |
| `~/.claude/tower.conf` | per-machine settings |
| `~/.claude/tower/rules/<Project>.md` | your personal rules for that project |
| `~/.claude/plugins/cache/tower/…` | the installed plugins (skills, scripts, hooks; the pane) |
| `~/.claude/tower/questions/<session id>.json` | a pop-up question waiting for an answer from the pane; removed once answered |
| `~/.claude/CLAUDE.md` | the tower block; your original is in `CLAUDE.md.pre-tower.bak` |
| `~/.local/state/pr-wait/` | `pr-wait`'s lock and event memory |

Repo layout:

```
tower/
  .claude-plugin/marketplace.json     this repo is its own marketplace: plugins tower and tower-pane
  plugin/
    .claude-plugin/plugin.json
    hooks/hooks.json                  approval, card reminder, card resume, needs-you notification
    scripts/                          ledger-lib.sh (shared), confirm-public.py, card-reminder.py, card-resume.py, notify-needs-you.py
    skills/                           hub/ status-card/ adopt/ project-rules/ babysit-pr/ leaving-pr-comments/
  pane/                               the tower-pane mod
    hooks/register.tsx                the pane, status line, toasts, Orca jump, question relay
    types/index.d.ts                  its saved state, as Claude Code checks it
    tests/                            claude plugin test pane
  setup.sh  CLAUDE.global.md  README.md  HANDOFF.md
```

## Using it on the work laptop

- Run `setup.sh` there and point `projects_dir` at the work vault (`~/Vault` → OneDrive). Work notes stay in company storage.
- On a team repo the hub lists only issues assigned to you, and nothing is written into the repo.
- `babysit-pr` waits on the repo's required checks. Every comment or push stops for your approval, and comments are labelled as AI-written.
- The repo is on your personal GitHub account: `gh auth switch` to it for `marketplace add` / updates.

## Gotchas

- **Cards are only as fresh as the last time an agent wrote one.** The hub flags `running` cards untouched for 24h as `STALE`, and trusts live session status over a card.
- **Agent view's JSON format isn't documented.** If it changes, the hub prints one "skipping live sessions" line and works from cards alone.
- **Worktrees share runtime state:** ports, databases, environment variables, and git-ignored folders. In Poot, `data/` exists only in the main checkout, so worktrees don't have it.
- **"Commits not on main" isn't proof work is missing from main** (squash merges, cherry-picks). adopt checks the actual diff before calling a branch unshipped.
- **Background sessions** (`claude --bg`) create their own worktrees under `.claude/worktrees/`. Add that path to the repo's ignore file if it shows up in `git status`.
- **Project names can't contain spaces** (the rules import path would break). Map to a name without spaces with `project.<repo>=`.
- Card time comes from `date`, never from the model's memory, because models often get today's date wrong.

## Updating and uninstalling

- **Update tower:** commit and push changes here, then on each Mac `claude plugin marketplace update tower && claude plugin update tower@tower && claude plugin update tower-pane@tower`. Bump `version` in `plugin/.claude-plugin/plugin.json` (and `pane/.claude-plugin/plugin.json` for pane changes) so installs notice. Re-run `setup.sh` if `CLAUDE.global.md` changed.
- **Change the pane:** edit `pane/`, run `claude plugin validate pane && claude plugin test pane`, and try it with `claude --plugin-dir pane` before pushing (uninstalled, or the installed copy loads too).
- **Update babysit-pr from upstream:** see `plugin/skills/babysit-pr/SAFETY.md`: diff against the pinned commit, re-read, re-apply the local patches, update the fingerprints.
- **Uninstall:** `claude plugin uninstall tower-pane@tower`, `claude plugin uninstall tower@tower` and `claude plugin marketplace remove tower`; delete `~/.claude/tower.conf` (and `~/.claude/tower/rules/` if you don't want your rules); remove the marked block from `~/.claude/CLAUDE.md` (or restore `CLAUDE.md.pre-tower.bak`). Your cards stay in the vault.

## Credits

- Skill base: [Matt Pocock's skills](https://github.com/mattpocock/skills) (Claude Code plugin `mattpocock-skills`).
- `babysit-pr`, `leaving-pr-comments`: [t3dotgg/tstack](https://github.com/t3dotgg/tstack), MIT, vendored at `1085050` with local patches.
- Working principles: [Karpathy-inspired guidelines](https://github.com/multica-ai/andrej-karpathy-skills).
- Design notes and the decisions behind them: `HANDOFF.md`.
