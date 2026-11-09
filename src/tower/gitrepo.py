"""gitrepo.py: the git facts every scan starts from: running git, listing worktrees, naming the repo.

Runs under whatever `python3` the plugin's scripts find (3.9 on a stock Mac), like the rest of the core.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

COMMAND_TIMEOUT_SECONDS = 15
COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")  # a full SHA-1 or SHA-256 commit id, as gh prints headRefOid
# Words that name a ticket when a number follows (fix-42), even behind one user or version prefix (rishi-fix-64).
TICKET_WORDS = {"issue", "gh", "feat", "fix", "chore", "refactor", "bug", "hotfix"}
# Types that are everyday words too (node-build-18, flaky-test-2), so they name one only starting the name or a /-part.
TYPE_WORDS = TICKET_WORDS | {"feature", "bugfix", "docs", "test", "perf", "ci", "build", "spike"}
NUMBER = re.compile(r"[0-9]+")
YEAR = re.compile(r"(?:19|20)[0-9][0-9]")
MONTH = re.compile(r"0?[1-9]|1[0-2]")


class NotARepo(Exception):
    """The folder given to a scan isn't inside a git repo."""


@dataclass(frozen=True)
class GitWorktree:
    """One block of `git worktree list --porcelain`. branch is None on a detached HEAD, and only there is short_sha
    set: git's unique short sha, the one its card is named with when first written ("" otherwise)."""

    path: str
    sha: str
    branch: str | None
    short_sha: str

    @property
    def label(self) -> str:
        """How every report names it: the branch, or "(detached <short sha>)"."""
        return self.branch or f"(detached {self.short_sha})"


def git(cwd: Path, *args: str) -> str | None:
    """Run one git command in cwd; its stdout with the trailing newline removed, or None if it failed."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args], check=False, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else None


def list_worktrees(repo_dir: Path) -> list[GitWorktree]:
    """Every worktree of the repo holding repo_dir, main checkout first (git lists it first)."""
    out = git(repo_dir, "worktree", "list", "--porcelain")
    if out is None:
        raise NotARepo(f"not a git repo: {repo_dir}")
    found: list[GitWorktree] = []
    path, sha, branch = "", "", None
    for line in (out + "\n\n").split("\n"):
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("HEAD "):
            sha = line[len("HEAD ") :]
        elif line.startswith("branch "):
            branch = line[len("branch ") :].replace("refs/heads/", "", 1)
        elif line == "" and path:
            # Asked in the repo, not the worktree's folder, which may be gone (a prunable worktree).
            detached_short = "" if branch or not sha else short_sha(repo_dir, sha)
            found.append(GitWorktree(path, sha, branch, short_sha=detached_short))
            path, sha, branch = "", "", None
    return found


def short_sha(repo_dir: Path, sha: str) -> str:
    """git's short name for commit sha: longer than core.abbrev when a shorter prefix is ambiguous, so no two commits
    share it (sha[:7] isn't unique); sha[:7] if git can't say. A detached label and its card name both use it."""
    return git(repo_dir, "rev-parse", "--short", sha) or sha[:7]


def is_main_checkout(directory: Path) -> bool:
    """Whether directory is in the repo's main checkout (git lists it first), where the hub runs and tower never starts
    a worker. Raises NotARepo outside a git repo."""
    top = git(directory, "rev-parse", "--show-toplevel")
    if top is None:
        raise NotARepo(f"not a git repo: {directory}")
    return os.path.realpath(top) == os.path.realpath(list_worktrees(directory)[0].path)


def repo_name(repo_dir: Path) -> str:
    """The repo's name, the same from every worktree: the folder holding the shared .git (normal repo), or the shared
    folder itself without ".git" (bare repo, /code/poot.git -> poot)."""
    common = git(repo_dir, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common is None:
        raise NotARepo(f"not a git repo: {repo_dir}")
    if os.path.basename(common) == ".git":
        return os.path.basename(os.path.dirname(common))
    return os.path.basename(common).removesuffix(".git")


def guess_ticket(branch: str) -> str:
    """The ticket a branch name points at (rishi/feat-42-x, rishi-fix-42, issue-42, 42-x -> #42), or none.

    Each /-part is read on its own, and its first word wins: 42-fix-2-bugs is #42. When unsure it guesses none rather
    than a wrong ticket, since a stack branch's guess counts as an issue in progress with nobody to correct it.
    """
    parts = branch.split("/")
    for position, part in enumerate(parts):
        words = part.split("-")
        ends_name = position == len(parts) - 1
        if ticket_number_at(words, 0, ends_name):  # 42-x
            return f"#{words[0]}"
        if words[0] in TYPE_WORDS and ticket_number_at(words, 1, ends_name):  # feat-42-x
            return f"#{words[1]}"
        if len(words) > 1 and words[1] in TICKET_WORDS and ticket_number_at(words, 2, ends_name):  # rishi-fix-64
            return f"#{words[2]}"
    return "none"


def ticket_number_at(words: list[str], index: int, ends_name: bool) -> bool:
    """Whether words[index] of a /-part can be a ticket: all digits, followed by a - or the name's end (release/2/fix-42
    is #42), and not the year of a year-month (2026-11-notes)."""
    if index >= len(words) or not NUMBER.fullmatch(words[index]):
        return False
    if index + 1 == len(words):
        return ends_name
    return not (YEAR.fullmatch(words[index]) and MONTH.fullmatch(words[index + 1]))
