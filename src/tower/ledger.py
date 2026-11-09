"""ledger.py: where a repo's cards live, the one place every scan and the status-card skill agree on it.

  - the per-machine config (~/.claude/tower.conf, plain `key=value` lines written by setup.sh) and how to read it
  - which project folder in the notes vault a repo maps to: <projects_dir>/<project.<repo>= or the repo's name>
  - how a branch becomes a card file name: "/" -> "__", a detached HEAD -> detached-<short sha>; and how a card stays
    with its worktree when the worktree moves to another branch (a stack worktree's worker does): the worktree's own
    git folder records its card's name (remember_card, followed_card)
  - where a worktree's decisions log lives (Ledger/_decisions/, by its card's name) and how a finished worker's notes
    are archived (Ledger/_archive/)

Cards live outside every repo, in <project folder>/Ledger/, so every worktree of a repo writes to the same folder,
cards never show up in a PR, and Obsidian can show them.
"""

from __future__ import annotations

import codecs
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tower.gitrepo import NotARepo, git, repo_name, short_sha

ARCHIVE = "_archive"
CARD_POINTER = "tower-card"  # in a worktree's own git folder: the name of the card that follows it
DECISIONS = "_decisions"  # a folder, so the scans that read the Ledger's *.md as cards never see a decisions log


class LedgerError(Exception):
    """The ledger can't be found: tower.conf is unreadable (ConfUnreadable), has no projects_dir, or names a folder
    that doesn't exist."""


class ConfUnreadable(LedgerError):
    """tower.conf exists but can't be read or isn't UTF-8: broken, so worth reporting, unlike a machine never set up."""


def conf_path() -> Path:
    """tower's per-machine config: $TOWER_CONF if set, else ~/.claude/tower.conf."""
    return Path(os.environ.get("TOWER_CONF") or Path.home() / ".claude" / "tower.conf")


def read_conf(path: Path) -> dict[str, str]:
    """The config's `key=value` lines as a dict; the last line for a key wins. It's parsed, never run, so a typo in it
    can't run as a command. Values may hold spaces (iCloud paths do), and a leading byte order mark (an editor's "UTF-8
    with BOM") is skipped. A missing file is an empty config; one that exists but can't be read, or isn't UTF-8 (the
    error names the line), raises ConfUnreadable, so it never passes for a machine that was never set up."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ConfUnreadable(f"can't read {path} ({error.strerror or type(error).__name__})") from error
    data = data.removeprefix(codecs.BOM_UTF8)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        # The bad byte's line as the loop below splits them (\r alone ends one too); "x" stands in for that byte.
        line_number = len((data[: error.start].decode("utf-8") + "x").splitlines())
        raise ConfUnreadable(f"{path} isn't UTF-8 text (line {line_number})") from error
    conf: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith("#"):
            conf[key] = value
    return conf


def conf_unused(error: ConfUnreadable) -> str:
    """The note for a tower.conf that couldn't be read, worded the same wherever it's made, so a report that gets it
    twice (from the policy's problems, or from each repo under `tower state --all`) can tell it's one note."""
    return f"{error}, so its settings weren't used"


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
        branch = "detached-" + short_sha(repo_dir, sha)
    return branch.replace("/", "__") + ".md"


def admin_dir(worktree_root: Path) -> Path | None:
    """The worktree's own git folder, read from its `.git` (a linked worktree's `.git` is a file, "gitdir:
    <repo>/.git/worktrees/<name>"; the main checkout's is the folder itself), with no git call; None when there's no
    `.git` to read. Raises LedgerError when it exists but can't be read."""
    dot_git = worktree_root / ".git"
    try:
        if dot_git.is_dir():
            return dot_git
        line = dot_git.read_text(encoding="utf-8", errors="replace").strip()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LedgerError(f"can't read {dot_git} ({error.strerror or type(error).__name__})") from error
    if not line.startswith("gitdir:"):
        return None
    folder = Path(line[len("gitdir:") :].strip())
    return folder if folder.is_absolute() else worktree_root / folder


def followed_card(git_dir: Path | None) -> str | None:
    """The card file name a worktree's git folder records (`<git dir>/tower-card`, written by remember_card), or None
    when it records none. Raises LedgerError when the record exists but can't be read or isn't a card file name:
    falling back to the branch's card would leave the worktree's real card, kickoff and decisions log behind."""
    if git_dir is None:
        return None
    pointer = git_dir / CARD_POINTER
    try:
        name = pointer.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        why = error.strerror if isinstance(error, OSError) else "it isn't UTF-8"
        raise LedgerError(f"can't read {pointer} ({why or type(error).__name__})") from error
    if not name.endswith(".md") or "/" in name or name.startswith("."):
        raise LedgerError(f"{pointer} doesn't name a card file: {name[:60]!r}")
    return name


def card_file(worktree_root: Path, branch: str | None, sha: str) -> str:
    """The card file name of the worktree at worktree_root, on branch at sha: the one its git folder records
    (followed_card), else the one named after its branch (card_name). Raises LedgerError when the record exists but
    can't be read."""
    return followed_card(admin_dir(worktree_root)) or card_name(branch, sha, worktree_root)


def remember_card(git_dir: Path, name: str) -> None:
    """Record name as the card of the worktree whose git folder is git_dir, so the card stays its card when the worktree
    moves to another branch (a stack worktree's worker does). Git removes the folder with the worktree, so a later
    worktree at the same path starts without it. Raises OSError when it can't be written."""
    try:
        if followed_card(git_dir) == name:
            return
    except LedgerError:
        pass  # an unreadable record is replaced
    fd, tmp = tempfile.mkstemp(
        dir=git_dir, prefix=f".{CARD_POINTER}-", suffix=".tmp"
    )  # unique: two writers can't clash
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(name + "\n")
        os.replace(tmp, git_dir / CARD_POINTER)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record_card(worktree_dir: Path, card: Path) -> str:
    """Record card as the card of the worktree holding worktree_dir (remember_card), unless that's the main checkout
    (the hub's, which has no card that moves); "" or a warning saying why it couldn't be recorded."""
    git_dir = git(worktree_dir, "rev-parse", "--absolute-git-dir")
    common = git(worktree_dir, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not git_dir or git_dir == common:  # no git folder, or the main checkout's own
        return ""
    try:
        remember_card(Path(git_dir), card.name)
    except OSError as error:
        why = error.strerror or type(error).__name__
        return f"couldn't record the card in the worktree's git folder ({why}), so it won't follow the worktree to another branch"
    return ""


def card_in(ledger_folder: Path, worktree_dir: Path) -> Path:
    """The card of the worktree holding worktree_dir in ledger_folder (card_file). Raises NotARepo outside a git repo
    and LedgerError when the worktree's record of its card can't be read."""
    top = git(worktree_dir, "rev-parse", "--show-toplevel")
    if top is None:
        raise NotARepo(f"not a git repo: {worktree_dir}")
    return ledger_folder / card_file(Path(top), git(worktree_dir, "branch", "--show-current"), "HEAD")


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
    """Where the worktree holding worktree_dir keeps its card (card_in); creates the Ledger folder if needed."""
    return card_in(open_ledger(worktree_dir), worktree_dir)


def existing_card_path(worktree_dir: Path) -> Path | None:
    """Where the worktree holding worktree_dir keeps its card, like card_path, but None when the Ledger folder doesn't
    exist, which it never creates: for readers that run in every session (any repo, not only tower's)."""
    folder = project_dir(repo_name(worktree_dir), read_conf(conf_path())) / "Ledger"
    if not folder.is_dir():
        return None
    return card_in(folder, worktree_dir)


def decisions_path(worktree_dir: Path) -> Path:
    """Where the worktree holding worktree_dir keeps its decisions log (decisions_log of its card). Creates the Ledger
    folder if needed, not the _decisions folder."""
    return decisions_log(card_path(worktree_dir))


def decisions_log(card: Path) -> Path:
    """The decisions log that goes with card: Ledger/_decisions/<the card's name>."""
    return card.parent / DECISIONS / card.name


def archive_decisions(card: Path) -> Path | None:
    """Move card's decisions log into the Ledger's _archive folder as <card stem>.decisions.md, beside where the card
    is archived, so a later worktree on the same branch name starts with an empty log. Where it went, or None when
    there is no log. Raises OSError when it can't be moved."""
    log = decisions_log(card)
    if not log.exists():
        return None
    return archive(log, card.parent, f"{card.stem}.decisions.md")


def archive(note: Path, ledger_folder: Path, name: str | None = None) -> Path:
    """Move note into the Ledger's _archive folder as name (default: its own), under a timestamped name when one by
    that name is already there (a reused branch); where it went. Raises OSError when it can't be moved."""
    folder = ledger_folder / ARCHIVE
    target = folder / (name or note.name)
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = folder / f"{target.stem}.{stamp}{target.suffix}"
    folder.mkdir(exist_ok=True)
    note.rename(target)
    return target
