"""card.py: a worktree's status card, rendered by the core from facts plus the agent's prose.

The agent sends only the narrative sections (`tower card write`, on stdin):

    ## Done             required
    ## Why it matters   required
    ## Needs you        required ("Nothing." when nothing)
    ## Verify           required ("Verify it yourself" is read as Verify)
    ## Words used       optional
    ## Files touched    optional (a main-checkout session lists which uncommitted files are its own here)

and the core writes the frontmatter, so no model ever writes YAML (an unquoted `ticket: #42` was a YAML comment):

    status    from facts: done when this branch's PR merged, waiting-ci while it's open, else running. The agent may
              ask for needs-you or blocked, with a reason; for running while it fixes an open PR; or for done when its
              PR is open and only waits on the user's merge. No PR, no done; waiting-ci only comes from the PR
    ticket    --ticket, else the last card's, else guessed from the branch (feat-42-x, fix/42-x), else none
    updated   now, local time, to the minute (the pane counts the card's age from it)
    branch    git's; none on a detached HEAD
    pr        this branch's PR from GitHub (gh); unknown when gh can't be asked, and then never done or waiting-ci
    base      the branch the PR merges into, else the repo's base branch (state.base_ref)
    reason    only on needs-you and blocked: why the agent asked for it

A merged PR counts only when its head is the worktree's HEAD (or contains it): a branch name reused after an old PR
merged is new work. `tower state` reads the card back like any other (state.read_card), and the card-resume.py hook
re-renders a needs-you card as running once the user answers (resume()).

Runs under whatever `python3` the plugin's scripts find (3.9 on a stock Mac), like the rest of the core.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from tower import ledger, state
from tower.github import try_gh
from tower.gitrepo import git, list_worktrees

SECTIONS = ("Done", "Why it matters", "Needs you", "Verify", "Words used", "Files touched")
REQUIRED = ("Done", "Why it matters", "Needs you", "Verify")
ALIASES = {"verify it yourself": "Verify"}
REQUESTS = ("running", "needs-you", "blocked", "done")
NEEDS_REASON = ("needs-you", "blocked")
HEADING = re.compile(r"^##\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
TICKET = re.compile(r"#?(\d+)")
# A ticket guess needs a known type before the number (feat-42-x, fix/42-x, or just 42-x), so release-2026-11 isn't #2026.
BRANCH_TICKET = re.compile(
    r"(?:^|/)(?:(?:feat|fix|chore|docs|refactor|test|perf|ci|build|spike|bug|hotfix)[-/])?(\d+)(?:-|$)"
)
TIME_FORMAT = "%Y-%m-%dT%H:%M"


class CardError(Exception):
    """The narrative or a flag can't be rendered into a card; nothing was written."""


class CardExists(Exception):
    """--if-absent was given and the worktree already has a card."""


@dataclass(frozen=True)
class Frontmatter:
    """The card's properties, in the order they're written. reason is "" unless status is needs-you or blocked."""

    status: str
    ticket: str
    updated: str
    branch: str
    pr: str
    base: str
    reason: str = ""


@dataclass(frozen=True)
class BranchPr:
    """This branch's PR as GitHub reports it. state is OPEN, MERGED or CLOSED; head is its head commit."""

    number: int
    state: str
    base: str
    head: str


@dataclass(frozen=True)
class Written:
    """What write_card() did: the card's path, its status, why, and warnings for the agent."""

    path: Path
    status: str
    why: str
    warnings: list[str]


# ---------- the narrative ----------


def parse_sections(text: str) -> dict[str, str]:
    """The narrative's sections by canonical name, each body without surrounding blank lines. Raises CardError for
    frontmatter, text before the first heading, an unknown or repeated section, or a required one missing or empty."""
    if text.lstrip().startswith("---"):
        raise CardError("the core writes the frontmatter: send only the sections (## Done, ## Why it matters, ...)")
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    fenced = False  # inside a ``` block, a "## " line is code (a shell comment, a snippet), not a section
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        heading = None if fenced else HEADING.match(line)
        if heading:
            name = canonical(heading.group(1))
            if name in found:
                raise CardError(f"section {name} appears twice")
            current = found[name] = []
        elif current is not None:
            current.append(line)
        elif line.strip():
            raise CardError(f"text before the first ## heading: {line.strip()[:60]!r}")
    sections = {name: "\n".join(lines).strip("\n") for name, lines in found.items()}
    for name in REQUIRED:
        if name not in sections:
            raise CardError(f"missing section: {name}")
    for name, content in sections.items():
        if not content.strip():
            raise CardError(f"section {name} is empty")
    return sections


def canonical(heading: str) -> str:
    """A section heading's canonical name; CardError if it isn't a card section."""
    for name in SECTIONS:
        if heading.lower() == name.lower():
            return name
    if heading.lower() in ALIASES:
        return ALIASES[heading.lower()]
    raise CardError(f"unknown section {heading!r}: a card has {', '.join(SECTIONS)}")


def render_sections(sections: dict[str, str]) -> str:
    """The card's body: each section under its heading, in SECTIONS order."""
    return "\n\n".join(f"## {name}\n{sections[name]}" for name in SECTIONS if name in sections) + "\n"


# ---------- the frontmatter ----------


def render(front: Frontmatter, body: str) -> str:
    """The whole card. Free-text values are JSON strings, which YAML reads as double-quoted scalars, so no value can
    become a comment, a number or a second line; status and updated stay bare so Obsidian types them as before."""
    lines = [
        "---",
        f"status: {front.status}",
        f"ticket: {quote_or_none(front.ticket)}",
        f"updated: {front.updated}",
        f"branch: {quote_or_none(front.branch)}",
        f"pr: {front.pr if front.pr in ('none', 'unknown') else quote(front.pr)}",
        f"base: {quote_or_none(front.base)}",
    ]
    if front.reason:
        lines.append(f"reason: {quote(front.reason)}")
    return "\n".join([*lines, "---", ""]) + "\n" + body.lstrip("\n")


def quote(value: str) -> str:
    """value as a one-line YAML double-quoted string."""
    return json.dumps(value, ensure_ascii=False)


def quote_or_none(value: str) -> str:
    """none bare (Obsidian shows it as before), anything else quoted."""
    return value if value == "none" else quote(value)


def normalize_ticket(text: str) -> str:
    """ "42" or "#42" -> "#42"; "none" -> "none". CardError otherwise."""
    if text.strip().lower() == "none":
        return "none"
    match = TICKET.fullmatch(text.strip())
    if match is None:
        raise CardError(f"a ticket is a number like 42 or #42, or none; got {text!r}")
    return f"#{match.group(1)}"


def guess_ticket(branch: str) -> str:
    """The ticket a branch name points at (rishi/feat-42-x, fix/42-x, 42-x -> #42), or none."""
    match = BRANCH_TICKET.search(branch)
    return f"#{match.group(1)}" if match else "none"


def choose_status(requested: str | None, pr: BranchPr | None, pr_known: bool) -> tuple[str, str, list[str]]:
    """(status, why, warnings) from the agent's request and this branch's PR. needs-you and blocked are honoured as
    asked; a merged PR is done whatever else is asked; an open PR is waiting-ci unless the agent says it's running, or
    done (ready for the user to merge). CardError when done is asked for without an open or merged PR."""
    if requested in NEEDS_REASON:
        return str(requested), "you asked for it", []
    if pr is not None and pr.state == "MERGED":
        warnings = [f"PR #{pr.number} is merged, so the card says done, not running"] if requested == "running" else []
        return "done", f"PR #{pr.number} is merged", warnings
    if pr is not None and pr.state == "OPEN":
        if requested in ("running", "done"):
            return str(requested), f"you asked for it; PR #{pr.number} is open", []
        return "waiting-ci", f"PR #{pr.number} is open", []
    if requested == "done":
        has = "GitHub couldn't be asked about one" if not pr_known else "this branch has no open or merged PR"
        raise CardError(f"done needs a PR, and {has}")
    if not pr_known:
        return "running", "GitHub couldn't be asked about a PR", []
    if pr is not None:
        return "running", f"PR #{pr.number} was closed without merging", []
    return "running", "no PR yet", []


# ---------- facts ----------


def branch_pr(worktree: Path, branch: str) -> tuple[BranchPr | None, str, str]:
    """(the branch's PR, a note, gh's error): its open PR, else its newest. A merged or closed PR whose head isn't
    the worktree's HEAD (nor contains it) is earlier work on a reused branch name: it's dropped, and the note says
    so. gh's error is "" when GitHub answered."""
    out, error = try_gh(
        worktree, "pr", "list", "--head", branch, "--state", "all", "--json", "number,state,baseRefName,headRefOid"
    )
    if out is None:
        return None, "", error
    try:
        listed = json.loads(out)
        prs = [BranchPr(int(p["number"]), str(p["state"]), str(p["baseRefName"]), str(p["headRefOid"])) for p in listed]
    except (ValueError, TypeError, KeyError):
        return None, "", f"gh pr list printed something unexpected: {out[:120]!r}"
    if not prs:
        return None, "", ""
    pr = max(prs, key=lambda p: (p.state == "OPEN", p.number))
    if pr.state != "OPEN" and not contains_head(worktree, pr.head):
        return None, f"PR #{pr.number} {pr.state.lower()} earlier work on this branch name", ""
    return pr, "", ""


def contains_head(worktree: Path, commit: str) -> bool:
    """Whether commit is the worktree's HEAD or has it in its history (False if git doesn't know commit)."""
    if git(worktree, "rev-parse", "HEAD") == commit:
        return True
    return git(worktree, "merge-base", "--is-ancestor", "HEAD", commit) is not None


def repo_base(worktree: Path) -> str:
    """The repo's base branch without "origin/" (state.base_ref), or none."""
    main = list_worktrees(worktree)[0]
    base = state.base_ref(Path(main.path), main.branch)
    return base[len("origin/") :] if base and base.startswith("origin/") else base or "none"


def read_frontmatter(text: str) -> dict[str, str] | None:
    """A card's frontmatter as key -> value, quoted values unquoted; None if it has no frontmatter block."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip()] = unquote(value.strip())
    return fields


def unquote(value: str) -> str:
    """A YAML scalar as written by render() or by hand: a JSON string decoded, else surrounding quotes dropped."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except ValueError:
            return value[1:-1]
        return decoded if isinstance(decoded, str) else value[1:-1]
    return value


def card_body(text: str) -> str:
    """Everything after a card's frontmatter (the whole text if it has none)."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            return "\n".join(lines[end + 1 :])
    return text


# ---------- writing ----------


def write_card(
    worktree: Path,
    narrative: str,
    requested: str | None = None,
    reason: str = "",
    ticket: str | None = None,
    if_absent: bool = False,
) -> Written:
    """Render and write the card of the worktree holding worktree. Raises NotARepo, LedgerError, CardError (bad
    narrative or flags; nothing written) or CardExists (if_absent and there is a card already)."""
    if requested is not None and requested not in REQUESTS:
        raise CardError(f"--status can be {', '.join(REQUESTS)}; waiting-ci comes from the PR")
    reason = " ".join(reason.split())
    if requested in NEEDS_REASON and not reason:
        raise CardError("needs-you and blocked need --reason")
    if reason and requested not in NEEDS_REASON:
        raise CardError("--reason only goes with --status needs-you or blocked")
    chosen_ticket = normalize_ticket(ticket) if ticket is not None else None
    body = render_sections(parse_sections(narrative))

    path = ledger.card_path(worktree)
    if if_absent and path.exists():
        raise CardExists(f"{path} already exists, so nothing was written")
    branch = git(worktree, "branch", "--show-current") or ""
    warnings: list[str] = []
    pr, note, gh_error = (None, "", "") if not branch else branch_pr(worktree, branch)
    if gh_error:
        warnings.append(f"couldn't read this branch's PR ({gh_error}), so the card says pr: unknown")
    status, why, status_warnings = choose_status(requested, pr, pr_known=not gh_error)
    warnings += status_warnings
    if note:
        why += f"; {note}"
    front = Frontmatter(
        status=status,
        ticket=chosen_ticket or previous_ticket(path) or guess_ticket(branch),
        updated=time.strftime(TIME_FORMAT),
        branch=branch or "none",
        pr="unknown" if gh_error else f"#{pr.number}" if pr else "none",
        base=pr.base if pr else repo_base(worktree),
        reason=reason if status in NEEDS_REASON else "",
    )
    write_atomically(path, render(front, body), if_absent)
    return Written(path, status, why, warnings)


def previous_ticket(path: Path) -> str | None:
    """The ticket the worktree's current card names, if it has a card naming a valid one."""
    try:
        fields = read_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if not fields or not fields.get("ticket"):
        return None
    try:
        found = normalize_ticket(fields["ticket"])
    except CardError:
        return None
    return None if found == "none" else found


def resume(worktree: Path) -> Path | None:
    """Re-render the worktree's card as running if it says needs-you (the user just answered), keeping its body,
    ticket, pr and base, taking the branch from git, and dropping the reason. The card's path if it changed, else None. No GitHub call: this
    runs on every prompt. Raises NotARepo or LedgerError when there's no card to find."""
    path = ledger.card_path(worktree)
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return None  # no card, or one that isn't UTF-8: re-rendering it would mangle its body
    fields = read_frontmatter(text)
    if fields is None or fields.get("status") != "needs-you":
        return None
    front = Frontmatter(
        status="running",
        ticket=fields.get("ticket") or "none",
        updated=time.strftime(TIME_FORMAT),
        branch=git(worktree, "branch", "--show-current") or "none",
        pr=fields.get("pr") or "none",
        base=fields.get("base") or repo_base(worktree),
    )
    write_atomically(path, render(front, card_body(text)), if_absent=False)
    return path


def create_new(tmp: str, path: Path, text: str) -> None:
    """Give the written tmp file the name path only if path doesn't exist: a hard link, else (a synced folder that
    has no hard links) an exclusive create, which a reader can briefly see half-written."""
    try:
        os.link(tmp, path)
        return
    except FileExistsError:
        raise CardExists(f"{path} already exists, so nothing was written") from None
    except OSError:
        pass  # no hard links here
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise CardExists(f"{path} already exists, so nothing was written") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def write_atomically(path: Path, text: str, if_absent: bool) -> None:
    """Replace path's content in one step, so the hub never reads a half-written card. With if_absent, the card is
    created only if none exists (os.link fails on an existing name), so a card written meanwhile is never replaced."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".card-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if if_absent:
            create_new(tmp, path, text)
        else:
            os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
