"""scan.py: `tower scan`, the hub's view of one repo, so the hub never reads transcripts.

Prints, from anywhere inside the repo:
  1. WORKTREES     every worktree with its effective state, the WIP count, unshipped work
  2. LIVE SESSIONS the Claude sessions open in each worktree right now (from agent view)
  3. ORPHAN CARDS  cards whose worktree no longer exists (finished or deleted work)
  4. GROUPS        every worktree in the hub's report order (Needs you, Blocked, Running, Waiting on CI or review,
                   Done, Housekeeping), each with the reason for its state, so the hub never regroups
  5. GITHUB        your open PRs, and open issues (all of them on your own repo, assigned to you otherwise)
  6. BACKLOG       parked ideas waiting for a slot (only used when there's no GitHub)

Sections 1, 3 and 4 are `tower state`'s report (state.py, the one place a worktree's status is decided), and section 2
draws the same `claude agents --json` snapshot, so the hub, the pane (which parses section 1) and the WIP count always
agree. Each GitHub call that fails prints its real error and the scan goes on. The pane runs this every 30 seconds with
github=False (`--no-github`).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tower import ledger, sessions, state
from tower.github import try_gh
from tower.gitrepo import repo_name

PR_TEMPLATE = (
    "{{range .}}- #{{.number}} {{.title}} [{{.headRefName}}] review:"
    '{{if .reviewDecision}}{{.reviewDecision}}{{else}}none{{end}}{{if .isDraft}} DRAFT{{end}}{{"\\n"}}{{end}}'
)
ISSUE_TEMPLATE = '{{range .}}- #{{.number}} {{.title}}{{range .labels}} [{{.name}}]{{end}}{{"\\n"}}{{end}}'


def hub_scan(repo_dir: Path, github: bool = True) -> str:
    """The whole scan as text. Raises NotARepo outside a git repo and LedgerError when the ledger can't be found:
    a hub without cards would report "nothing needs you"."""
    conf = ledger.read_conf(ledger.conf_path())
    folder = ledger.project_dir(repo_name(repo_dir), conf) / "Ledger"
    folder.mkdir(parents=True, exist_ok=True)
    pr_approval = ledger.conf_get(conf, "pr_approval", "every")
    if pr_approval not in ("every", "none"):
        pr_approval = "every"  # anything else is the safe default, as the approval hook reads it
    report = state.gather(repo_dir)

    lines = [f"REPO: {report.repo}", f"LEDGER: {folder}", ""]
    lines += [*state.worktrees_section(report), f"PR approval: {pr_approval}", ""]
    lines.append("== LIVE SESSIONS (agent view) ==")
    if report.agents is None:
        lines.append("(no live sessions: tower couldn't read agent view; see the NOTE lines under WORKTREES)")
    else:
        lines += sessions.render(report.agents, [(f.label, f.path) for f, _ in report.worktrees])
    lines += ["", *state.orphans_section(report), "", *state.groups_section(report), ""]
    lines += ["== GITHUB ==", *(github_section(repo_dir) if github else ["(skipped: --no-github)"]), ""]
    lines.append("== BACKLOG (parked ideas) ==")
    return "\n".join(lines) + "\n" + backlog(folder / "_backlog.md")


def github_section(repo_dir: Path) -> list[str]:
    """Your open PRs, and the open issues: all of them on your own repo (tickets from /to-tickets are usually
    unassigned), only those assigned to you on someone else's (work)."""
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
    issues = ("issue", "list", "--state", "open", "--limit", "30", "--json", "number,title,labels")
    if me and owner == me:
        lines += gh_list(repo_dir, "open issues (all — your repo)", *issues, "--template", ISSUE_TEMPLATE)
    else:
        lines += gh_list(
            repo_dir, "open issues assigned to you", *issues, "--assignee", "@me", "--template", ISSUE_TEMPLATE
        )
    return lines


def gh_list(repo_dir: Path, title: str, *args: str) -> list[str]:
    """One titled list from gh: its lines, "(none)", or the real error if gh failed."""
    out, error = try_gh(repo_dir, *args)
    if out is None:
        return [f"-- {title} --", f"(gh failed: {error})"]
    return [f"-- {title} --", *(out.rstrip("\n").split("\n") if out.rstrip("\n") else ["(none)"])]


def backlog(path: Path) -> str:
    """The parked ideas file as it is, or "(empty)" when it's missing or empty."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        text = ""
    return text or "(empty)\n"
