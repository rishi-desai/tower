"""memory.py: worker memory (#93, ADR 0008), what a worker gets back when its context restarts.

Three parts, re-read by the SessionStart hook (plugin/scripts/worker-memory.py) after compaction, /clear, or in a fresh
session on the worktree:

    kickoff         the hub's first prompt, a fixed section of the card (card.set_kickoff, card.split_kickoff)
    card            the worktree's current card, as it was last written
    decisions log   Ledger/_decisions/<card name>: one dated line per decision or discovery, only ever appended to
                    (`tower decisions add`); tower clean archives it with the card

Claude Code caps what a SessionStart hook adds to the context at 10,000 characters (above that, Claude sees a 2,000
character preview and a file path), so session_context() stays under BUDGET: the kickoff and card come whole, and the
log keeps its newest lines that fit and names the file for the rest (docs/research/precompact-hook.md).

Runs under whatever `python3` the plugin's scripts find (3.9 on a stock Mac), like the rest of the core.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from tower import card, ledger
from tower.gitrepo import git, is_main_checkout

MAX_DECISION = 300  # characters: a line, not a paragraph; every line is re-read on each restart
BUDGET = 9_500  # characters; Claude Code's cap is 10,000, measured on the whole output
# What SessionStart's source says about the restart; any other source (resume, fork) still has its transcript.
RESTARTS = {"compact": "after compaction", "clear": "after /clear", "startup": "in a new session"}
LEFT_OUT_ROOM = 300  # characters kept free for the "older decisions left out" line, whose path can be long


class DecisionError(Exception):
    """A decision the log can't hold, or a worktree that has no log (the main checkout); nothing was written."""


def add_decision(worktree: Path, text: str) -> Path:
    """Append one dated line to the decisions log of the worktree holding worktree; the log's path. Whitespace (new
    lines too) collapses to single spaces. Earlier lines are never rewritten: the file is opened for appending only,
    and the line goes in one write. Raises DecisionError, NotARepo, LedgerError or OSError."""
    decision = " ".join(text.split())
    if not decision:
        raise DecisionError("a decision is empty: say what you chose over what, and why, or what you found out")
    if len(decision) > MAX_DECISION:
        raise DecisionError(
            f"a decision is one line of at most {MAX_DECISION} characters; this one has {len(decision)}"
        )
    if is_main_checkout(worktree):
        raise DecisionError("the main checkout has no decisions log: tower starts every worker in its own worktree")
    path = ledger.decisions_path(worktree)
    path.parent.mkdir(exist_ok=True)
    line = f"- {time.strftime(card.TIME_FORMAT)} {decision}\n"
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            line = "\n" + line  # a hand edit left the last line open; never join this one onto it
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def session_context(worktree: Path, source: str) -> str:
    """What a session starting in worktree gets back (SessionStart's source: compact, clear or startup): its kickoff,
    its card and its decisions log, under BUDGET characters; "" for any other source (a resumed or forked session still
    has its transcript), in the main checkout, or when the worktree has neither card nor log. The log keeps its newest
    lines that fit, and says how many older ones it left out and where they are. Raises NotARepo, LedgerError or
    OSError."""
    if source not in RESTARTS or is_main_checkout(worktree):
        return ""
    card_file = ledger.existing_card_path(worktree)
    if card_file is None:
        return ""
    log_file = ledger.decisions_log(card_file)
    born = worktree_birth(worktree)
    card_text, card_problem = read_since(card_file, born)
    log_text, log_problem = read_since(log_file, born)
    if card_text is None and log_text is None and not card_problem and not log_problem:
        return ""
    front, body = card.split_card(card_text or "")
    rest, kickoff = card.split_kickoff(body)
    branch = card.read_frontmatter(front).get("branch") or card_file.stem
    header = (
        f"tower worker memory for this worktree ({branch}), given back {RESTARTS[source]}: the hub's kickoff word for"
        " word, your card as you last wrote it, and your decisions log. Follow the kickoff; keep adding to the log"
        " (status-card skill)."
    )
    kickoff_part = f"# Kickoff\n{kickoff or '(none recorded: the hub did not start this worktree)'}"
    card_part = f"# Card ({card_file})\n" + (
        "\n".join(front) + "\n\n" + rest.strip("\n") if card_text is not None else card_problem or "(none yet)"
    )
    log_heading = f"# Decisions log ({log_file}, oldest first)"
    card_room = BUDGET - len(f"{header}\n\n{kickoff_part}\n\n\n\n{log_heading}") - 2 * LEFT_OUT_ROOM
    if len(card_part) > card_room:  # a card far past its ~30 lines; the kickoff, before it, always stays whole
        cut = card_part[: max(card_room, 0)].rsplit("\n", 1)[0]
        card_part = f"{cut}\n(the card is cut here to stay under Claude Code's limit: read {card_file})"
    room = BUDGET - len(f"{header}\n\n{kickoff_part}\n\n{card_part}\n\n{log_heading}") - LEFT_OUT_ROOM
    decisions = [line for line in (log_text or "").splitlines() if line.strip()]
    kept = newest_that_fit(decisions, room)
    log_part = log_heading + "\n" + ("\n".join(kept) if kept else log_problem or "(none yet)")
    if len(kept) < len(decisions):
        log_part += (
            f"\n({len(decisions) - len(kept)} older decisions left out to stay under Claude Code's limit:"
            f" read {log_file})"
        )
    return f"{header}\n\n{kickoff_part}\n\n{card_part}\n\n{log_part}\n"


def newest_that_fit(lines: list[str], room: int) -> list[str]:
    """The newest of lines (oldest first) whose total length, one newline each, fits in room, still oldest first."""
    kept: list[str] = []
    for line in reversed(lines):
        if len(line) + 1 > room:
            break
        kept.append(line)
        room -= len(line) + 1
    return kept[::-1]


def read_since(path: Path, born: float | None) -> tuple[str | None, str]:
    """(the file's text, "") when it was written since born (bytes that aren't UTF-8 replaced); (None, "") when there's
    no file or it's older than the worktree, so from earlier work on the same branch name; (None, why) when it can't be
    read, so the rest of the memory still comes back."""
    try:
        if born is not None and path.stat().st_mtime < born:
            return None, ""
        return path.read_text(encoding="utf-8", errors="replace"), ""
    except FileNotFoundError:
        return None, ""
    except OSError as error:
        return None, f"(couldn't read it: {error.strerror or type(error).__name__})"


def worktree_birth(worktree: Path) -> float | None:
    """When the worktree was made: its own git folder's creation time (.git/worktrees/<name>), or None where the
    filesystem doesn't keep one (st_birthtime is macOS and BSD) or git can't say."""
    git_dir = git(worktree, "rev-parse", "--absolute-git-dir")
    try:
        return getattr(os.stat(git_dir), "st_birthtime", None) if git_dir else None  # getattr: Linux has none
    except OSError:
        return None
