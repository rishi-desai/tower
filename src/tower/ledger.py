"""ledger.py: where a repo's cards live, the one place every scan and the status-card skill agree on it.

  - the per-machine config (~/.claude/tower.conf, plain `key=value` lines written by setup.sh) and how to read it
  - which project folder in the notes vault a repo maps to: <projects_dir>/<project.<repo>= or the repo's name>
  - how a branch becomes a card file name: "/" -> "__", a detached HEAD -> detached-<short sha>

Cards live outside every repo, in <project folder>/Ledger/, so every worktree of a repo writes to the same folder,
cards never show up in a PR, and Obsidian can show them.
"""

from __future__ import annotations

import os
from pathlib import Path

from tower.gitrepo import git, repo_name


class LedgerError(Exception):
    """The ledger can't be found: no projects_dir in the config, or that folder doesn't exist."""


def conf_path() -> Path:
    """tower's per-machine config: $TOWER_CONF if set, else ~/.claude/tower.conf."""
    return Path(os.environ.get("TOWER_CONF") or Path.home() / ".claude" / "tower.conf")


def read_conf(path: Path) -> dict[str, str]:
    """The config's `key=value` lines as a dict; the last line for a key wins. It's parsed, never run, so a typo in it
    can't run as a command. Values may hold spaces (iCloud paths do). A missing file is an empty config; one that
    exists but can't be read, or isn't UTF-8, raises LedgerError, so it never passes for a machine that was never set
    up (and a stray byte, say a Latin-1 path, is a note in every report instead of a crash)."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise LedgerError(f"can't read {path} ({error.strerror or type(error).__name__})") from error
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        line_number = data.count(b"\n", 0, error.start) + 1
        raise LedgerError(f"{path} isn't UTF-8 text (line {line_number})") from error
    conf: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith("#"):
            conf[key] = value
    return conf


def conf_get(conf: dict[str, str], key: str, default: str = "") -> str:
    """key's value, or default when it's absent or empty (an empty `project.<repo>=` means the repo's name)."""
    return conf.get(key) or default


def wip_limit(conf: dict[str, str]) -> int:
    """wip_limit from the config; 3 (setup.sh's default) when absent or not a whole number."""
    value = conf.get("wip_limit", "")
    return int(value) if value.isdigit() else 3


def projects_root(conf: dict[str, str]) -> Path:
    """The vault folder holding one folder per project. Raises LedgerError, instead of guessing a fallback, when
    projects_dir is unset or missing (a typo, or an unsynced vault), so a card is never written where the hub won't look."""
    projects_dir = conf.get("projects_dir", "")
    if not projects_dir:
        raise LedgerError(f"no projects_dir in {conf_path()} (run setup.sh on this machine)")
    if not os.path.isdir(projects_dir):
        raise LedgerError(f"projects_dir does not exist: {projects_dir} (check {conf_path()})")
    return Path(projects_dir)


def project_dir(repo: str, conf: dict[str, str]) -> Path:
    """The repo's project folder in the vault: <projects_dir>/<project.<repo>= or the repo's name>. Raises LedgerError
    when projects_dir is unset or missing (see projects_root)."""
    return projects_root(conf) / conf_get(conf, f"project.{repo}", repo)


def ledger_dir(repo: str, conf: dict[str, str]) -> tuple[Path | None, str]:
    """The repo's Ledger folder, or None and why it can't be found (for reports that go on without cards)."""
    try:
        return project_dir(repo, conf) / "Ledger", ""
    except LedgerError as error:
        return None, f"{error}, so no cards were read"


def card_name(branch: str | None, sha: str, repo_dir: Path) -> str:
    """A worktree's card file name. "/" becomes "__" (a "/" would make a subfolder: feat/42-x -> feat__42-x.md); a
    detached HEAD (no branch) becomes detached-<short sha>.md, so its card still has a unique name."""
    if not branch:
        branch = "detached-" + (git(repo_dir, "rev-parse", "--short", sha) or sha[:7])
    return branch.replace("/", "__") + ".md"


def open_ledger(repo_dir: Path) -> Path:
    """The Ledger folder of the repo holding repo_dir, created if needed. Raises NotARepo outside a git repo and
    LedgerError when the ledger can't be found."""
    ledger = project_dir(repo_name(repo_dir), read_conf(conf_path())) / "Ledger"
    try:
        ledger.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise LedgerError(f"can't create {ledger} ({error.strerror or type(error).__name__})") from error
    return ledger


def card_path(worktree_dir: Path) -> Path:
    """Where the worktree holding worktree_dir keeps its card; creates the Ledger folder if needed."""
    branch = git(worktree_dir, "branch", "--show-current")  # "" on a detached HEAD
    return open_ledger(worktree_dir) / card_name(branch, "HEAD", worktree_dir)
