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
import re
from pathlib import Path

from tower import ledger, sessions, state
from tower.github import try_gh
from tower.gitrepo import GitWorktree, git, list_worktrees, repo_name

SHOWN_CHANGES = 8  # uncommitted entries listed under a worktree; the rest are counted
LAST_COMMIT_CHARS = 70


def adopt_scan(repo_dir: Path) -> str:
    """The whole scan as text. Raises NotARepo outside a git repo and LedgerError when the ledger can't be found."""
    conf = ledger.read_conf(ledger.conf_path())
    name = repo_name(repo_dir)
    folder = ledger.project_dir(name, conf) / "Ledger"
    folder.mkdir(parents=True, exist_ok=True)
    worktrees = list_worktrees(repo_dir)
    default = default_branch(repo_dir)
    # Compared with origin's copy when there is one, so "ahead" means "not yet on the remote default".
    base = f"origin/{default}" if has_ref(repo_dir, f"refs/remotes/origin/{default}") else default
    has_gh = try_gh(repo_dir, "auth", "status")[0] is not None and try_gh(repo_dir, "repo", "view")[0] is not None

    lines = [
        "== CONFIG ==",
        (
            f"repo: {name} | ledger: {folder} | default branch: {default} (compared with {base})"
            f" | WIP limit: {ledger.wip_limit(conf)} | GitHub: {'yes' if has_gh else 'no'}"
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


def default_branch(repo_dir: Path) -> str:
    """What origin/HEAD points at, else main if there's a local main, else master."""
    origin_head = git(repo_dir, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if origin_head:
        return origin_head.removeprefix("origin/")
    return "main" if has_ref(repo_dir, "refs/heads/main") else "master"


def has_ref(repo_dir: Path, ref: str) -> bool:
    """Whether the exact ref (refs/heads/main, refs/remotes/origin/main) exists."""
    return git(repo_dir, "show-ref", "--verify", "--quiet", ref) is not None


def worktree_rows(
    repo_dir: Path, worktree: GitWorktree, is_main: bool, folder: Path, base: str, roots: set[str], has_gh: bool
) -> list[str]:
    """One worktree's row, then its first uncommitted entries, the worktree-folder notes, and its PR."""
    path = Path(worktree.path)
    card_file = folder / ledger.card_name(worktree.branch, worktree.sha, repo_dir)
    try:
        card = state.read_card(card_file, 0)
        card_state = f"card: {card.status}" if card else "no card"
    except OSError:
        card_state = "card: (unreadable)"
    changes, worktree_folders = uncommitted(path, roots)
    counts = (git(path, "rev-list", "--left-right", "--count", f"{base}...HEAD") or "").split()
    behind, ahead = counts if len(counts) == 2 else ("?", "?")
    last = (git(path, "log", "-1", "--format=%cr: %s") or "")[:LAST_COMMIT_CHARS]
    ticket = re.search(r"[0-9]+", worktree.branch or "")
    role = "MAIN CHECKOUT" if is_main else "worktree"
    rows = [
        (
            f"- [{role}] {worktree.label} | {card_state} | uncommitted: {len(changes)} | ahead {ahead} / behind {behind}"
            f" of {base} | last: {last} | ticket guess: {'#' + ticket.group() if ticket else 'none'} | {worktree.path}"
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
        template = "{{range .}}#{{.number}} {{.state}} {{.title}}{{end}}"
        pr, error = try_gh(repo_dir, "pr", "list", "--head", worktree.branch, "--state", "all", "--limit", "1",
                           "--json", "number,state,title", "--template", template)  # fmt: skip
        rows.append(f"    PR: {pr.strip() or 'none'}" if pr is not None else f"    PR: (gh failed: {error})")
    return rows


def uncommitted(worktree: Path, roots: set[str]) -> tuple[list[str], list[str]]:
    """The worktree's uncommitted entries as `git status --porcelain` lines (paths unquoted), and the untracked
    folders left out because they only hold worktrees (roots: every worktree's real path).

    An untracked folder that holds worktrees and other files is listed file by file instead, without the worktrees.
    """
    changes: list[str] = []
    worktree_folders: list[str] = []
    for code, path in status_entries(worktree):
        if code == "??" and path.endswith("/") and holds_worktree(worktree / path, roots):
            inside = status_entries(worktree, "--untracked-files=all", "--", path)
            others = [(c, p) for c, p in inside if not (p.endswith("/") and is_root(worktree / p, roots))]
            if others:
                changes += [f"{c} {p}" for c, p in others]
            else:
                worktree_folders.append(path)
            continue
        changes.append(f"{code} {path}")
    return changes, worktree_folders


def status_entries(worktree: Path, *args: str) -> list[tuple[str, str]]:
    """`git status --porcelain -z` as (XY code, path) pairs; a rename's path is "old -> new". Empty if git fails.
    Pathspecs in args are literal, so a folder named `[a]*` means only itself."""
    out = git(worktree, "--literal-pathspecs", "status", "--porcelain", "-z", *args)
    fields = (out or "").split("\0")
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
        count = git(repo_dir, "rev-list", "--count", f"{base}..{branch}") or "0"
        if not count.isdigit() or int(count) == 0:
            continue
        pr = ""
        if has_gh:
            template = "{{range .}}#{{.number}} {{.state}}{{end}}"
            found, error = try_gh(repo_dir, "pr", "list", "--head", branch, "--state", "all", "--limit", "1",
                                  "--json", "number,state", "--template", template)  # fmt: skip
            pr = f" | PR: {found.strip() or 'none'}" if found is not None else f" | PR: gh failed: {error}"
        last = (git(repo_dir, "log", "-1", "--format=%cr: %s", branch) or "")[:LAST_COMMIT_CHARS]
        rows.append(f"- {branch} | {count} commit(s) not on {base}{pr} | last: {last}")
    return rows
