"""stacks.py: the stacks git-spice tracks in a repo, as trees in merge order, and which branches need a restack.

A stack is branches each built on the one below (its parent), the bottom one on the stack's base. git-spice keeps each
branch's parent in one ref shared by every worktree, `refs/spice/data`: a tree of JSON blobs, `repo` ({"trunk":
"main"}), an optional `version` ("1"), and `branches/<name>` ({"base": {"name": <parent>, "hash": ...}, "change":
{"github": {"pr": {"number": 12}}}} once submitted). tower reads that ref with `git cat-file` and never runs git-spice:
git-spice's own commands write to the ref (even `git-spice log` initializes a repo that never used it), and a scan must
never write (docs/adr/0010-stacks-one-worktree-read-from-git-spices-ref.md). A repo without the ref has no stacks, and
nothing is said about it.

Per branch, tower adds what the hub needs:

    merged       its PR merged (GitHub says so, when PRs were read), or, without GitHub, git-spice submitted it and its
                 tip is already on the stack's base (`tower merge` merges a stacked PR with a merge commit)
    restack      why it needs a restack: its parent merged or is gone (deleted before git-spice noticed), or its
                 parent moved (the parent's tip isn't in this
                 branch: the parent was rebased or amended), git-spice's own test. A bottom branch is never flagged
                 for its base moving on: GitHub merges it all the same.
    next         the lowest branch that hasn't merged and sits on the stack's base or on a merged parent: the only one
                 that may merge now

Only stacks of two or more branches are listed: one tracked branch on its own is an ordinary PR.

Runs under whatever `python3` the plugin's scripts find (3.9 on a stock Mac), like the rest of the core.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tower.gitrepo import COMMAND_TIMEOUT_SECONDS, git, guess_ticket

TRACKING_REF = "refs/spice/data"
BRANCHES_DIR = "branches/"
READ_VERSIONS = ("1",)  # git-spice's tracking layout versions tower reads; 2 is its fork mode


@dataclass(frozen=True)
class Tracked:
    """One branch git-spice tracks: its name, its parent, and its PR number once git-spice submitted it."""

    name: str
    parent: str
    pr: int | None


@dataclass(frozen=True)
class StackBranch:
    """One branch of a stack, as the hub and the pane show it. depth is 0 for the bottom branch; worktree is the path
    of the worktree that has it checked out, if any; pr_state is GitHub's (OPEN, MERGED, CLOSED), None when PRs
    weren't read. ticket is the ticket guess from its name (#41, or none). merged, restack and next: see the module
    docstring."""

    name: str
    ticket: str
    parent: str
    depth: int
    worktree: str | None
    pr: int | None
    pr_state: str | None
    merged: bool
    restack: str
    next: bool


@dataclass(frozen=True)
class Stack:
    """A stack: the branch its bottom branch is built on, its branches in merge order (bottom first, each branch
    before the ones built on it), git-spice's trunk, the git comparisons that failed while working out its flags (so a
    flag may be missing), and whether that left which branches merged unknown."""

    base: str
    branches: tuple[StackBranch, ...]
    trunk: str = ""
    failed: tuple[str, ...] = ()
    merged_unknown: bool = False

    @property
    def worktrees(self) -> list[str]:
        """The worktrees holding a branch of this stack (one, for a stack worktree), in merge order."""
        return list(dict.fromkeys(b.worktree for b in self.branches if b.worktree))

    def unmerged_besides(self, branch: str | None) -> list[str]:
        """The stack's branches other than branch that haven't merged and weren't closed on GitHub: while there are
        any, a stack worktree isn't finished, whatever its own branch's PR says."""
        return [b.name for b in self.branches if not b.merged and b.pr_state != "CLOSED" and b.name != branch]


class PrLike(Protocol):
    """What stacks reads from a PR: its number, state, and head commit (state.Pr has all three)."""

    @property
    def number(self) -> int: ...

    @property
    def state(self) -> str: ...

    @property
    def head_oid(self) -> str | None: ...


@dataclass(frozen=True)
class Tracking:
    """What git-spice's tracking ref says: its trunk, the remote it pushes to, and the branches it tracks by name."""

    trunk: str
    remote: str
    branches: dict[str, Tracked]


def read_tracking(repo_dir: Path) -> tuple[Tracking | None, str]:
    """(git-spice's tracking for the repo holding repo_dir, a note). No ref: (None, ""). A ref tower can't read: (None,
    why), so the report says stacks weren't shown instead of showing none."""
    present = git(repo_dir, "for-each-ref", "--format=%(refname)", TRACKING_REF)
    if present is None:
        return None, unreadable("git for-each-ref failed")
    if not present:
        return None, ""
    listed = git(repo_dir, "ls-tree", "-r", "-z", "--full-tree", TRACKING_REF)
    if listed is None:
        return None, unreadable("git ls-tree failed")
    paths: dict[str, str] = {}
    for entry in listed.split("\0"):
        info, _, path = entry.partition("\t")
        if path and info.split(" ")[1:2] == ["blob"]:
            paths[path] = info.split(" ")[2]
    blobs = read_blobs(repo_dir, list(paths.values()))
    if blobs is None:
        return None, unreadable("git cat-file failed")
    files = dict(zip(paths, blobs))
    version = files.get("version", "1").strip()
    if version not in READ_VERSIONS:
        return (
            None,
            f"git-spice's tracking is in layout version {version}, which tower doesn't read",
        )
    try:
        repo = json.loads(files["repo"])
        trunk, remote = repo["trunk"], repo.get("remote")
        tracked = {
            path[len(BRANCHES_DIR) :]: tracked_branch(path[len(BRANCHES_DIR) :], json.loads(text))
            for path, text in files.items()
            if path.startswith(BRANCHES_DIR)
        }
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        return None, unreadable(f"unexpected content: {type(error).__name__} {error}")
    if not isinstance(trunk, str) or not trunk:
        return None, unreadable("no trunk")
    remote = remote if isinstance(remote, str) and remote and "/" not in remote else "origin"
    return Tracking(trunk, remote, tracked), ""


def unreadable(why: str) -> str:
    """The report's note for a tracking ref tower couldn't read."""
    return f"couldn't read git-spice's tracking ({TRACKING_REF}: {why})"


def tracked_branch(name: str, state: object) -> Tracked:
    """One `branches/<name>` blob as a Tracked. Raises KeyError, TypeError or ValueError when it isn't one."""
    if not isinstance(state, dict):
        raise TypeError(f"branches/{name} isn't an object")
    parent = state["base"]["name"]
    if not isinstance(parent, str) or not parent:
        raise ValueError(f"branches/{name} has no base name")
    pr = None
    change = state.get("change")
    if isinstance(change, dict) and isinstance(change.get("github"), dict):
        found = change["github"].get("pr")
        number = found.get("number") if isinstance(found, dict) else found  # a bare number in older git-spice
        pr = number if isinstance(number, int) and not isinstance(number, bool) else None
    return Tracked(name, parent, pr)


def read_blobs(repo_dir: Path, oids: list[str]) -> list[str] | None:
    """The contents of oids, in order, from one `git cat-file --batch`; None if git failed or answered unexpectedly."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "cat-file", "--batch"],
            input="".join(f"{oid}\n" for oid in oids).encode(),
            check=False,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out, at, found = proc.stdout, 0, []
    for _ in oids:
        header_end = out.find(b"\n", at)
        header = out[at:header_end].split(b" ")
        if header_end < 0 or len(header) != 3 or header[1] != b"blob" or not header[2].isdigit():
            return None
        size = int(header[2])
        found.append(out[header_end + 1 : header_end + 1 + size].decode("utf-8", errors="replace"))
        at = header_end + 1 + size + 1  # the content, then a newline
    return found


def parent_of(repo_dir: Path, branch: str) -> str | None:
    """The branch git-spice says branch is built on, or None when it doesn't track it (or there's no git-spice)."""
    tracking = read_tracking(repo_dir)[0]
    found = tracking.branches.get(branch) if tracking else None
    return found.parent if found else None


def open_besides(
    repo_dir: Path, branch: str, done_on_github: Callable[[str], bool | None] | None = None
) -> tuple[list[str] | None, str]:
    """(the other branches of branch's stack still to merge, a note): what keeps a stack worktree whose branch merged
    from being finished, the rule `tower state` uses (Stack.unmerged_besides). done_on_github, when given, says whether
    GitHub has a branch's PR merged or closed (None: it couldn't be asked), so a squash merge, which leaves nothing on
    the base that git can see, still counts. [] when git-spice doesn't track branch or it's in no stack. None and why
    when it can't be told: the tracking can't be read but has branch in it, git couldn't tell which branches are on
    the base, or GitHub couldn't be asked."""
    tracking, note = read_tracking(repo_dir)
    if tracking is None:
        tracked_here = note and git(repo_dir, "cat-file", "-e", f"{TRACKING_REF}:{BRANCHES_DIR}{branch}") is not None
        return (None, note) if tracked_here else ([], "")
    if branch not in tracking.branches:
        return [], ""
    found, _ = read_stacks(repo_dir, {})
    stack = next((s for s in found if any(b.name == branch for b in s.branches)), None)
    if stack is None:
        return [], ""
    if stack.merged_unknown:
        return None, f"git couldn't tell which branches are already on {stack.base}"
    unmerged = stack.unmerged_besides(branch)
    if done_on_github is None:
        return unmerged, ""
    still = []
    for name in unmerged:
        done = done_on_github(name)
        if done is None:
            return None, f"GitHub couldn't be asked about {name}'s PR"
        if not done:
            still.append(name)
    return still, ""


def read_stacks(
    repo_dir: Path, worktrees: dict[str, str], prs: Mapping[str, PrLike] | None = None
) -> tuple[list[Stack], str]:
    """(every stack of two or more branches in the repo holding repo_dir, a note when something couldn't be read).
    worktrees maps a checked-out branch to its worktree's path; prs maps a branch to its PR, when GitHub was asked."""
    tracking, note = read_tracking(repo_dir)
    if tracking is None:
        return [], f"{note}, so stacks weren't shown" if note else ""
    heads = git(repo_dir, "for-each-ref", "--format=%(refname)", "refs/heads/")
    if heads is None:
        return [], "git for-each-ref failed, so stacks weren't shown"
    existing = set(heads.splitlines())
    # A branch deleted outside git-spice is still in its tracking until a git-spice command notices: skip it, and put
    # the stack on what it was built on (its own parent, down to a branch that still exists).
    gone = {name: t.parent for name, t in tracking.branches.items() if f"refs/heads/{name}" not in existing}
    tracked = {name: t for name, t in tracking.branches.items() if name not in gone}
    # Where each branch really sits: on its parent, or below a deleted one (so a stack never splits at a gap).
    sits_on = {name: below_gone(t.parent, gone) for name, t in tracked.items()}
    children: dict[str, list[str]] = {}
    for name in sorted(tracked):
        children.setdefault(sits_on[name], []).append(name)
    roots = [name for name in sorted(tracked) if sits_on[name] not in tracked]
    found = []
    facts = StackFacts(repo_dir, tracking.remote, tracked, sits_on, worktrees, prs or {})
    for root in roots:
        order = in_merge_order(root, children)
        if len(order) >= 2:
            rows, failed, merged_unknown = stack_branches(facts, sits_on[root], order)
            found.append(Stack(sits_on[root], rows, tracking.trunk, failed, merged_unknown))
    failed_anywhere = [f for stack in found for f in stack.failed]
    if failed_anywhere:
        count = len(failed_anywhere)
        return (
            found,
            f"git couldn't compare {count} branch(es) ({failed_anywhere[0]}), so some stack flags may be missing",
        )
    return found, ""


def below_gone(branch: str, gone: dict[str, str]) -> str:
    """branch, or, when it was deleted outside git-spice, the first branch down its tracked parents that wasn't (a
    branch listed once, so hand-edited tracking with a cycle can't loop)."""
    seen: set[str] = set()
    while branch in gone and branch not in seen:
        seen.add(branch)
        branch = gone[branch]
    return branch


def in_merge_order(root: str, children: dict[str, list[str]]) -> list[tuple[str, int]]:
    """(branch, depth) from root up, each branch before the ones built on it (depth-first, siblings by name). A branch
    is listed once, so a cycle in hand-edited tracking can't loop."""
    order: list[tuple[str, int]] = []
    seen: set[str] = set()
    pending = [(root, 0)]
    while pending:
        name, depth = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        order.append((name, depth))
        pending += [(child, depth + 1) for child in reversed(children.get(name, []))]
    return order


@dataclass(frozen=True)
class StackFacts:
    """What stack_branches works from: the repo, git-spice's remote, its existing tracked branches and where each
    sits (its parent, or below a deleted one), the checked-out branches' worktrees, and GitHub's PRs."""

    repo_dir: Path
    remote: str
    tracked: dict[str, Tracked]
    sits_on: dict[str, str]
    worktrees: dict[str, str]
    prs: Mapping[str, PrLike]

    def pr(self, name: str) -> PrLike | None:
        """GitHub's PR for branch name, when it's this branch's: the one git-spice submitted (same number) or, before
        git-spice recorded one, an open PR or one whose head contains the branch. A merged PR from earlier work on a
        reused branch name isn't it; one whose head isn't in this clone can't be checked, so it's trusted, as
        github.unless_earlier_work does."""
        pr = self.prs.get(name)
        recorded = self.tracked[name].pr
        if pr is None or (recorded is not None and pr.number != recorded):
            return None
        earlier_work = (
            recorded is None
            and pr.state != "OPEN"
            and pr.head_oid is not None
            and is_ancestor(self.repo_dir, f"refs/heads/{name}", pr.head_oid) is False
        )
        return None if earlier_work else pr


def merged_refs(repo_dir: Path, landed: str, remote: str) -> set[str] | None:
    """Every local branch, and every remote-tracking branch of remote, whose tip is already on landed, as full ref
    names, from one `git for-each-ref --merged`; None if git failed."""
    out = git(
        repo_dir, "for-each-ref", f"--merged={landed}", "--format=%(refname)", "refs/heads/", f"refs/remotes/{remote}/"
    )
    return None if out is None else set(out.splitlines())


def stack_branches(
    facts: StackFacts, base: str, order: list[tuple[str, int]]
) -> tuple[tuple[StackBranch, ...], tuple[str, ...], bool]:
    """(the stack's branches, order being (branch, depth) in merge order, with their PR, merged, restack and next
    worked out; the git comparisons that failed, so a flag may be missing; whether which branches merged is unknown). A branch submitted with no PR read from
    GitHub is merged when its tip, or GitHub's copy of it (GitHub rebases the PRs above a merged one), is on the
    stack's base. A branch whose parent was deleted outside git-spice (it sits below it) needs a restack: git-spice
    repo sync moves it."""
    failed: list[str] = []
    landed = f"refs/remotes/{facts.remote}/{base}"
    if git(facts.repo_dir, "rev-parse", "--verify", "--quiet", landed) is None:
        landed = f"refs/heads/{base}"
    on_base = merged_refs(facts.repo_dir, landed, facts.remote)
    if on_base is None:
        failed.append(f"the branches with {landed}")
    prs = {name: facts.pr(name) for name, _ in order}
    merged: dict[str, bool] = {}
    for name, _ in order:
        pr = prs[name]
        if pr is not None:
            merged[name] = pr.state == "MERGED"
        else:
            refs = {f"refs/heads/{name}", f"refs/remotes/{facts.remote}/{name}"}
            merged[name] = facts.tracked[name].pr is not None and bool(on_base and refs & on_base)
    rows = []
    next_found = False
    for name, depth in order:
        parent, sits_on, pr = facts.tracked[name].parent, facts.sits_on[name], prs[name]
        restack = ""
        if parent != base and not merged[name]:
            if sits_on != parent:
                restack = f"its parent {parent} is gone (merged, or deleted)"
            elif merged.get(parent):
                restack = f"its parent {parent} merged"
            else:
                holds_parent = is_ancestor(facts.repo_dir, f"refs/heads/{parent}", f"refs/heads/{name}")
                if holds_parent is None:
                    failed.append(f"{parent} and {name}")
                elif not holds_parent:
                    restack = f"its parent {parent} moved"
        closed = pr is not None and pr.state == "CLOSED"  # abandoned: it can't merge
        is_next = not (next_found or merged[name] or closed) and (sits_on == base or merged.get(sits_on, False))
        next_found = next_found or is_next
        rows.append(
            StackBranch(
                name=name,
                ticket=guess_ticket(name),
                parent=parent,
                depth=depth,
                worktree=facts.worktrees.get(name),
                pr=pr.number if pr is not None else facts.tracked[name].pr,
                pr_state=pr.state if pr is not None else None,
                merged=merged[name],
                restack=restack,
                next=is_next,
            )
        )
    return tuple(rows), tuple(failed), on_base is None


def is_ancestor(repo_dir: Path, ancestor: str, descendant: str) -> bool | None:
    """Whether ancestor's commit is in descendant's history (`git merge-base --is-ancestor` exits 0 or 1); None when
    git failed (a bad ref, a timeout), which isn't a no."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {0: True, 1: False}.get(proc.returncode)
