"""adopt.py: `tower adopt-scan`, the facts for bringing an in-flight repo under tower. Read-only: it changes nothing
except creating the project's empty Ledger folder.

Prints, from anywhere inside the repo:
  1. CONFIG        repo, ledger folder, default branch, WIP limit
  2. WORKTREES     per worktree: card?, uncommitted files, ahead/behind the default branch, last commit, PR for the
                   branch, ticket guessed from the branch name
  3. LIVE SESSIONS which Claude sessions are open in which worktree (from agent view)
  4. BRANCHES      local branches with unmerged commits but no worktree (parked work)

A worktree inside another one (Orca's `./worktrees` folder in the main checkout) shows in the outer one's `git status`
as an untracked folder. That isn't work on main: an untracked folder that is, or only holds, worktrees of this repo
isn't counted or listed, and a note says how to hide it from git status instead (#4). Other untracked files next to
those worktrees still count.
"""

from __future__ import annotations

import os
from pathlib import Path

from tower import ledger, policy, sessions, state
from tower.github import branch_pr as github_branch_pr
from tower.github import try_gh, worktree_pr
from tower.gitrepo import GitWorktree, git, guess_ticket, list_worktrees, repo_name

SHOWN_CHANGES = 8  # uncommitted entries listed under a worktree; the rest are counted
LAST_COMMIT_CHARS = 70


def adopt_scan(repo_dir: Path) -> str:
    """The whole scan as text. Raises NotARepo outside a git repo and LedgerError when the ledger can't be found."""
    name = repo_name(repo_dir)
    folder = ledger.open_ledger(repo_dir)
    worktrees = list_worktrees(repo_dir)
    default = default_branch(repo_dir, worktrees[0].branch)
    # Compared with origin's copy when there is one, so "ahead" means "not yet on the remote default".
    base = f"origin/{default}" if has_ref(repo_dir, f"refs/remotes/origin/{default}") else default
    repo_policy, _ = policy.for_checkout(repo_dir)
    has_gh = try_gh(repo_dir, "auth", "status")[0] is not None and try_gh(repo_dir, "repo", "view")[0] is not None

    lines = [
        "== CONFIG ==",
        (
            f"repo: {name} | ledger: {folder} | default branch: {default} (compared with {base})"
            f" | WIP limit: {repo_policy.wip_limit} | GitHub: {'yes' if has_gh else 'no'}"
        ),
        "",
        "== WORKTREES ==",
    ]
    roots = {os.path.realpath(w.path) for w in worktrees}
    for index, worktree in enumerate(worktrees):
        lines += worktree_rows(repo_dir, worktree, index == 0, folder, base, roots, has_gh)
    lines += ["", "== LIVE SESSIONS (agent view) =="]
    agents, why = sessions.read_agents()
    lines += [f"({why})"] if agents is None else sessions.render(agents, [(w.label, w.path) for w in worktrees])
    lines += ["", "== BRANCHES WITH UNMERGED WORK AND NO WORKTREE =="]
    checked_out = {w.branch for w in worktrees if w.branch}
    lines += parked_branches(repo_dir, checked_out | {default}, base, has_gh) or ["(none)"]
    return "\n".join(lines) + "\n"


def default_branch(repo_dir: Path, main_checkout_branch: str | None) -> str:
    """What origin/HEAD points at, else a local main, else a local master, else the main checkout's branch (a repo
    with no origin on `trunk`), else "main"."""
    origin_head = git(repo_dir, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if origin_head:
        return origin_head.removeprefix("origin/")
    for name in ("main", "master"):
        if has_ref(repo_dir, f"refs/heads/{name}"):
            return name
    return main_checkout_branch or "main"


def has_ref(repo_dir: Path, ref: str) -> bool:
    """Whether the exact ref (refs/heads/main, refs/remotes/origin/main) exists."""
    return git(repo_dir, "show-ref", "--verify", "--quiet", ref) is not None


def worktree_rows(
    repo_dir: Path, worktree: GitWorktree, is_main: bool, folder: Path, base: str, roots: set[str], has_gh: bool
) -> list[str]:
    """One worktree's row, then its first uncommitted entries, the worktree-folder notes, and its PR."""
    path = Path(worktree.path)
    record_problem = ""
    try:
        card_file = folder / ledger.card_file(path, worktree.branch, worktree.sha)
    except ledger.LedgerError as record_error:
        card_file = folder / ledger.card_name(worktree.branch, worktree.sha, repo_dir)
        record_problem = f" ({record_error}: looked for by its branch)"
    try:
        card = state.read_card(card_file, 0)
        card_state = f"card: {card.status}" if card else "no card"
    except OSError:
        card_state = "card: (unreadable)"
    card_state += record_problem
    changes, worktree_folders = uncommitted(path, roots)
    dirty = "?" if changes is None else str(len(changes))  # git status failed: unknown, not clean
    changes = changes or []
    counts = (git(path, "rev-list", "--left-right", "--count", f"{base}...HEAD") or "").split()
    behind, ahead = counts if len(counts) == 2 else ("?", "?")
    last = (git(path, "log", "-1", "--format=%cr: %s") or "")[:LAST_COMMIT_CHARS]
    role = "MAIN CHECKOUT" if is_main else "worktree"
    rows = [
        (
            f"- [{role}] {worktree.label} | {card_state} | uncommitted: {dirty} | ahead {ahead} / behind {behind}"
            f" of {base} | last: {last} | ticket guess: {guess_ticket(worktree.branch or '')} | {worktree.path}"
        )
    ]
    rows += [f"    {change}" for change in changes[:SHOWN_CHANGES]]
    if len(changes) > SHOWN_CHANGES:
        rows.append(f"    ... and {len(changes) - SHOWN_CHANGES} more")
    if worktree_folders:
        exclude = exclude_file(path)
        rows += [
            f"    note: {f} holds only git worktrees, so it isn't counted; to hide it from git status,"
            f" add the line /{f} to {exclude}"
            for f in worktree_folders
        ]
    if has_gh and worktree.branch and not is_main:
        pr, error = branch_pr(repo_dir, worktree.branch, with_title=True, worktree=path)
        rows.append(f"    PR: {pr}" if pr is not None else f"    PR: (gh failed: {error})")
    return rows


def branch_pr(repo_dir: Path, branch: str, with_title: bool, worktree: Path | None = None) -> tuple[str | None, str]:
    """The branch's PR as "#N STATE[ title]", "none" when it has none, or None and gh's error: the worktree's PR
    (github.worktree_pr) for a branch checked out in worktree, else github.branch_pr's pick (its open PR, else its
    newest, never a fork's), since a parked branch has no HEAD of its own to check earlier work against."""
    if worktree is None:
        pr, error = github_branch_pr(repo_dir, branch)
    else:
        pr, _, error = worktree_pr(worktree, branch)
    if error:
        return None, error
    if pr is None:
        return "none", ""
    return f"#{pr.number} {pr.state}" + (f" {pr.title}" if with_title else ""), ""


def uncommitted(worktree: Path, roots: set[str]) -> tuple[list[str] | None, list[str]]:
    """The worktree's uncommitted entries as `git status --porcelain` lines (paths unquoted), or None if git failed;
    and the untracked folders left out because they only hold worktrees (roots: every worktree's real path)."""
    entries = status_entries(worktree)
    if entries is None:
        return None, []
    changes: list[str] = []
    worktree_folders: list[str] = []
    for code, path in entries:
        if code == "??" and path.endswith("/") and holds_worktree(worktree / path, roots):
            # A worktree root itself (git lists one with status.showUntrackedFiles=all) holds nothing to look at.
            beside = [] if is_root(worktree / path, roots) else untracked_beside_worktrees(worktree, path, roots)
            if beside is None:
                return None, []  # couldn't look inside: unknown, not clean
            if beside:
                changes += [f"?? {p}" for p in beside]
            else:
                worktree_folders.append(path)
            continue
        changes.append(f"{code} {path}")
    return changes, worktree_folders


def untracked_beside_worktrees(worktree: Path, folder: str, roots: set[str]) -> list[str] | None:
    """The untracked paths in folder (relative, ending in "/") other than the worktrees in it, as git's normal
    `git status` would show them: an untracked subfolder is one entry, and ignored files don't show. None when the
    folder can't be listed or git fails."""
    paths: list[str] = []
    pathspecs: list[str] = []
    try:
        children = sorted(os.listdir(worktree / folder))
    except OSError:
        return None
    for child in children:
        inside = worktree / folder / child
        if is_root(inside, roots):
            continue
        if inside.is_dir() and not inside.is_symlink() and holds_worktree(inside, roots):
            deeper = untracked_beside_worktrees(worktree, f"{folder}{child}/", roots)
            if deeper is None:
                return None
            paths += deeper
        else:
            pathspecs.append(f"{folder}{child}")
    if pathspecs:  # one git call: with each child as a pathspec, git groups an untracked folder per child
        found = status_entries(worktree, "--", *pathspecs)
        if found is None:
            return None
        paths += [path for _, path in found]
    return sorted(paths)


def status_entries(worktree: Path, *args: str) -> list[tuple[str, str]] | None:
    """`git status --porcelain -z` as (XY code, path) pairs, or None if git failed; a rename's path is "old -> new".
    Pathspecs in args are literal, so a folder named `[a]*` means only itself."""
    out = git(worktree, "--literal-pathspecs", "status", "--porcelain", "-z", *args)
    if out is None:
        return None
    fields = out.split("\0")
    entries = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue  # the empty field after the last NUL
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            path = f"{fields[i]} -> {path}"  # -z gives the new path first, then the old one as its own field
            i += 1
        entries.append((code, path))
    return entries


def holds_worktree(folder: Path, roots: set[str]) -> bool:
    """Whether folder is a worktree root or has one somewhere inside it."""
    real = os.path.realpath(folder)
    return any(root == real or root.startswith(real + os.sep) for root in roots)


def is_root(folder: Path, roots: set[str]) -> bool:
    """Whether folder is exactly a worktree's root."""
    return os.path.realpath(folder) in roots


def exclude_file(worktree: Path) -> Path:
    """The repo's .git/info/exclude (shared by every worktree), as an absolute path."""
    found = git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
    return Path(found) if found else worktree / ".git" / "info" / "exclude"


def parked_branches(repo_dir: Path, skip: set[str], base: str, has_gh: bool) -> list[str]:
    """Local branches not in skip with commits not on base, each with its PR when GitHub is there: a squash-merged
    branch still has commits "not on main", and its merged PR says so."""
    rows = []
    for branch in (git(repo_dir, "for-each-ref", "--format=%(refname:short)", "refs/heads/") or "").splitlines():
        if branch in skip:
            continue
        count = git(repo_dir, "rev-list", "--count", f"{base}..{branch}") or "?"  # "?": git couldn't compare it
        if count == "0":
            continue
        pr = ""
        if has_gh:
            found, error = branch_pr(repo_dir, branch, with_title=False)
            pr = f" | PR: {found}" if found is not None else f" | PR: gh failed: {error}"
        last = (git(repo_dir, "log", "-1", "--format=%cr: %s", branch) or "")[:LAST_COMMIT_CHARS]
        rows.append(f"- {branch} | {count} commit(s) not on {base}{pr} | last: {last}")
    return rows
