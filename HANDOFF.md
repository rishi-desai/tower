# Handoff: tower (parallel-agent workflow system)

Written 2026-11-04 from a planning conversation in Claude chat. You are picking this up in Claude Code. Read this whole file before acting.

## Who and why

Rishi is a software engineer (Georgia Tech CS, hand-coded for years). He is now a Forward Deployed Engineer (FDE) at Accenture building with AI on short timelines, and he builds personal projects the same way. He has ADHD (attention-deficit/hyperactivity disorder). He can run many agents at once, but he loses track of which session owns what, what's shared, and what needs him. He also finds agent outputs either walls of text or too thin, which leads to chains of follow-up questions that burn time and tokens.

**Goal:** one cohesive system for building with many parallel agents, the same for personal and work projects, where he stays the person who understands how all the pieces fit together.

## His two current contexts

- **Personal — Poot.** A personal options trading platform modeled on George: it reads analyst alerts from a Discord, analyzes them, manages portfolio and risk, tells him when to exit, and learns from the analysts' wins and losses. It also has a worm-themed UI (Poot is a worm plush). He currently runs one Orca project with worktrees (main, design, tracker) and several Claude sessions per worktree, including about four in main at once. That setup is the source of his confusion.
- **Work — agentic firewall platform** (Accenture, 3–4 people). The lead architect uses Matt Pocock's skills (Wayfinder) to create specs and GitHub issues, and assigns them. Rishi points agents at the tickets, verifies the work, and ships PRs (pull requests) as stacks; one stack was 16 PRs over 3 days. GitHub issues are the ticket system. It's unknown whether specs are committed to the repo. Do not add files to the shared work repo without asking.

## Decisions made

1. **One ticket = one branch = one worktree = one primary session.** Subagents inside a worktree are fine. Never several sessions editing the same worktree.
2. **WIP (work-in-progress) limit** set per machine in `~/.claude/tower.conf` (`wip_limit=`): 3 on the personal Mac, 4 on the work laptop. Edit the line to change it. New ideas get parked as tickets, not started.
3. **Hub and ledger instead of a relay session.** Workers write a short status card to disk. A hub session reads only cards, `git worktree list`, and `gh` output, never transcripts. The hub is disposable: `/clear` it and re-run, because all state is on disk.
4. **Ledger lives in the Obsidian vault, outside repos**, at `<projects_dir>/<Project>/Ledger/<branch>.md` (the `/` in a branch name becomes `__`; a detached HEAD becomes `detached-<sha>`). `projects_dir` is set per machine by `setup.sh` in `~/.claude/tower.conf`, so the personal Mac and the work laptop each point at their own vault. `project.<repo>=<Folder>` lines in that file map a repo to a differently named project folder. Nothing gets committed, and it works on the shared work repo without touching it.
4b. **Personal per-project rules never touch the repo.** Each worktree gets a one-line, git-ignored `CLAUDE.local.md` importing `~/.claude/tower/rules/<Project>.md` (protected by Claude Code; per machine, so work rules never leave the work Mac). Edits to rule files always go through the approval hook. Personal hooks go in `.claude/settings.local.json`. The team's `CLAUDE.md`/`settings.json` are left alone.
5. **Packaged as a Claude Code plugin in its own repo** (`rishi-desai/tower`, private, at `~/Developer/personal/tower` per the dotfiles home layout). Skills + approval hook ship in `plugin/`; the repo is its own marketplace. Reason: the dotfiles' `appsettings` mirrors `~/.claude/skills`, `CLAUDE.md` and `settings.json` with `rsync --delete`, which would delete or duplicate copied-in tower skills; plugins live under `~/.claude/plugins`, which appsettings never touches. Install from GitHub (copied into protected `~/.claude/plugins/cache`), never from a local folder day to day (Claude Code then reads the editable source folder directly). `setup.sh` does the per-machine rest.
6. **Report contract** for every final message: Done / Why it matters / Needs you (one question, lettered options, a recommendation) / Verify / define every term. This fixes all three of his output complaints: jargon, the missing "why," and unclear asks.
7. **Skill base: Matt Pocock's skills** (`/grill-me`, `/grill-with-docs`, `/to-spec`, `/to-tickets`, `/implement`, `/code-review`, `/handoff`, `/wait-what`), since his team uses them. Add Karpathy's 4 principles to the global CLAUDE.md. **Skip Superpowers**: it overlaps with Matt's skills and auto-triggers, so the two would conflict. tstack's `babysit-pr` (+ its dependency `leaving-pr-comments`) is vendored and bundled, reviewed line by line (see `skills/babysit-pr/SAFETY.md`); `file-pr` still later, if wanted.
8. **Stay on Orca** for now. Its gaps are covered by the hub. Alternatives were reviewed (Conductor, Superset, Emdash, Vibe Kanban, T3 Code), and none fixes the core problem better.
9. **Daily loop** (adapted from Thariq's effort article): grill → spec/tickets → `/implement` at low or medium effort → review cards → `/code-review` at high effort → PR → `/handoff` before stopping.
10. **Nothing public without approval.** The plugin's PreToolUse hook (`plugin/scripts/confirm-public.py`, matcher `Bash|Edit|Write|MultiEdit`) forces a Claude Code approval prompt for GitHub writes (comments, reviews, PR/issue changes, `gh api` writes) and for any edit to `CLAUDE.local.md` or `~/.claude/tower/rules/`. Pushes follow `push_approval=` in `tower.conf`: `every` (current, while he verifies it), `force`, or `none`. PR create/edit/ready/merge/close/reopen/update-branch follow `pr_approval=` (`every` default, or `none`; decided 2026-11-05: `none` on the personal Mac so workers squash-merge their own PRs after `/code-review`, `every` on the work laptop until he's happy with it); comments, reviews and issue writes always ask. The hub's kickoff line drops babysit-pr when it's `none`. The prompt shows in the worker's session; the worker puts its draft in its card first, and the hub lists it under Needs you. babysit-pr is patched to wait with `--until green` (the repo's own required checks) because neither repo uses the Greptile/Macroscope bots that `ready` waits for.
11. **Name: tower.** Repo, README, `~/.claude/tower.conf`, `~/.claude/tower/rules/`, CLAUDE.md block markers, env vars (`TOWER_CONF`, `TOWER_LIB`), skills `tower:<name>`.
12. **adopt** (`tower:adopt`) brings an in-flight repo under tower: read-only scan → one plan → approved steps one at a time; live sessions write their own first cards via a paste-in prompt; main-checkout work moves with `git stash push -- <files>` / `pop`, never destructive commands.
13. **Dotfiles: fully separate.** The dotfiles repo has no tower references (decided 2026-11-04). Its one related change is its own: `appsettings` saves `CLAUDE.md` per profile (work and personal differ on purpose; the old shared file moved to `exports/work/`). New-Mac setup for tower is documented only in this README: clone, run `setup.sh`.
14. **Effort is set, not remembered; never below medium** (his instruction), mapped from Thariq's "Spending your effort" (in ~/Downloads): status-card/project-rules/hub medium; adopt high (verification, but user approves each step so no extra assumptions); babysit-pr high (brownfield fixes, often unattended). Hub kickoff starts with `/effort` per ticket: medium for clear feature slices, high for bug fixes or unattended runs, xhigh for security, concurrency/storage, performance, money/risk logic. Verification `/code-review high`, xhigh for security. Thariq's rule of thumb has no xhigh row; xhigh is used where he'd say max, since max is excluded. The kickoff sets the level for the worker session only (`/effort` slider + `s`, or `claude --effort <level>`), never `/effort <level>`, which saves it as the default for all new sessions (found 2026-11-05: one `/effort xhigh` left every later session at xhigh).
15. **The tower pane** (decided 2026-11-05): the hub as a mod, `pane/` in this repo and a second plugin (`tower-pane@tower`) in the same marketplace, installed by `setup.sh` but optional (a failed install doesn't stop setup). Read-only apart from its question files; anything that changes something is sent to the hub's Claude as a prompt so approvals still apply. Clicking a worktree jumps to its Claude tab in Orca (`orca terminal list/switch`). Live session state outranks a card: a needs-you card whose session is busy shows as running.
16. **Pop-up questions can be answered from the pane; approvals can't** (decided and tested live 2026-11-05). The pane in each worker writes its AskUserQuestion pop-up to `~/.claude/tower/questions/<session id>.json`; the hub's pane shows it and sends the click back with `$.session.send`, tagged with a random id the worker checks. First answer wins. Approvals stay in their own session, so no other session can approve a public action.
17. **Cards flip back to running on an answer** (`card-resume.py`, UserPromptSubmit and PostToolUse on AskUserQuestion): rewrites only `status` and `updated`, so the hub stops listing a session as needing the user once it has the answer.
18. **The hub discusses and starts work through Orca** (#2, 2026-11-05). Ideas are discussed in the hub with grilling (no edits or commits on main) and end as a parked issue or a `/to-spec` spec (`ready-for-agent`); a spike gets an early worktree. "start #N" shows one `orca worktree create --repo path:<main> --name <type>/<N>-<slug> --no-parent --issue <N> --agent claude --model <model> --effort <level> --surface terminal --json --prompt '<kickoff>'` and runs it on a yes, then writes a starter `running` card; the two-step paste is the fallback when `orca status` isn't reachable. Checked live: `--effort` requires `--model`; `--name` is only Orca's display name (branch = Orca's Branch prefix + name with `/` → `-`); a `/implement` prompt runs as the skill; terminal-surface sessions show in LIVE SESSIONS. The kickoff names both reviews: `/implement` runs Matt's, then the built-in `/code-review high`.

## What was built (this repo)

```
tower/
  .claude-plugin/marketplace.json  # the repo is its own marketplace (plugin source ./plugin)
  plugin/.claude-plugin/plugin.json
  plugin/hooks/hooks.json          # approval hook -> ${CLAUDE_PLUGIN_ROOT}/scripts/confirm-public.py
  plugin/scripts/ledger-lib.sh     # shared: config reader, repo name (incl. bare repos), project folder, card file name
  plugin/scripts/confirm-public.py # approval hook: GitHub writes, pushes (per push_approval), rule-file edits
  plugin/skills/status-card/       # worker side: scripts/card-path.sh [WORKTREE_DIR]
  plugin/skills/hub/               # scripts/hub-scan.sh + scripts/sessions.py (live sessions from `claude agents --json`)
  plugin/skills/adopt/             # scripts/adopt-scan.sh (read-only facts for messy repos)
  plugin/skills/project-rules/     # scripts/project-rules.sh (links ~/.claude/tower/rules/<Project>.md via CLAUDE.local.md)
  plugin/skills/babysit-pr/        # vendored tstack @1085050 (MIT) + SAFETY.md, patched
  plugin/skills/leaving-pr-comments/
  setup.sh                         # per machine: tower.conf, rules dir, global git ignore, CLAUDE.md block, plugin install
  CLAUDE.global.md                 # the block
  README.md                        # user guide
```

Card format: YAML frontmatter (`status`, `ticket` (quoted), `updated`, `branch`, `pr`) so Obsidian shows them as properties; `hub-scan.sh` greps `^status:` etc. Keep the keys.

`hub-scan.sh` also: prints `WIP: n of limit`; flags `STALE` running cards (file untouched >24h); shows live Claude sessions per worktree and flags several interactive sessions in one worktree (agent view's JSON is undocumented, so it degrades to a one-line "skipping"); prints the real `gh` error instead of guessing; lists all open issues on a repo you own, only assigned ones otherwise.

**Tested 2026-11-04** with fake HOMEs, under bash 5.3 and macOS /bin/bash 3.2. Plugin: `claude plugin validate --strict` passes for marketplace and plugin; `setup.sh` ×2 in a fake HOME adds the marketplace and installs/enables `tower@tower` via the real CLI, keeps other settings (Orca hook) intact, adds one CLAUDE.md block, is idempotent. Scripts run from the plugin: card naming incl. `/` branches and detached HEAD; hub-scan (stale, WIP, orphans, live sessions with longest-prefix match, gh errors); adopt-scan on a messy repo (dirty main, merged worktree, parked branch) and read-only on real Poot; project-rules (link, ignore check, missing rules dir). Hook: 34 public-action cases × 3 push modes + 8 rule-file cases + passthrough. **Not yet tested:** install on the real ~/.claude; the prompt inside a real session (incl. auto mode); install from GitHub (repo not pushed yet); the work laptop.

## Next steps (in order)

1. Push: create private `rishi-desai/tower` and push; push the dotfiles branch `claude-md-per-profile` (only the per-profile CLAUDE.md change). Then on this Mac: `setup.sh --dry-run`, then `setup.sh`.
2. ~~Check git ≥ 2.31 and `gh auth status`~~ Done 2026-11-04 on the personal Mac (git 2.56, gh logged in as rishi-desai). install.sh checks git itself.
3. On Poot: run `/tower:adopt` from main (scan 2026-11-04: rolly-polly has 2 sessions + uncommitted `ui/` work and is 20 behind; copilot 18 ahead/20 behind; six local branches with commits not on main and no PR). Fix whatever breaks and add it to the skills' Gotchas.
4. ~~Install Matt Pocock's skills~~ Already installed (plugin 1.3.1, all skills named above present). Run `/setup-matt-pocock-skills` per repo only if not done; Poot already uses its triage and wayfinder labels. On the work repo, check with him first, since it may write config files.
5. Later, only if wanted: a `stack-explainer` skill (one HTML page showing how a PR stack fits together), a Poot domain glossary/skill (trading terms, risk rules), and a visual board built from the ledger files.

## Open questions for Rishi

Answered 2026-11-04: WIP 3 personal / 4 work (per-machine config); Poot uses GitHub issues; babysit-pr bundled now, installed with everything else; push approval `every` for now.

Done 2026-11-04: his own `~/.claude/CLAUDE.md` cleaned up (on the personal Mac, before install). The "Code rules, shortcuts, and hooks" section (which told agents to write per-project files into repos without asking) was removed in favour of decision 4b; "Who I am" updated to FDE; Orca added to the stack. Original kept at `~/.claude/CLAUDE.md.pre-tower.bak`.

## How he wants to be talked to

- Write out acronyms on first use.
- Explain code step by step, including what each variable, file, and command does.
- Detailed enough that he doesn't need follow-ups, but not a wall of text: structure, short sections, a decision clearly marked.

## Reference material used

- Thariq (Claude Code team) articles: dynamic workflows (fan-out-and-synthesize, adversarial verification), how Anthropic uses skills (gotchas sections, scripts in skills, description = trigger), prompt caching, seeing like an agent, context engineering for Claude 5 (fewer rules, progressive disclosure), session management (context rot around 300–400k tokens, `/clear` vs `/compact`, subagents), spending effort (low/medium to build, high to verify), HTML over markdown for human-facing reports.
- Repos: github.com/mattpocock/skills, github.com/multica-ai/andrej-karpathy-skills, github.com/obra/superpowers, github.com/t3dotgg/tstack.
