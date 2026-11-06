# tower A+ plan

Written 2026-11-06. Goal: A+ in every category about how tower is built (engineering quality, safety, building
complex systems, measurable productivity, FDE (Forward Deployed Engineer) work, cognitive load), close every gap
from the two reviews, and fold in the open issues (#4–#16).

**Decided:** keep tower's workflow (hub, cards, one ticket = one worktree, approvals). Move its logic into a small,
tested Python core that owns the state; Claude writes only the prose. Stacks use git-spice. Workers auto-merge into
any branch; merging into `main` needs you and green CI. Gates become policy profiles (`work`, `personal`, `sandbox`).

---

## Filed issues

Tracking issue: #17. T01 #18 (transfer to `rishi-desai/ci`) · T02 #19 · T03 #20 · T04 #21 · T05 #22 · T06 #24 ·
T07 #27 · T08 #28 · T09 #26 · T10 #31 · T11 #29 · T12a #30 · T12 #32 · T13 #33 · T14 #38 · T15 #25 · T16 #39 ·
T17 #34 · T18 #37 · T19 #35 · T20 #36 · T21 #23 · T22 #40.

## How to run this plan in Orca

1. **T00 first** (in the hub): file every ticket below as a GitHub issue with `ready-for-agent` and its `Blocked by`
   links. Claude can file them for you on your OK, or you can paste this file into the hub and type `/to-tickets`.
   Use T-numbers in issue titles (`T06: effective state engine`) so this file and the tracker stay matched.
2. Then in the hub, every time: **"what's next?"**, then **"start #N"**. The hub chooses the effort, writes the
   kickoff, and runs `orca worktree create …`. The effort below is a suggestion; the hub's table wins if they differ.
3. Tickets marked **Discuss first** start in the hub with `discuss: <topic>` (the `grilling` skill) and end in
   `/to-spec`. The spec replaces the ticket body below. Then "start #N".
4. Keep WIP (work in progress) at 3. The **Lanes** section lists what can run in parallel.
5. Before closing each worker: `/retro` in that session (you type it).

Kickoff template, for starting a worker in Orca by hand without the hub (the hub writes this for you):

```
/implement #<N> — <title>. Use the status-card skill whenever you stop. Record domain decisions from the ticket with the domain-modeling skill in this branch. Before the PR, run mattpocock-skills:code-review by that full name, then the built-in /code-review <high|xhigh>; once it passes, open the PR and squash-merge it yourself with gh pr merge --squash --delete-branch after its checks pass.
```

For a bug ticket, start with `/diagnosing-bugs #<N>` instead of `/implement`. For a spike, use `/prototype <question> — #<N>`.

### When to use Matt Pocock's skills

| Moment | Skill | Where | You type it? |
|---|---|---|---|
| An idea or ticket is fuzzy | `grilling` (the hub picks it on "discuss: …") | Hub, main checkout | No |
| A huge, foggy effort (none in this plan) | `/wayfinder` | **A worktree**, never main (#9) | Yes |
| The discussion is settled | `/to-spec` (writes the issue, labels it `ready-for-agent`) | Hub | Yes |
| The spec is too big for one session | `/to-tickets` (sub-issues with `Blocked by`) | Hub | Yes |
| Build a ticket | `/implement #N` (runs `tdd` and Matt's `code-review` itself) | Worker | Kickoff does it |
| Fix a bug ticket | `/diagnosing-bugs #N` | Worker | Kickoff does it |
| A question needs running code to answer | `/prototype` (spike) | Spike worktree | Kickoff does it |
| Record a design decision or term | `domain-modeling` (ADR or GLOSSARY.md, in the branch) | Worker | No |
| Check docs or facts | `research` | Worker | No |
| Move a discussion, or stop for the day | `/handoff` | Any session | Yes |
| New issues come in labelled `needs-triage` | `/triage` | Hub | Yes |
| A worker is finished | `/retro` | That worker | Yes |

---

## Lanes (what can run in parallel)

```
Lane A (safety)     T01 ─┐        T04 ──────────── T09 ─── T10
                    T02 ─┼─ T03                      │
Lane B (core)            └─ T05 ─ T06 ─ T07 ─ T08 ───┤
Lane C (measure)              T05 ─ T15              │
Lane D (systems)                    T06 ─ T12a ─ T12 ─ T13 ─ T14
Lane E (UI)                         T06 ─ T11 ─────────────────── T16 ─ T18
Lane F (fillers)    T21 (anytime)   T20 (after T09)   T17, T19 (after T07)   T22 (last)
```

Suggested order with WIP 3: **T01 + T02 + T21** → **T03 (you) + T04 + T05** → **T06 + T15 + T09** → **T07 + T11 + T12a**
→ **T08 + T12 + T10** → **T13 + T17 + T19** → **T14 + T20** → **T16** → **T18** → **T22**.

---

## Phase 0: CI and protection

### T00: File this plan as issues
- **Where:** hub. **Effort:** none (no worker).
- Create one issue per ticket below with `ready-for-agent` and `Blocked by` links. Add a comment on each existing
  issue a ticket absorbs (`Superseded by #N`), but leave closing to the PR that does the work (`Closes #N`).
- Run `/triage` on #12–#16 (labelled `needs-triage`). They're absorbed by T11, T16, T18, T19 and T22.

### T01: CI repo hygiene and workflow security linting (repo: `rishi-desai/ci`)
- **Effort:** medium. **Start:** `/implement`. **Blocked by:** nothing.
- Delete the committed `pi/__pycache__/` and `pi/tests/__pycache__/`; add `.gitignore` (`__pycache__/`, `*.pyc`, `.venv/`).
- Fix the README line saying the repo is private (it's public). Decide: keep it public (fine, nothing secret) or make it private.
- Add **zizmor** (security linter for GitHub Actions workflows) to `mise.toml` tools and the `ci` task, next to `actionlint`.
- Add an optional **gitleaks** (secret scanner) step to the README's "Add a repo" instructions as a recommended line in each repo's `ci` task.
- Document required repo settings for callers: a ruleset on `main` (see T03) and Dependabot for the repo's own packages.
- **Done when:** `mise run ci` passes locally and in GitHub; zizmor reports no findings (or each one is fixed or
  justified in a comment); `git ls-files | grep pycache` prints nothing. Move the `v1` tag after merge.

### T02: tower adopts shared CI; commit the approval hook's tests
- **Effort:** high. **Start:** `/implement`. **Blocked by:** nothing.
- Add `mise.toml`: tools (python, uv, ruff, mypy, shellcheck, gitleaks; node if the pane's tests need it) and a
  `ci` task: `ruff check`, `ruff format --check`, `mypy`, `pytest`, `shellcheck` on every `.sh`, gitleaks, the pane's
  type check and tests (find out how `claude-code/testing` tests run headless; if they can't run in CI, document it
  and run them in `mise run ci` locally only), and `claude plugin validate --strict` if the CLI installs in CI
  (otherwise a JSON schema check of the three manifests).
- Add `.github/workflows/ci.yml` calling `rishi-desai/ci/.github/workflows/ci.yml@v1`, and `.github/dependabot.yml`
  (github-actions is not needed: the shared workflow pins actions; add npm/uv only if tower gains a lockfile).
- Add `pyproject.toml` (uv-managed; ruff + mypy strict + pytest config) and `tests/`.
- Port the hook's test cases from HANDOFF.md ("34 public-action cases × 3 push modes + 8 rule-file cases +
  passthrough") into `tests/test_confirm_public.py`, parametrised. Add the known bypasses as `xfail(strict=True)`
  tests so T04 must turn them green:
  `git -C "/tmp/my repo" push`, `gh pr -R owner/repo comment 5`, `gh pr --repo owner/repo merge 3`,
  `gh api -XPOST repos/a/b/issues`, `echo x > ~/.claude/tower.conf`.
- **Done when:** CI is green on the PR and the `CI` check appears on it; `mise run ci` locally gives the same result.

### T03: Protect `main` on tower, ci and Poot (you, in GitHub settings)
- **Effort:** none (manual, about 10 minutes). **Blocked by:** T01 and T02 (so the `CI` check exists to require).
- Settings → Rules → Rulesets → New branch ruleset, target `main`:
  - Require a pull request before merging.
  - Require status checks to pass: `CI / CI`, with **Require branches to be up to date before merging** on.
  - Block force pushes; restrict deletions.
- **Gotcha, your own approval:** GitHub never lets a PR's author approve their own PR. Workers open PRs as *you*
  (your `gh` login), so "require 1 approving review" would block every PR forever. Pick one:
  - **A (recommended now):** no required review in the ruleset. Your approval is tower's `merge_to_main = "ask"`
    gate (T09), plus the required green CI. You merge to `main` from the approval prompt or from GitHub.
  - **B (later, strongest):** give workers their own identity (a GitHub App or a second "bot" account) so their PRs
    aren't yours, then require 1 approving review from you. Add as a follow-up issue if you want it.
- Enable Dependabot alerts and security updates. On public repos also enable secret scanning + push protection and
  CodeQL default setup (free on public repos only).
- **Done when:** a test PR with a failing check can't be merged, and a direct `git push origin main` is refused.

---

## Phase 1: Trust (status and approvals you can rely on)

### T04: Harden the approval hook's command parsing
- **Effort:** xhigh (security). **Start:** `/diagnosing-bugs`. **Blocked by:** T02.
- Replace the regex splitting in `plugin/scripts/confirm-public.py` with `shlex`-based tokenising per simple command
  (split on `&&`, `||`, `;`, `|`, newlines while respecting quotes), then match on tokens:
  - `git` with any global options before the subcommand (`-C <path>` quoted or not, `-c k=v`, `--git-dir=…`,
    `--work-tree=…`), then `push`.
  - `gh` with `-R/--repo` anywhere (before or after the subcommand), and `api` with `-X`/`--method` attached
    (`-XPOST`, `--method=POST`) or separate.
  - Commands wrapped in `bash -c '…'`, `sh -c`, `env VAR=x …`, `command …`, `xargs …`: inspect the inner command.
  - Writes to `~/.claude/tower.conf` (and later the policy file) count as rule-file edits.
- Anything it can't tokenise (unbalanced quotes) asks. Keep the docstring's "Limits" honest.
- **Done when:** every `xfail` from T02 passes with `strict` removed, no old case regresses, and new cases cover
  each form above in all three push modes.

### T05: Core skeleton and the event log
- **Effort:** high. **Start:** `/implement`. **Blocked by:** T02.
- Create the Python package `src/tower/` with a `tower` CLI (`tower --version`, `tower events tail`). No third-party
  runtime dependencies (standard library only), Python ≥ 3.11, fully typed.
- Event log: append-only JSON Lines at `~/.claude/tower/events.jsonl` (one file for all repos, so the multi-repo view
  is free). Each event: `ts`, `type`, `session_id`, `repo`, `worktree`, `branch`, plus a type-specific payload.
  Writes are atomic appends with a file lock; rotate monthly.
- The existing hooks (Stop, Notification, UserPromptSubmit, PostToolUse on AskUserQuestion) also append events.
  Add SessionStart/SessionEnd hooks if Claude Code provides them (check the hooks docs with `research`). Hooks must
  never fail the session: catch everything, log the error to `~/.claude/tower/errors.log`.
- Purely additive: nothing reads the log yet; today's behaviour is unchanged.
- Write **ADR 0001: deterministic core, Claude writes prose** (`docs/adr/0001-…`) and add GLOSSARY.md terms
  (card, ledger, hub, worker, effective state, execution slot) with `domain-modeling`.
- **Done when:** unit tests cover the writer (concurrent appends from several processes don't interleave lines);
  using a worker for a few minutes produces the expected event sequence; CI green.

### T06: Effective state engine (one truth for hub, pane and WIP)
- **Effort:** high. **Start:** `/implement`. **Blocked by:** T05. **Closes:** #11.
- `tower state [--repo PATH] --json` computes, per worktree, one **effective status** from: the card, live sessions
  (`claude agents --json`, degrading cleanly if missing), the event log, git, and (when asked) the PR:
  `needs-you` · `running` · `waiting-ci` (finished, waiting on CI/review/merge) · `blocked` · `done` ·
  `ready-to-clean` · `no-card` · `main`. Each row includes a `reason` string ("card says needs-you but the session is
  busy: you already answered").
- Rules, each with a test:
  - A live session waiting on you is `needs-you`, whatever the card says.
  - A busy session beats a `needs-you` card → `running` (today hub-scan.sh leaves it under Needs you; the pane
    differs).
  - **Execution slots** (what WIP counts) = worktrees with a busy or waiting session, or a `running`/`needs-you`
    card. A busy worker with no card counts (today it gives `WIP: 0 of 3`). `waiting-ci` doesn't count.
  - Report "unshipped work" (worktrees with commits not on main) separately from slots.
- `hub-scan.sh` sections 1 and 4 call `tower state` instead of their own logic; the hub skill reports from it.
- **Done when:** tests reproduce both disagreements from the review (3 busy cardless workers → 3 slots; busy +
  stale needs-you → running) and pass; the hub and the pane show the same status for the same fixture.

### T07: Port the rest of the scans to Python
- **Effort:** high. **Start:** `/implement`. **Blocked by:** T06. **Closes:** #4.
- Move `hub-scan.sh` (orphans, GitHub section, backlog), `adopt-scan.sh`, `card-path.sh`, `project-rules.sh` and
  `ledger-lib.sh` into the core as `tower scan`, `tower adopt-scan`, `tower card-path`, `tower rules`. Keep each
  `.sh` as a one-line wrapper for one release so skills keep working, then delete them in T19.
- adopt-scan: skip an untracked path that is (or only contains) a git worktree root, and print the
  `.git/info/exclude` note instead (#4's check).
- **Done when:** fixture repos (dirty main, nested `worktrees/`, merged worktree, detached HEAD, `/` in branch names)
  give the same facts as the bash version, except the #4 fix; bash 3.2 workarounds are gone.

### T08: Cards become a rendered view
- **Effort:** high. **Start:** `/implement`. **Blocked by:** T06.
- `tower card write` takes the agent's narrative sections (Done, Why it matters, Needs you, Verify, Words used) on
  stdin; the core fills the frontmatter (`status`, `ticket`, `branch`, `pr`, `updated`, `base`) from facts and
  renders the file in the vault. The agent can't set a status that contradicts the facts; it can request
  `needs-you` or `blocked`, with a reason.
- Update the `status-card` skill to use it. `card-resume.py` becomes an event plus a re-render.
- Obsidian still shows the same properties, so nothing changes for you.
- **Done when:** a card can't say `done` without a PR; the ticket-quoting bug class is gone (the core writes YAML,
  not the model); the status-card skill is shorter.

---

## Phase 2: Policy profiles (every gate is a setting)

### T09: Policy profiles: work, personal, sandbox
- **Effort:** xhigh (security). **Start:** `/implement`. **Blocked by:** T04, T05. **Closes:** #7.
- `~/.claude/tower/policy.toml` with `default_profile`, `[profiles.*]` and `[repos]` overrides (glob patterns on
  `owner/repo`). Keys: `push` (every|force|none), `merge_to_main` (ask|auto), `merge_to_branch` (ask|auto),
  `comments` (ask|auto), `issue_writes` (ask|auto), `pr_writes` (ask|auto), `cleanup` (ask|auto), `wip_limit`.
- Built-in profiles: `work` (everything asks), `personal` (push asks on force only, merges into branches auto, into
  `main` ask, issue writes auto, comments ask), `sandbox` (nothing asks).
- Precedence: repo override → `default_profile` → built-in strictest. Unknown or invalid values → strictest, and
  `tower doctor` (T20) reports them.
- `tower policy` shows the active profile for the current repo and why; `tower policy use <profile> [--repo]`
  switches it (you run it; it's a terminal command, not something an agent does).
- **Safety floor in every profile, sandbox included:** any agent edit of the policy file, `tower.conf`, rule files or
  `CLAUDE.local.md` asks. An agent can't loosen its own leash.
- The hook reads policy through the core. Migrate `push_approval` / `pr_approval` from `tower.conf` on first run.
  `setup.sh` asks for the machine's default profile. The pane's status line shows the profile (`tower · work · 2/4`).
- **Done when:** a test matrix covers every key × profile × override, plus the floor; your work laptop runs `work`
  and this Mac `personal`; a sandbox VM asks nothing except policy/rule edits.

---

## Phase 3: Worker lifecycle

### T10: Verified cleanup of finished workers
- **Effort:** high (it deletes things). **Start:** `/implement`. **Blocked by:** T06, T09. **Closes:** #5.
- `tower clean [<worktree>|--all]`, offered by the hub for `ready-to-clean` rows. Every check must pass, or it
  reports which failed and touches nothing: PR merged; worktree HEAD equals the PR's head commit; `git status
  --porcelain` empty; no stash entries for the branch; no busy or waiting session there.
- Then: close the worktree's terminals, `orca worktree rm` (fallback `git worktree remove` + `git branch -D`),
  delete the remote branch if it remains, archive the card (to `Ledger/_archive/`, not delete, so metrics keep
  history), `git pull --ff-only` on main. `cleanup = "ask"` lists what it will remove and asks once; `"auto"` just does it.
- **Done when:** tests cover each failed check leaving everything in place; a real merged worker is cleaned in one step.

### T11: Pane v2: one source of state, jump-to-answer, all issues
- **Effort:** high. **Start:** `/implement`. **Blocked by:** T06. **Closes:** #12, #6.
- The pane reads `tower state --json` instead of parsing hub-scan's text, so it can't disagree with the hub.
- #12: remove the in-pane question relay (the worker side writing `~/.claude/tower/questions`, the polling, the
  QUESTION badge, the buttons). Waiting sessions show WAITING with "input needed"; clicking (or 1–9) jumps to the
  exact waiting terminal in Orca; one key or click returns to the hub.
- #6: list every open issue (capped at 10, "+ N more"): `ready-for-agent` gets start, the rest get discuss.
- **Done when:** pane tests use JSON fixtures that are shared with the core's tests.

---

## Phase 4: Building complex systems

### T12a: Spike: git-spice with Orca worktrees
- **Effort:** medium. **Start:** `/prototype Does git-spice work cleanly across Orca worktrees? — #<N>`. **Blocked by:** T06.
- Answer in a throwaway repo: does git-spice's branch tracking (stored in the shared `.git`) behave when each stack
  branch lives in its own worktree? Can `gs` restack a branch checked out in another worktree (git refuses to move
  a checked-out branch)? Does `gs stack submit` set each PR's base correctly? Does auto-merging a lower PR retarget
  the next one? Note: on macOS the `gs` binary name collides with Ghostscript; check the install docs.
- Output: a short write-up in the issue and a recommendation: one worktree per stack (branches stacked inside it)
  or one worktree per branch.
- **Done when:** the questions have answers with commands you can rerun.

### T12: Stacked PRs with git-spice
- **Effort:** high. **Start:** `/implement`. **Blocked by:** T12a, T08.
- A ticket can be marked as part of a stack (from `/to-tickets` `Blocked by` links). The hub's kickoff tells the
  worker to create its branch with git-spice on top of its parent, and to submit with `gs`.
- The core reads git-spice's stack (parent of each branch) into state and cards (`base:`). The hub and pane show a
  stack tree in merge order, and flag "needs restack" when a parent moved (rebased, merged or force-pushed).
- Merging follows policy: PRs into stack branches merge under `merge_to_branch`; the bottom PR into `main` waits
  under `merge_to_main`. After a lower PR merges, the next one is restacked and its CI re-run.
- Update `README` and the hub skill's kickoff section; add `git-spice` to `setup.sh` checks.
- **Done when:** a 3-PR test stack goes from creation to "bottom PR waiting for you" with the hub showing the tree
  correctly at each step.

### T13: Conflict detection: files and runtime
- **Effort:** high. **Start:** `/implement`. **Blocked by:** T07.
- **Files:** `tower state` computes each worktree's changed files from git (`git diff --name-only
  <merge-base>...HEAD` plus uncommitted), never from cards, and flags pairs that overlap. The hub lists them under
  Housekeeping with the shared files.
- **Runtime:** worktrees share ports, databases and env vars (README Gotchas). Add per-project config outside the
  repo, `~/.claude/tower/projects/<Project>.toml`, declaring resources (e.g. `port_base = 3000`, `db_name =
  "poot_{slot}"`). Each worktree gets a stable slot number; the kickoff and `tower env` export `TOWER_SLOT`,
  `PORT`, etc. so two workers never fight over port 3000. Poot's main-only `data/` folder: document a `seed`
  command in the project config.
- **Done when:** two fixture worktrees editing the same file are flagged; two Poot workers run their dev servers at
  once without clashing.

### T14: Integration view: "how the pieces fit"
- **Effort:** high. **Discuss first** (in the hub: `discuss: integration view`, then `/to-spec`). **Blocked by:** T12, T13.
- Starting points for the discussion:
  - A **capability** is a parent issue (from `/to-tickets`) with its child tickets.
  - Specs gain two sections the hub fills in while discussing: **Contracts** (the interfaces between the tickets,
    e.g. the alert event format between alert ingestion and the risk engine, with the file paths that define them)
    and **Integration check** (one command that proves the pieces work together, run after the last child merges).
  - `tower map <capability>` shows: the children, status, stack order, which contracts each touches, and whether
    a contract file changed in more than one open worktree (the "both done, but they disagree" case).
  - A weekly digest from the event log and merged PRs: what changed in the system and which contracts moved.
- **Done when:** the spec's own acceptance checks pass; on Poot, a two-ticket capability shows its contract and its
  integration check runs.

---

## Phase 5: Measurement

### T15: Metrics from the event log
- **Effort:** medium. **Start:** `/implement`. **Blocked by:** T05. Start early: the log needs time to collect a baseline.
- `tower stats [--since 14d] [--repo]` computes, per accepted change (merged PR):
  - your active minutes (from prompt/answer events and approvals),
  - start → merged, and start → verified working capability (T14's integration check, once it exists),
  - time waiting on you, and time to resume after an interruption (gap between a needs-you event and your answer),
  - follow-up questions per report (your prompts after a worker's report before it continues),
  - integration rework (commits or reopened issues after `done`),
  - model cost per change (Claude Code can export cost and token metrics through OpenTelemetry; check the docs
    with `research` and use them if available, otherwise leave the column out).
- Write today's numbers to `docs/metrics/baseline.md` after two weeks, before the dashboard (T16).
- **Done when:** tests over a fixture event log give known numbers; `tower stats` runs on your real log.

---

## Phase 6: Interfaces

### T16: Dashboard
- **Effort:** high. **Discuss first** (`discuss: #16 dashboard`; `/prototype` the embedded terminal if needed;
  then `/to-spec` and `/to-tickets`). **Blocked by:** T11, T12, T14, T15, T17. **Closes:** #16.
- Starting point: `tower serve` runs a local-only web page (bound to `127.0.0.1`) reading the same state JSON: all
  repos, needs you, stacks, overlaps, the integration map, metrics. Actions go to the hub as prompts, as the pane
  does, so approvals still apply. The embedded hub terminal from #16 is a later slice after a spike. The pane stays
  as the in-flow view.

### T17: Multi-repo view
- **Effort:** medium. **Start:** `/implement`. **Blocked by:** T07.
- `tower state --all` across every repo with a Ledger (or that appears in the event log); the hub answers "what
  needs me across everything?" with one group per repo and one next action overall. Work and personal stay
  separate because each machine has its own vault and log.

### T18: One plugin instead of two
- **Effort:** medium. **Start:** `/implement` (it checks the plugin and mod docs with `research` first).
  **Blocked by:** T11. **Closes:** #13.

---

## Phase 7: Simpler to use

### T19: Merge adopt into the hub; delete the bash wrappers
- **Effort:** medium. **Start:** `/implement`. **Blocked by:** T07. **Closes:** #14.

### T20: `tower doctor`
- **Effort:** medium. **Start:** `/implement`. **Blocked by:** T09.
- Checks each dependency and says exactly how to fix it: Orca reachable, `gh` logged in to the right account,
  vault path exists, plugin enabled, git-spice installed, mise present, policy valid, `claude agents --json` parses,
  hooks firing (an event in the last hour). `setup.sh` runs it at the end; the hub runs it when a scan fails.

### T21: Wayfinder fixes
- **Effort:** medium. **Start:** `/diagnosing-bugs`. **Blocked by:** nothing (filler). **Closes:** #8, #9.

### T22: README rewrite, cheat sheet and versioning
- **Effort:** medium. **Start:** `/implement`. **Blocked by:** everything else. **Closes:** #15.
- README: what tower is, install, the daily loop; reference material moves to `docs/`. A one-page
  `docs/cheatsheet.md` (things to say to the hub, the CLI commands, the profiles). Add release-please so versions,
  tags and `CHANGELOG.md` come from commit messages. Update HANDOFF.md's decision list with the decisions above.

---

## Gap → ticket map

| Gap | Ticket |
|---|---|
| Status disagrees between hub and pane; WIP undercount | T06, T11 |
| Approval hook bypasses; agent can loosen its own config | T04, T09 |
| No committed hook tests, no CI | T02 |
| Main not protected; can't self-approve | T03 |
| Gates not adjustable per context | T09 |
| No history or metrics | T05, T15 |
| No stacked PRs | T12a, T12 |
| No file or runtime conflict detection | T13 |
| "How the pieces fit" / integration contracts | T14 |
| One repo only | T17 |
| Cognitive load: too many entry points, long README, fragile dependencies | T18, T19, T20, T22 |
| Worker lifecycle and cleanup | T10, T11 |
| Undocumented `claude agents --json` | T06 (degrades cleanly), T20 (detects breakage) |
