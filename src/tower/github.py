"""github.py: the merged PRs `tower stats` measures, read from GitHub with the gh CLI (read-only calls only).

A PR's tickets are its closingIssuesReferences plus every "Closes/Fixes/Resolves #N" in its body: GitHub links a
closing keyword only for PRs into the default branch, and tower's workers often merge into an integration branch.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime

from tower.stats import MergedPr

GH_TIMEOUT_SECONDS = 60
PR_LIMIT = 1000  # gh's --limit; far more merges than one window holds
CLOSING_KEYWORD = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.IGNORECASE)


class GhError(Exception):
    """A gh call failed: not installed, not logged in, not a GitHub repo, or timed out."""


def merged_prs(repo: str, since: datetime, reopens_from: datetime) -> list[MergedPr]:
    """The PRs merged in repo (a checkout's path) at or after since, each with its tickets. Reopen counts (one gh call
    per ticket) are fetched only for PRs merged at or after reopens_from; earlier ones get 0."""
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
        "number,title,headRefName,mergedAt,body,closingIssuesReferences",
    )
    try:
        listed = json.loads(output)
    except ValueError as error:
        raise GhError(f"gh pr list in {repo}: output isn't JSON: {output[:200]!r}") from error
    prs = []
    for item in listed:
        try:
            number, title, branch = int(item["number"]), str(item["title"]), str(item["headRefName"])
            merged_at = datetime.fromisoformat(item["mergedAt"])
        except (KeyError, TypeError, ValueError) as error:
            raise GhError(f"gh pr list in {repo}: unexpected PR record {item!r}") from error
        if merged_at < since:
            continue
        closes = tickets(item.get("body") or "", item.get("closingIssuesReferences") or [])
        reopened = sum(reopens_after(repo, ticket, merged_at) for ticket in closes) if merged_at >= reopens_from else 0
        prs.append(MergedPr(repo, number, title, branch, merged_at, closes, reopened))
    return prs


def tickets(body: str, closing_references: list[dict[str, object]]) -> tuple[int, ...]:
    """The ticket numbers a PR closes, sorted: its closing references plus closing keywords in its body."""
    numbers = {int(match) for match in CLOSING_KEYWORD.findall(body)}
    for ref in closing_references:
        number = ref.get("number")
        if isinstance(number, int):
            numbers.add(number)
    return tuple(sorted(numbers))


def reopens_after(repo: str, issue: int, merged_at: datetime) -> int:
    """How many times issue was reopened after merged_at."""
    out = gh(
        repo,
        "api",
        "--paginate",
        f"repos/{{owner}}/{{repo}}/issues/{issue}/events",
        "--jq",
        '.[] | select(.event == "reopened") | .created_at',
    )
    try:
        return sum(1 for line in out.splitlines() if line and datetime.fromisoformat(line) > merged_at)
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
