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
# A ticket guess takes the number after an issue word or a known type (issue-42, feat-42-x, fix/42-x), even behind a
# user or version prefix (rishi-fix-64, v2-fix-42), or a bare number starting the name or a /-part (42-x). A version or
# date isn't one: release-2026-11 and 2026-11-notes guess none.
_TICKET_WORDS = r"issue|gh|feat|fix|chore|docs|refactor|test|perf|ci|build|spike|bug|hotfix"
_YEAR_MONTH = r"\d{4}-(?:0?[1-9]|1[0-2])(?:-|$)"
BRANCH_TICKET = re.compile(rf"(?:(?:^|[-/])(?:{_TICKET_WORDS})[-/]|(?:^|/)(?!{_YEAR_MONTH}))(\d+)(?:-|$)")


class NotARepo(Exception):
    """The folder given to a scan isn't inside a git repo."""


@dataclass(frozen=True)
class GitWorktree:
    """One block of `git worktree list --porcelain`. branch is None on a detached HEAD."""

    path: str
    sha: str
    branch: str | None

    @property
    def label(self) -> str:
        """How every report names it: the branch, or "(detached <short sha>)"."""
        return self.branch or f"(detached {self.sha[:7]})"


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
            found.append(GitWorktree(path, sha, branch))
            path, sha, branch = "", "", None
    return found


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
    """The ticket a branch name points at (rishi/feat-42-x, rishi-fix-42, issue-42, 42-x -> #42), or none."""
    match = BRANCH_TICKET.search(branch)
    return f"#{match.group(1)}" if match else "none"
