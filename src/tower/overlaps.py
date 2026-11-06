"""overlaps.py: worktrees that changed the same files, found from git, never from cards.

Two workers editing one file find out at merge time, after both have built on their version. tower state checks every
pair of worktrees (the main checkout excluded: it is the hub's) and reports the files both changed, so the hub can say
so under Housekeeping while there is still time to coordinate.

What counts as a worktree's change, for one pair A and B: the files touched by A's own commits (on A, not on B and not
on the base branch, `git log A ^B ^base`), plus A's uncommitted and untracked files. Comparing each branch only to the
base would flag every pair of branches cut from the same integration branch (its commits aren't on the base yet), and
every branch stacked on another (the lower branch's commits are in both). Counted this way, a stacked branch overlaps
its parent only on files it changes itself, and a squash-merged branch never overlaps work that started after the
merge. Renames count as a delete plus an add, so a file renamed in one worktree and edited in the other is flagged.

Runs under whatever `python3` the plugin's scripts find (3.9 on a stock Mac), like the rest of the core.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from tower.gitrepo import GitWorktree, git


@dataclass(frozen=True)
class Overlap:
    """Two worktrees, by label, and the files both changed (sorted)."""

    first: str
    second: str
    files: tuple[str, ...]


def find_overlaps(
    main_checkout: Path, worktrees: list[GitWorktree], base: str | None
) -> tuple[list[Overlap], list[str]]:
    """Every pair of worktrees (in git's order) that changed a file in common, and notes on what git couldn't say.
    worktrees should leave out the main checkout. base is the base branch, or None when it's unknown (then commits
    on it count as each branch's own, which may flag more)."""
    notes: list[str] = []
    uncommitted: dict[str, set[str]] = {}
    for wt in worktrees:
        found = uncommitted_files(Path(wt.path))
        if found is None:
            notes.append(f"couldn't list {wt.label}'s uncommitted files, so only its commits were checked for overlaps")
        uncommitted[wt.path] = found or set()
    overlaps = []
    for first, second in combinations(worktrees, 2):
        first_own = own_commit_files(main_checkout, first, second, base)
        second_own = own_commit_files(main_checkout, second, first, base)
        if first_own is None or second_own is None:
            notes.append(f"git couldn't compare {first.label} and {second.label}, so their files weren't checked")
            continue
        shared = (first_own | uncommitted[first.path]) & (second_own | uncommitted[second.path])
        if shared:
            overlaps.append(Overlap(first.label, second.label, tuple(sorted(shared))))
    return overlaps, notes


def own_commit_files(
    main_checkout: Path, worktree: GitWorktree, other: GitWorktree, base: str | None
) -> set[str] | None:
    """Files touched by worktree's commits that neither other nor base has; None if git can't say."""
    excluded = [f"^{revision(other)}", *([f"^{base}"] if base else [])]
    out = git(
        main_checkout, "log", "--no-renames", "--name-only", "-z", "--format=", revision(worktree),
        *excluded, "--",
    )  # fmt: skip
    return None if out is None else null_separated(out)


def revision(worktree: GitWorktree) -> str:
    """How git names the worktree's commit: its branch in full (a tag with the same name can't be taken for it), or
    the commit itself on a detached HEAD."""
    return f"refs/heads/{worktree.branch}" if worktree.branch else worktree.sha


def uncommitted_files(worktree: Path) -> set[str] | None:
    """Files changed but not committed in worktree (staged or not), plus untracked ones that aren't ignored; None if
    git can't say."""
    changed = git(worktree, "diff", "--name-only", "-z", "--no-renames", "HEAD", "--")
    untracked = git(worktree, "ls-files", "-z", "--others", "--exclude-standard")
    if changed is None or untracked is None:
        return None
    return null_separated(changed) | null_separated(untracked)


def null_separated(out: str) -> set[str]:
    """The paths in git's -z output: NUL-terminated, so spaces, quotes and non-ASCII names come through as they are.
    A newline git log may put between commits is dropped."""
    return {path.lstrip("\n") for path in out.split("\0") if path.lstrip("\n")}
