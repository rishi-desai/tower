"""github.py: the merged PRs `tower stats` measures, read from GitHub with the gh CLI (read-only calls only), and
try_gh() for the scans' GitHub facts.

A PR's tickets are the "Closes/Fixes/Resolves #N" in its body, read here rather than from GitHub's
closingIssuesReferences: GitHub links a closing keyword only for PRs into the default branch (tower's workers often
merge into an integration branch), and its references can name another repo's issue. "Closes owner/repo#N" doesn't
match, so a ticket is always this repo's. PRs from forks are left out: their head branch isn't a local branch.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from tower.stats import MergedPr

GH_TIMEOUT_SECONDS = 60
PR_LIMIT = 1000  # gh's --limit; far more merges than one window holds
CLOSING_KEYWORD = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.IGNORECASE)


class GhError(Exception):
    """A gh call failed: not installed, not logged in, not a GitHub repo, or timed out."""


def from_this_repo(pr: dict[str, object]) -> bool:
    """Whether a `gh pr list` record is a PR from this repo rather than a fork: a stranger's patch-1 isn't the work
    of the worktree whose branch has that name. A record without isCrossRepository (gh wasn't asked for it) counts as
    a fork, so a caller that forgot the field shows no PRs rather than strangers'."""
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
