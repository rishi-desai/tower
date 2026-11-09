"""scan.py: `tower scan`, the hub's view of one repo, so the hub never reads transcripts.

Prints, from anywhere inside the repo:
  1. WORKTREES     every worktree with its effective state, the WIP count, unshipped work, the repo's approval
                   policy (the Policy: line, see policy.py), and whether the repo still needs adopting (the ADOPT:
                   line, which switches the hub into its adopt flow)
  2. LIVE SESSIONS the Claude sessions open in each worktree right now (from agent view)
  3. ORPHAN CARDS  cards whose worktree no longer exists (finished or deleted work)
     STACKS        the stacks git-spice tracks, as trees in merge order, with which branch merges next and which
                   need a restack (stacks.py)
  4. GROUPS        every worktree in the hub's report order (Needs you, Blocked, Running, Waiting on CI or review,
                   Done, Housekeeping), each with the reason for its state, so the hub never regroups
  5. GITHUB        your open PRs, and open issues (all of them on your own repo, assigned to you otherwise), each
                   issue in progress marked with where it runs (state.issues_in_progress)
  6. BACKLOG       parked ideas waiting for a slot (only used when there's no GitHub)

Sections 1, 3, STACKS and 4 are `tower state`'s report (state.py, the one place a worktree's status is decided), and section 2
draws the same `claude agents --json` snapshot, so the hub, the pane (which reads `tower state --json`) and the WIP count
always agree. Each GitHub call that fails prints its real error and the scan goes on. The pane runs this only for its
GitHub section (Refresh GitHub), never on its 30-second refresh.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from tower import adopt, ledger, policy, sessions, state
from tower.github import try_gh

PR_TEMPLATE = (
    "{{range .}}- #{{.number}} {{.title}} [{{.headRefName}}] review:"
    '{{if .reviewDecision}}{{.reviewDecision}}{{else}}none{{end}}{{if .isDraft}} DRAFT{{end}}{{"\\n"}}{{end}}'
)
ISSUE_LIMIT = 30  # gh issue list's --limit; the pane shows "+" when a list is this long (SCAN_ISSUE_LIMIT)
ISSUE_TEMPLATE = '{{range .}}- #{{.number}} {{.title}}{{range .labels}} [{{.name}}]{{end}}{{"\\n"}}{{end}}'
ISSUE_ROW = re.compile(r"- (#\d+) ")  # the start of a row ISSUE_TEMPLATE prints: its ticket


def hub_scan(repo_dir: Path, github: bool = True) -> str:
    """The whole scan as text. Raises NotARepo outside a git repo and LedgerError when the ledger can't be found:
    a hub without cards would report "nothing needs you"."""
    folder = ledger.open_ledger(repo_dir)
    report = state.gather(repo_dir)

    lines = [f"REPO: {report.repo}", f"LEDGER: {folder}", ""]
    lines += state.worktrees_section(report)
    lines += [policy.scan_line(report.policy)] if report.policy is not None else []
    lines += [adopt_line(report), ""]
    lines.append("== LIVE SESSIONS (agent view) ==")
    if report.agents is None:
        lines.append("(no live sessions: tower couldn't read agent view; see the NOTE lines under WORKTREES)")
    else:
        lines += sessions.render(report.agents, [(f.label, f.path) for f, _ in report.worktrees])
    lines += ["", *state.orphans_section(report), "", *state.stacks_section(report)]
    lines += ["", *state.groups_section(report), ""]
    github_lines = github_section(repo_dir, state.issues_in_progress(report)) if github else ["(skipped: --no-github)"]
    lines += ["== GITHUB ==", *github_lines, ""]
    lines.append("== BACKLOG (parked ideas) ==")
    return "\n".join(lines) + "\n" + backlog(folder / "_backlog.md")


def adopt_line(report: state.Report) -> str:
    """ADOPT: yes, with why, when the repo hasn't been brought under tower yet: most worktrees have no card (at least
    two: one is Housekeeping's "no card"), or the main checkout has uncommitted work or more than one interactive session
    (the hub is one). Else ADOPT: no. What tower couldn't read (a card, git status, sessions when agent view is
    unreadable) is never a reason: the hub would plan from facts it doesn't have, and the NOTE lines already say so."""
    reasons: list[str] = []
    worktrees = [facts for facts, _ in report.worktrees if not facts.is_main]
    no_card = sum(1 for facts in worktrees if facts.card is None and not facts.card_unknown)
    if no_card >= 2 and no_card * 2 > len(worktrees):
        reasons.append(f"{no_card} of {len(worktrees)} worktrees have no card")
    roots = {os.path.realpath(facts.path) for facts, _ in report.worktrees}
    # Same count as `tower adopt-scan`, so a folder that only holds worktrees isn't work on main (#4).
    changes, _ = adopt.uncommitted(Path(report.main_checkout), roots)
    if changes:
        reasons.append(f"the main checkout has {state.plural(len(changes), 'uncommitted change')}")
    main = next(facts for facts, _ in report.worktrees if facts.is_main)
    interactive = sum(1 for s in main.sessions if s.kind == "interactive")
    if interactive > 1:
        reasons.append(f"{interactive} interactive sessions share the main checkout (one is the hub)")
    return f"ADOPT: yes — {'; '.join(reasons)}" if reasons else "ADOPT: no"


def github_section(repo_dir: Path, in_progress: dict[str, str]) -> list[str]:
    """Your open PRs, and the open issues: all of them on your own repo (tickets from /to-tickets are usually
    unassigned), only those assigned to you on someone else's (work). An issue in progress (in_progress, from
    state.issues_in_progress) is marked with where it runs."""
    if shutil.which("gh") is None:
        return ["(gh not installed — skipping)"]
    if try_gh(repo_dir, "auth", "status")[0] is None:
        return ["(gh not logged in — run: gh auth login)"]
    found, error = try_gh(
        repo_dir, "repo", "view", "--json", "nameWithOwner,owner", "--jq", '.nameWithOwner + " " + .owner.login'
    )
    if found is None:
        return [f"(no GitHub repo for this checkout: {error})"]
    slug, _, owner = found.strip().partition(" ")
    me, error = try_gh(repo_dir, "api", "user", "--jq", ".login")
    me = (me or "").strip()
    lines = [f"repo: {slug} | you: {me or f'unknown ({error})'}"]
    lines += gh_list(
        repo_dir,
        "my open PRs",
        "pr", "list", "--author", "@me", "--state", "open",
        "--json", "number,title,headRefName,reviewDecision,isDraft", "--template", PR_TEMPLATE,
    )  # fmt: skip
    issues = ("issue", "list", "--state", "open", "--limit", str(ISSUE_LIMIT), "--json", "number,title,labels")
    if me and owner == me:
        listed = gh_list(repo_dir, "open issues (all — your repo)", *issues, "--template", ISSUE_TEMPLATE)
    else:
        listed = gh_list(
            repo_dir, "open issues assigned to you", *issues, "--assignee", "@me", "--template", ISSUE_TEMPLATE
        )
    return lines + [marked_in_progress(line, in_progress) for line in listed]


def marked_in_progress(line: str, in_progress: dict[str, str]) -> str:
    """An issue row, with where it runs after its labels when it's in progress: "- #93 title [bug] [in progress:
    running in feat-93-x]". The mark is one more bracket at the end, so the pane still reads the title and labels; a
    "]" in a branch name is dropped from it, or it would end the bracket early."""
    row = ISSUE_ROW.match(line)
    if row is None or row[1] not in in_progress:
        return line
    return f"{line} [in progress: {in_progress[row[1]].replace(']', '')}]"


def gh_list(repo_dir: Path, title: str, *args: str) -> list[str]:
    """One titled list from gh: its lines, "(none)", or the real error if gh failed."""
    out, error = try_gh(repo_dir, *args)
    if out is None:
        return [f"-- {title} --", f"(gh failed: {error})"]
    return [f"-- {title} --", *(out.rstrip("\n").split("\n") if out.rstrip("\n") else ["(none)"])]


def backlog(path: Path) -> str:
    """The parked ideas file as it is, "(empty)" when it's missing or empty, or why it couldn't be read."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        text = ""
    except OSError as error:  # an iCloud file not downloaded, or no permission: say so, the rest of the scan stands
        return f"(couldn't read {path}: {error.strerror or type(error).__name__})\n"
    return text or "(empty)\n"
