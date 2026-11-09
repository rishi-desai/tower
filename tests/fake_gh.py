"""The `gh` that conftest.fake_github puts on PATH: lists a branch's PRs the way gh and GitHub do for acme/widget.

Run as `python fake_gh.py PRS_FILE FAILING GH_ARGS...`: PRS_FILE holds gh pr list's records, newest first; FAILING is a
JSON object mapping a branch to the message asking about it fails with. conftest.fake_github's docstring has the rules.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

Record = dict[str, object]
OWNER, REPO = "acme", "widget"
CURRENT_BRANCH = "the-current-branch"  # what gh fills {branch} with
PLACEHOLDER = re.compile(r":(owner|repo|branch)\b|\{(owner|repo|branch)\}")  # gh fills both forms, :repo too


def main(argv: list[str]) -> int:
    """Answer one gh command line; its exit code."""
    prs: list[Record] = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    failing: dict[str, str] = json.loads(argv[2])
    args = argv[3:]
    if args[:2] in (["auth", "status"], ["repo", "view"]):
        return 0
    if args[:2] == ["pr", "list"]:
        return pr_list(prs, failing, args)
    if args[:1] == ["api"] and len(args) == 2:
        return api_pulls(prs, failing, args[1])
    return 9


def pr_list(prs: list[Record], failing: dict[str, str], args: list[str]) -> int:
    """`gh pr list [--head B] --json F [--limit N]` as gh does: every PR on B, forks too, newest first, cut at --limit
    (30 unless given), with only the fields asked for."""
    head, fields = flag(args, "--head"), (flag(args, "--json") or "").split(",")
    if head in failing:
        print(failing[head], file=sys.stderr)
        return 1
    listed = [pr for pr in prs if head is None or on_branch(pr, head)][: int(flag(args, "--limit") or "30")]
    print(json.dumps([{key: value for key, value in pr.items() if key in fields} for pr in listed]))
    return 0


def api_pulls(prs: list[Record], failing: dict[str, str], endpoint: str) -> int:
    """`gh api 'repos/{owner}/{repo}/pulls?...'` as GitHub answers it once gh has filled the placeholders:
    head=acme:B gives B's PRs whose head repo is acme's, or gone (a deleted fork no longer says whose it was); a head
    without an owner is ignored, listing every PR; state defaults to open and per_page to 30."""
    names = {"owner": OWNER, "repo": REPO, "branch": CURRENT_BRANCH}
    url = urlsplit(PLACEHOLDER.sub(lambda match: names[match[1] or match[2]], endpoint))
    if url.path != f"repos/{OWNER}/{REPO}/pulls":
        return 9
    query = {key: values[-1] for key, values in parse_qs(url.query).items()}
    owner, colon, branch = query.get("head", "").partition(":")
    if branch in failing:
        print(failing[branch], file=sys.stderr)
        return 1
    if colon:
        prs = [pr for pr in prs if on_branch(pr, branch) and head_owner(pr) in (owner, None)]
    if query.get("state") != "all":
        prs = [pr for pr in prs if pr.get("state") == "OPEN"]
    print(json.dumps([rest_pull(pr, branch) for pr in prs[: int(query.get("per_page", "30"))]]))
    return 0


def flag(args: list[str], name: str) -> str | None:
    """The value after name in args, or None."""
    return args[args.index(name) + 1] if name in args else None


def on_branch(pr: Record, branch: str) -> bool:
    """Whether pr is on branch: a record without headRefName is on every branch."""
    return pr.get("headRefName", branch) == branch


def head_repo(pr: Record) -> str | None:
    """pr's head repo's full name: headRepository when given (None: a deleted fork), else this repo for a record whose
    isCrossRepository is false, else a stranger's fork."""
    if "headRepository" in pr:
        given = pr["headRepository"]
        return given if isinstance(given, str) else None
    return f"{OWNER}/{REPO}" if pr.get("isCrossRepository") is False else "stranger/widget"


def head_owner(pr: Record) -> str | None:
    """The owner of pr's head repo, or None when it's gone."""
    repo = head_repo(pr)
    return repo.split("/")[0] if repo else None


def rest_pull(pr: Record, branch: str) -> Record:
    """pr as REST's pull object, with only the fields the record has (and the queried branch for one on every
    branch)."""
    repo = head_repo(pr)
    head: Record = {"ref": pr.get("headRefName", branch), "repo": {"full_name": repo} if repo else None}
    base: Record = {"repo": {"full_name": f"{OWNER}/{REPO}"}}
    pull: Record = {
        "number": pr["number"],
        "state": "open" if pr.get("state") == "OPEN" else "closed",
        "merged_at": "2026-11-06T10:00:00Z" if pr.get("state") == "MERGED" else None,
        "head": head,
        "base": base,
    }
    if "headRefOid" in pr:
        head["sha"] = pr["headRefOid"]
    if "baseRefName" in pr:
        base["ref"] = pr["baseRefName"]
    if "url" in pr:
        pull["html_url"] = pr["url"]
    if "title" in pr:
        pull["title"] = pr["title"]
    return pull


if __name__ == "__main__":
    sys.exit(main(sys.argv))
