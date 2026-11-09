"""github.py: the merged PRs `tower stats` measures, read from GitHub with the gh CLI (read-only calls only),
try_gh() for the scans' GitHub facts, the one place a branch's own PRs are listed (branch_prs), which the card, the
adopt scan, `tower state --pr` and `tower clean` all use, and the one rule for a worktree's PR (worktree_pr), which
`tower card` and `tower state --pr` both use.

A PR's tickets are the "Closes/Fixes/Resolves #N" in its body, read here rather than from GitHub's
closingIssuesReferences: GitHub links a closing keyword only for PRs into the default branch (tower's workers often
merge into an integration branch), and its references can name another repo's issue. "Closes owner/repo#N" doesn't
match, so a ticket is always this repo's. PRs from forks are left out: their head branch isn't a local branch.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from tower.gitrepo import COMMIT_ID, git
from tower.merge import Gh, gh_reason, json_at, run_gh
from tower.stats import MergedPr

GH_TIMEOUT_SECONDS = 60
PR_LIMIT = 1000  # gh's --limit; far more merges than one window holds
BRANCH_PRS_PER_PAGE = 100  # REST's most; far more than one repo reuses a branch name
CLOSING_KEYWORD = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.IGNORECASE)


class GhError(Exception):
    """A gh call failed: not installed, not logged in, not a GitHub repo, or timed out."""


@dataclass(frozen=True)
class BranchPr:
    """A branch's PR as GitHub reports it. state is OPEN, MERGED or CLOSED; head is its head commit (for a merged PR,
    as it was when it merged), "" when gh didn't give a full commit id; base, url and title are "" when gh didn't give
    them."""

    number: int
    state: str
    base: str
    head: str
    url: str
    title: str


def from_this_repo(pr: dict[str, object]) -> bool:
    """Whether a `gh pr list` record is a PR from this repo rather than a fork: a stranger's patch-1 isn't the work
    of the worktree whose branch has that name. A record without isCrossRepository (gh wasn't asked for it) counts as
    a fork, so a caller that forgot the field shows no PRs rather than strangers'. pull_from_this_repo is the same rule
    for REST's pull objects."""
    return pr.get("isCrossRepository") is False


def merged_prs(repo: str, since: datetime) -> list[MergedPr]:
    """The PRs merged in repo (a checkout's path) at or after since, each with its tickets; not those from forks."""
    output = gh(
        repo,
        "pr",
        "list",
        "--state",
        "merged",
        "--search",
        f"merged:>={since.date().isoformat()}",  # search has day precision; the exact cut is below
        "--limit",
        str(PR_LIMIT),
        "--json",
        "number,title,headRefName,mergedAt,body,isCrossRepository",
    )
    try:
        listed = json.loads(output)
    except ValueError as error:
        raise GhError(f"gh pr list in {repo}: output isn't JSON: {output[:200]!r}") from error
    prs = []
    for item in listed:
        try:
            number, title, branch = int(item["number"]), str(item["title"]), str(item["headRefName"])
            merged_at = datetime.fromisoformat(str(item["mergedAt"]).replace("Z", "+00:00"))  # 3.9 rejects "Z"
        except (KeyError, TypeError, ValueError) as error:
            raise GhError(f"gh pr list in {repo}: unexpected PR record {item!r}") from error
        if merged_at < since or not from_this_repo(item):
            continue
        closes = tuple(sorted({int(match) for match in CLOSING_KEYWORD.findall(item.get("body") or "")}))
        prs.append(MergedPr(repo, number, title, branch, merged_at, closes))
    return prs


def ticket_reopens(repo: str, tickets: set[int]) -> tuple[dict[int, list[datetime]], list[str]]:
    """When each of repo's tickets was reopened (one gh call per ticket), and a warning for each ticket whose events
    couldn't be read (deleted, transferred, or a typo in a PR body); those tickets are left out, not the repo."""
    reopens: dict[int, list[datetime]] = {}
    warnings: list[str] = []
    for ticket in sorted(tickets):
        try:
            reopens[ticket] = reopen_times(repo, ticket)
        except GhError as error:
            warnings.append(f"reopens of #{ticket} not counted: {error}")
    return reopens, warnings


def reopen_times(repo: str, issue: int) -> list[datetime]:
    """Every time issue was reopened, oldest first."""
    out = gh(
        repo,
        "api",
        "--paginate",
        f"repos/{{owner}}/{{repo}}/issues/{issue}/events",
        "--jq",
        '.[] | select(.event == "reopened") | .created_at',
    )
    try:
        return sorted(datetime.fromisoformat(line.replace("Z", "+00:00")) for line in out.splitlines() if line)
    except ValueError as error:
        raise GhError(f"gh api in {repo}: unexpected reopen time in {out[:200]!r}") from error


def gh(repo: str, *args: str) -> str:
    """Run gh with args in repo; its stdout, or GhError with gh's own message."""
    try:
        proc = subprocess.run(
            ["gh", *args], cwd=repo, check=False, capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GhError(f"gh {args[0]} in {repo}: {error}") from error
    if proc.returncode != 0:
        raise GhError(f"gh {args[0]} in {repo}: {proc.stderr.strip() or f'exit {proc.returncode}'}")
    return proc.stdout


def try_gh(cwd: Path, *args: str) -> tuple[str | None, str]:
    """Run gh with args in cwd, for reports that show a failure and go on: (stdout, "") or (None, the last line of
    gh's error, its real reason rather than a guess)."""
    try:
        proc = subprocess.run(
            ["gh", *args], cwd=cwd, check=False, capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS
        )
    except FileNotFoundError:
        return None, "gh not installed"
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"gh {args[0]} didn't run ({type(error).__name__})"
    if proc.returncode != 0:
        return None, (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1]
    return proc.stdout, ""


def branch_prs(gh: Gh, branch: str) -> tuple[list[BranchPr] | None, str]:
    """(branch's PRs from this repo, newest first; or None and why GitHub couldn't be read). The one place they are
    listed: GitHub is asked only for the PRs whose head is this repo owner's branch (`head=OWNER:BRANCH`), so however
    many strangers' forks open PRs from a branch of the same name (patch-1), none pushes this repo's own out of the
    answer, as they could out of gh pr list's first 30. Of what it lists, a PR whose head repo isn't this repo, or that
    doesn't say (a deleted fork), is left out: unsure means fork. Only the BRANCH_PRS_PER_PAGE newest are read.

    REST's pull objects become BranchPr: state is OPEN for "open", else MERGED when merged_at is set, else CLOSED; head
    is head.sha, base is base.ref, url is html_url."""
    # Percent-encoded, the colon too: gh fills :owner, :repo and :branch in an endpoint as well as {owner}, so
    # head={owner}:repo-x would reach GitHub as head=acmewidget-x.
    head = "{owner}" + quote(f":{branch}", safe="")
    listed = gh(["api", f"repos/{{owner}}/{{repo}}/pulls?head={head}&state=all&per_page={BRANCH_PRS_PER_PAGE}"])
    if listed.code != 0:
        return None, gh_reason(listed)
    unexpected = f"gh api printed something unexpected: {listed.out[:120]!r}"
    try:
        pulls = json.loads(listed.out)
    except ValueError:
        return None, unexpected
    if not isinstance(pulls, list):
        return None, unexpected
    prs = []
    for pull in pulls:
        number, state = json_at(pull, "number"), json_at(pull, "state")
        if not isinstance(number, int) or state not in ("open", "closed"):
            return None, unexpected
        # GitHub lists every PR when it can't read the head filter: one on another branch isn't this branch's
        if json_at(pull, "head", "ref") != branch or not pull_from_this_repo(pull):
            continue
        prs.append(
            BranchPr(
                number,
                "OPEN" if state == "open" else "MERGED" if json_at(pull, "merged_at") else "CLOSED",
                text(json_at(pull, "base", "ref")),
                head_commit(json_at(pull, "head", "sha")),
                text(json_at(pull, "html_url")),
                text(json_at(pull, "title")),
            )
        )
    return sorted(prs, key=lambda pr: -pr.number), ""


def pull_from_this_repo(pull: object) -> bool:
    """Whether a REST pull object's head branch is in the repo it was opened on, rather than a fork's: from_this_repo's
    rule, so a pull that doesn't name both repos (a deleted fork's head repo is null) counts as a fork's."""
    head_repo, base_repo = json_at(pull, "head", "repo", "full_name"), json_at(pull, "base", "repo", "full_name")
    return isinstance(head_repo, str) and isinstance(base_repo, str) and head_repo.lower() == base_repo.lower()


def text(value: object) -> str:
    """value when it's a string, else ""."""
    return value if isinstance(value, str) else ""


def branch_pr(directory: Path, branch: str) -> tuple[BranchPr | None, str]:
    """(branch's PR, gh's error): its open PR, else its newest, of branch_prs, read with gh in directory. The one place
    "open first, else newest" is decided. gh's error is "" when GitHub answered."""
    prs, error = branch_prs(run_gh(directory), branch)
    if not prs:
        return None, error
    return max(prs, key=lambda p: (p.state == "OPEN", p.number)), ""


def branch_done(gh: Gh, branch: str) -> bool | None:
    """Whether branch has PRs from this repo (branch_prs) and none open: merged or closed, a stack branch that needs
    nothing more. None when GitHub couldn't be read."""
    prs, _ = branch_prs(gh, branch)
    if prs is None:
        return None
    return bool(prs) and all(pr.state != "OPEN" for pr in prs)


def head_commit(head: object) -> str:
    """head when it's a full commit id, else "": only a commit id goes on to git, never something it could read as an
    option."""
    return head if isinstance(head, str) and COMMIT_ID.fullmatch(head) else ""


def unless_earlier_work(worktree: Path, pr: BranchPr) -> tuple[BranchPr | None, str]:
    """(pr, or None when it's earlier work on a reused branch name; a note saying which, or that it couldn't be
    checked). A merged or closed PR whose head doesn't contain the worktree's HEAD is that earlier work. One whose head
    isn't in this clone (a suggestion accepted on GitHub, not fetched), or that gh gave no head for, can't be checked,
    so it's trusted."""
    if pr.state == "OPEN":
        return pr, ""
    if not pr.head:
        return pr, "GitHub didn't give its head commit, so it wasn't checked against this worktree"
    if git(worktree, "cat-file", "-e", f"{pr.head}^{{commit}}") is None:
        return pr, f"its head {pr.head[:7]} isn't in this clone, so it wasn't checked against this worktree"
    if git(worktree, "merge-base", "--is-ancestor", "HEAD", pr.head) is None:
        return None, f"PR #{pr.number} {pr.state.lower()} earlier work on this branch name"
    return pr, ""


def worktree_pr(worktree: Path, branch: str) -> tuple[BranchPr | None, str, str]:
    """(the worktree's PR, a note, gh's error): branch_pr's pick for the worktree's branch, unless it's earlier work on
    a reused branch name (unless_earlier_work). `tower card` and the adopt scan decide it here; `tower state --pr`
    runs the same two steps itself, to hand the stacks the pick before the second."""
    pr, error = branch_pr(worktree, branch)
    if pr is None:
        return None, "", error
    kept, note = unless_earlier_work(worktree, pr)
    return kept, note, ""
