"""overlaps.py: worktrees that changed the same files, found from git, never from cards.

Two workers editing one file find out at merge time, after both have built on their version. tower state checks every
pair of worktrees (the main checkout excluded: it is the hub's) and reports the files both changed, so the hub can say
so under Housekeeping while there is still time to coordinate.

What counts as a worktree's change, for one pair A and B: the files touched by A's own commits (on A, not on B and not
on the base branch), plus A's uncommitted and untracked files. A committed file only counts while A's version of it
still differs from the base's: once A is squash-merged, the base has the same content and A stops overlapping work
that started after the merge. Comparing each branch only to the base would flag every pair of branches cut from the
same integration branch (its commits aren't on the base yet), and every branch stacked on another (the lower branch's
commits are in both). Counted this way, a stacked branch overlaps its parent only on files it changes itself.
Renames count as a delete plus an add, so a file renamed in one worktree and edited in the other is flagged.

Cost: the pane runs this every 30 seconds, so git is asked three things per worktree (its commits not on the base
with their files, which files differ from the base, and `git status`), and every pair is worked out from those sets.

Known limits: a merge commit's own changes (conflict resolutions) aren't counted, since listing a merge's files would
list everything the merged branch brought in. A branch that merged in newer integration commits than its sibling has
counts those commits as its own, so the sibling may be flagged against it for a file only the integration branch
changed (the hub's row still names the file to look at). Without a base branch, nothing is checked.

Runs under whatever `python3` the plugin's scripts find (3.9 on a stock Mac), like the rest of the core.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from tower.gitrepo import GitWorktree, git

COMMIT_MARK = "\x01"  # starts each commit's hash in the `git log` output below, so it can't be taken for a path


@dataclass(frozen=True)
class Overlap:
    """Two worktrees, by label, and the files both changed (sorted)."""

    first: str
    second: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class WorktreeChanges:
    """What one worktree changed: its commits not on the base, each with the files it touched; the files whose
    content differs from the base's; and its uncommitted and untracked files."""

    commits: dict[str, frozenset[str]]
    differs_from_base: frozenset[str]
    uncommitted: frozenset[str]

    def own_files(self, other: WorktreeChanges) -> set[str]:
        """The files this worktree changed that other didn't bring along: its commits other lacks, still differing
        from the base, plus everything uncommitted."""
        committed = {path for sha, paths in self.commits.items() if sha not in other.commits for path in paths}
        return (committed & self.differs_from_base) | self.uncommitted


def find_overlaps(
    main_checkout: Path, worktrees: list[GitWorktree], base: str | None
) -> tuple[list[Overlap], list[str]]:
    """Every pair of worktrees (in git's order) that changed a file in common, and notes on what git couldn't say.
    worktrees should leave out the main checkout; one whose folder is gone (`git worktree prune` not run yet) is
    skipped. base is the base branch; without one nothing is checked."""
    if base is None:
        return [], ["no base branch, so worktrees weren't checked for changing the same files"]
    notes: list[str] = []
    changes: list[tuple[GitWorktree, WorktreeChanges]] = []
    for wt in worktrees:
        if not os.path.isdir(wt.path):
            continue
        found = worktree_changes(main_checkout, wt, base)
        if found is None:
            notes.append(f"git couldn't list what {wt.label} changed, so it wasn't checked for overlaps")
        else:
            changes.append((wt, found))
    overlaps = []
    for (first, first_changes), (second, second_changes) in combinations(changes, 2):
        shared = first_changes.own_files(second_changes) & second_changes.own_files(first_changes)
        if shared:
            overlaps.append(Overlap(first.label, second.label, tuple(sorted(shared))))
    return overlaps, notes


def worktree_changes(main_checkout: Path, worktree: GitWorktree, base: str) -> WorktreeChanges | None:
    """What worktree changed relative to base, from three git calls; None if any of them fails."""
    tip = revision(worktree)
    log = git(
        main_checkout, "log", "--no-renames", "--name-only", "-z", f"--format={COMMIT_MARK}%H", tip, f"^{base}", "--"
    )
    differs = git(main_checkout, "diff", "--name-only", "-z", "--no-renames", base, tip, "--")
    status = git(Path(worktree.path), "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all")
    if log is None or differs is None or status is None:
        return None
    return WorktreeChanges(
        commits=commits_with_files(log),
        differs_from_base=frozenset(null_separated(differs)),
        # Each entry is "XY path": two status letters and a space, then the path as it is (-z: never quoted).
        uncommitted=frozenset(entry[3:] for entry in null_separated(status) if len(entry) > 3),
    )


def revision(worktree: GitWorktree) -> str:
    """How git names the worktree's commit: its branch in full (a tag with the same name can't be taken for it), or
    the commit itself on a detached HEAD."""
    return f"refs/heads/{worktree.branch}" if worktree.branch else worktree.sha


def commits_with_files(log: str) -> dict[str, frozenset[str]]:
    """`git log --name-only -z --format=<COMMIT_MARK>%H` output as {commit: the files it touched}. A merge commit
    lists no files."""
    commits: dict[str, set[str]] = {}
    current: set[str] = set()
    for token in null_separated(log):
        if token.startswith(COMMIT_MARK):
            current = commits.setdefault(token[len(COMMIT_MARK) :], set())
        else:
            current.add(token)
    return {sha: frozenset(paths) for sha, paths in commits.items()}


def null_separated(out: str) -> list[str]:
    """The entries of git's -z output: NUL-terminated, so spaces, quotes and non-ASCII names come through as they are.
    A newline git log puts between commits is dropped."""
    return [entry.lstrip("\n") for entry in out.split("\0") if entry.lstrip("\n")]
