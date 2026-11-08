"""card.py: a worktree's status card, rendered by the core from facts plus the agent's prose.

The agent sends only the narrative sections (`tower card write`, on stdin):

    ## Done             required
    ## Why it matters   required
    ## Needs you        required ("Nothing." when nothing)
    ## Verify           required ("Verify it yourself" is read as Verify)
    ## Words used       optional
    ## Files touched    optional (a main-checkout session lists which uncommitted files are its own here)

The card's last section, `## Kickoff`, is the hub's kickoff prompt word for word (worker memory, ADR 0008). Only the hub
sets it (`tower card kickoff`, set_kickoff()); write_card() refuses a Kickoff section from the agent and carries the
card's kickoff over unchanged, so a worker can't reword or drop it.

and the core writes the frontmatter, so no model ever writes YAML (an unquoted `ticket: #42` was a YAML comment):

    status    from facts: done when this branch's PR merged, waiting-ci while it's open, else running. The agent may
              ask for needs-you or blocked, with a reason; for running while it fixes an open PR; or for done when its
              PR is open and only waits on the user's merge. No PR, no done; waiting-ci only comes from the PR
    ticket    --ticket, else the last card's, else guessed from the branch (feat-42-x, fix/42-x), else none
    updated   now, local time, to the minute (the pane counts the card's age from it)
    branch    git's; none on a detached HEAD
    pr        this branch's PR from GitHub (gh); unknown when gh can't be asked, and then never done or waiting-ci
    base      the branch the PR merges into, else the branch's parent in its git-spice stack (stacks.py), else the
              repo's base branch (state.base_ref)
    reason    only on needs-you and blocked: why the agent asked for it

A merged PR counts only when its head contains the worktree's HEAD: a branch name reused after an old PR merged is new
work. PRs from forks never count. Properties the user adds in Obsidian (tags) are kept across writes. `tower state` reads the card back like any other (state.read_card), and the card-resume.py hook
re-renders a needs-you card as running once the user answers (resume()).

A card stays with its worktree, not its branch: the worktree's own git folder records the card's name
(ledger.remember_card), so a stack worktree's worker keeps its card, kickoff and decisions log when it moves to the
stack's next branch.

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

from tower import ledger, stacks, state
from tower.github import from_this_repo, try_gh
from tower.gitrepo import git, guess_ticket, is_main_checkout, list_worktrees

SECTIONS = ("Done", "Why it matters", "Needs you", "Verify", "Words used", "Files touched")
REQUIRED = ("Done", "Why it matters", "Needs you", "Verify")
ALIASES = {"verify it yourself": "Verify"}
KICKOFF = "Kickoff"
# Characters. Claude Code shows the first 2,000 of SessionStart output that passes its cap, and the kickoff comes first.
MAX_KICKOFF = 1500
REQUESTS = ("running", "needs-you", "blocked", "done")
NEEDS_REASON = ("needs-you", "blocked")
PROPERTIES = ("status", "ticket", "updated", "branch", "pr", "base", "reason")  # what the core writes
HEADING = re.compile(r"^##\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
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
        if fenced and is_kickoff_heading(line):
            canonical(KICKOFF)  # refused even in a code block: split_kickoff finds the kickoff without fences
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
    if heading.lower() == KICKOFF.lower():
        raise CardError(
            "the kickoff is fixed: the hub set it (tower card kickoff) and card write keeps it, so leave the"
            " ## Kickoff section out"
        )
    raise CardError(f"unknown section {heading!r}: a card has {', '.join(SECTIONS)}")


def render_sections(sections: dict[str, str]) -> str:
    """The card's body: each section under its heading, in SECTIONS order."""
    return "\n\n".join(f"## {name}\n{sections[name]}" for name in SECTIONS if name in sections) + "\n"


# ---------- the kickoff ----------


def normalize_kickoff(text: str) -> str:
    """The kickoff without surrounding blank space. CardError when it's empty or has a "## " line, which the card
    would read as a section of its own."""
    kickoff = text.strip()
    if not kickoff:
        raise CardError("the kickoff is empty")
    if len(kickoff) > MAX_KICKOFF:
        raise CardError(f"a kickoff is at most {MAX_KICKOFF} characters; this one has {len(kickoff)}")
    if any(HEADING.match(line) for line in kickoff.splitlines()):
        raise CardError("a kickoff can't contain a ## heading: the card would read it as a section")
    return kickoff


def split_kickoff(body: str) -> tuple[str, str | None]:
    """(the card's body without its ## Kickoff section, the kickoff's text or None when it has none). The kickoff is
    the last "## Kickoff" line to the next "## " line or the end, code blocks or not: a worker's unclosed ``` must
    never hide it, and neither the narrative (parse_sections) nor a kickoff (normalize_kickoff) can hold such a line."""
    lines = body.split("\n")
    starts = [i for i, line in enumerate(lines) if is_kickoff_heading(line)]
    if not starts:
        return body, None
    start = starts[-1]
    end = next((i for i in range(start + 1, len(lines)) if HEADING.match(lines[i])), len(lines))
    rest = "\n".join(lines[:start] + lines[end:]).rstrip("\n") + "\n"
    return rest, "\n".join(lines[start + 1 : end]).strip("\n")


def is_kickoff_heading(line: str) -> bool:
    """Whether line is the ## Kickoff heading."""
    heading = HEADING.match(line)
    return heading is not None and heading.group(1).lower() == KICKOFF.lower()


def with_kickoff(body: str, kickoff: str) -> str:
    """body with the kickoff as its last section."""
    return body.rstrip("\n") + f"\n\n## {KICKOFF}\n{kickoff}\n"


# ---------- the frontmatter ----------


def render(front: Frontmatter, body: str, extra: list[str] | None = None) -> str:
    """The whole card. Free-text values are JSON strings, which YAML reads as double-quoted scalars, so no value can
    become a comment, a number or a second line; status and updated stay bare so Obsidian types them as before. extra
    is properties the user added in Obsidian (tags, aliases), kept as written."""
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
    return "\n".join([*lines, *(extra or []), "---", ""]) + "\n" + body.lstrip("\n")


def quote(value: str) -> str:
    """value as a one-line YAML double-quoted string."""
    return json.dumps(value, ensure_ascii=False)


def quote_or_none(value: str) -> str:
    """none bare (Obsidian shows it as before), anything else quoted."""
    return value if value == "none" else quote(value)


def normalize_ticket(text: str) -> str:
    """ "42" or "#42" -> "#42"; "none" -> "none" (state.normalized_ticket, which reading a card uses too). CardError
    otherwise."""
    ticket = state.normalized_ticket(text)
    if ticket is None:
        raise CardError(f"a ticket is a number like 42 or #42, or none; got {text!r}")
    return ticket


def choose_status(
    requested: str | None, pr: BranchPr | None, pr_known: bool, stack_open: tuple[str, ...] | None = ()
) -> tuple[str, str, list[str]]:
    """(status, why, warnings) from the agent's request and this branch's PR. needs-you and blocked are honoured as
    asked; a merged PR is done whatever else is asked, unless the worktree's stack still has unmerged branches
    (stack_open), or whether it has any couldn't be checked (None): then the stack's work goes on, running unless done
    is asked for; an open PR is waiting-ci unless the
    agent says it's running, or done (ready for the user to merge). CardError when done is asked for without an open
    or merged PR."""
    if requested in NEEDS_REASON:
        return str(requested), "you asked for it", []
    if pr is not None and pr.state == "MERGED" and stack_open != ():
        unsure = "whether its stack is finished couldn't be checked"
        unmerged = f"its stack still has unmerged branches: {', '.join(stack_open)}" if stack_open else unsure
        still = f"PR #{pr.number} is merged; {unmerged}"
        return ("done" if requested == "done" else "running"), still, []
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
    """(the branch's PR, a note, gh's error): its open PR, else its newest, never one from a fork (a stranger's
    patch-1 isn't this work). A merged or closed PR whose head doesn't contain the worktree's HEAD is earlier work on a
    reused branch name: it's dropped, and the note says so. One whose head isn't in this clone (a suggestion accepted
    on GitHub, not fetched) can't be checked, so it's trusted and the note says that. gh's error is "" when GitHub
    answered."""
    fields = "number,state,baseRefName,headRefOid,isCrossRepository"
    out, error = try_gh(worktree, "pr", "list", "--head", branch, "--state", "all", "--json", fields)
    if out is None:
        return None, "", error
    try:
        prs = [
            BranchPr(int(p["number"]), str(p["state"]), str(p["baseRefName"]), str(p["headRefOid"]))
            for p in json.loads(out)
            if from_this_repo(p)
        ]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None, "", f"gh pr list printed something unexpected: {out[:120]!r}"
    if not prs:
        return None, "", ""
    pr = max(prs, key=lambda p: (p.state == "OPEN", p.number))
    if pr.state == "OPEN":
        return pr, "", ""
    if git(worktree, "cat-file", "-e", f"{pr.head}^{{commit}}") is None:
        return pr, f"its head {pr.head[:7]} isn't in this clone, so it wasn't checked against this worktree", ""
    if git(worktree, "merge-base", "--is-ancestor", "HEAD", pr.head) is None:
        return None, f"PR #{pr.number} {pr.state.lower()} earlier work on this branch name", ""
    return pr, "", ""


def pr_done(worktree: Path, branch: str) -> bool | None:
    """Whether GitHub has branch's PR merged or closed and none open (a stack branch that needs nothing more); None
    when gh couldn't be asked."""
    out, _ = try_gh(worktree, "pr", "list", "--head", branch, "--state", "all", "--json", "state,isCrossRepository")
    try:
        states = [str(p["state"]) for p in json.loads(out or "") if from_this_repo(p)]
    except (ValueError, TypeError, KeyError):
        return None
    return bool(states) and "OPEN" not in states


def repo_base(worktree: Path) -> str:
    """The repo's base branch without "origin/" (state.base_ref), or none."""
    main = list_worktrees(worktree)[0]
    base = state.base_ref(Path(main.path), main.branch)
    return base[len("origin/") :] if base and base.startswith("origin/") else base or "none"


def split_card(text: str) -> tuple[list[str], str]:
    """(the frontmatter's lines, the body after it); no lines and the whole text when there's no frontmatter block."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            return lines[1:end], "\n".join(lines[end + 1 :])
    return [], text


def read_frontmatter(lines: list[str]) -> dict[str, str]:
    """Frontmatter lines as key -> value, quoted values unquoted (the first line for a key wins, as in state.py)."""
    fields: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition(":")
        if sep and key.strip() and not line[0].isspace():
            fields.setdefault(key.strip(), unquote(value.strip()))
    return fields


def extra_properties(lines: list[str]) -> list[str]:
    """The frontmatter lines of properties the core doesn't write (tags or aliases added in Obsidian), with their
    indented or "- " continuation lines, as written."""
    kept: list[str] = []
    keep = False
    for line in lines:
        if line and not line[0].isspace() and not line.startswith("-"):
            keep = line.partition(":")[0].strip() not in PROPERTIES
        if keep:
            kept.append(line)
    return kept


def unquote(value: str) -> str:
    """A YAML scalar as written by render() or by hand: a JSON string decoded, else surrounding quotes dropped."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except ValueError:
            return value[1:-1]
        return decoded if isinstance(decoded, str) else value[1:-1]
    return value


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
    stack_open: tuple[str, ...] | None = ()
    if pr is not None and pr.state == "MERGED":
        unmerged, stack_note = stacks.open_besides(worktree, branch, lambda name: pr_done(worktree, name))
        stack_open = None if unmerged is None else tuple(unmerged)
        if stack_note:
            warnings.append(f"whether this worktree's stack is finished couldn't be checked: {stack_note}")
    status, why, status_warnings = choose_status(requested, pr, pr_known=not gh_error, stack_open=stack_open)
    warnings += status_warnings
    if note:
        why += f"; {note}"
    old_lines, old_body, warning = previous_card(path)
    if warning:
        warnings.append(warning)
    kickoff = split_kickoff(old_body)[1]
    if kickoff:
        body = with_kickoff(body, kickoff)
    front = Frontmatter(
        status=status,
        ticket=chosen_ticket or previous_ticket(old_lines) or guess_ticket(branch),
        updated=time.strftime(TIME_FORMAT),
        branch=branch or "none",
        pr="unknown" if gh_error else f"#{pr.number}" if pr else "none",
        base=pr.base if pr else (stacks.parent_of(worktree, branch) if branch else None) or repo_base(worktree),
        reason=reason if status in NEEDS_REASON else "",
    )
    write_atomically(path, render(front, body, extra_properties(old_lines)), if_absent)
    recorded = ledger.record_card(worktree, path)
    if recorded:
        warnings.append(recorded)
    return Written(path, status, why, warnings)


def previous_card(path: Path) -> tuple[list[str], str, str]:
    """The frontmatter lines and body of the card being replaced (none when there's no card), and a warning when it
    exists but can't be read, so its ticket, extra properties and kickoff are lost."""
    try:
        lines, body = split_card(path.read_text(encoding="utf-8", errors="replace"))
        return lines, body, ""
    except FileNotFoundError:
        return [], "", ""
    except OSError as error:
        why = error.strerror or type(error).__name__
        return [], "", f"couldn't read the last card ({why}), so its ticket, extra properties and kickoff weren't kept"


def previous_ticket(lines: list[str]) -> str | None:
    """The ticket (or none) the last card's frontmatter names, if it names a valid one."""
    try:
        return normalize_ticket(read_frontmatter(lines).get("ticket") or "")
    except CardError:
        return None


def set_kickoff(worktree: Path, text: str, replace: bool = False) -> tuple[Path, Path | None]:
    """Put the hub's kickoff into the card of the worktree holding worktree, as its last section, keeping everything
    else: (the card's path, where the earlier work's decisions log was archived or None). The same kickoff again changes nothing.
    replace is for an orphan card from earlier work on the same branch name: it replaces a different kickoff and
    archives that work's decisions log. Raises NotARepo, LedgerError, OSError, or CardError (an empty kickoff, the
    main checkout, no card yet, a different kickoff without replace; nothing written). An OSError from archiving the log
    comes after the kickoff was written."""
    kickoff = normalize_kickoff(text)
    if is_main_checkout(worktree):
        raise CardError("the main checkout has no kickoff: tower starts every worker in its own worktree")
    path = ledger.card_path(worktree)
    try:
        card_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CardError(f"no card yet at {path}: write the starter card first (tower card write --if-absent)") from None
    except UnicodeDecodeError:
        raise CardError(f"{path} isn't UTF-8 text, so the kickoff wasn't added") from None
    body = split_card(card_text)[1]
    rest, current = split_kickoff(body)
    if current == kickoff and not replace:
        return path, None
    if current is not None and not replace:
        raise CardError(
            f"{path} already has a different kickoff. Pass --replace only when it's an orphan card from earlier work"
            " on this branch name (that also archives the earlier work's decisions log)"
        )
    prefix = card_text[: len(card_text) - len(body)]
    write_atomically(path, prefix + with_kickoff(rest, kickoff), if_absent=False)
    ledger.record_card(worktree, path)  # the hub's starter card may not have been recorded (it existed already)
    return path, ledger.archive_decisions(path) if replace else None  # only once the new kickoff is in


def resume(worktree: Path) -> Path | None:
    """Re-render the worktree's card as running if it says needs-you (the user just answered), keeping its body,
    ticket, pr, base and any property the user added, taking the branch from git, and dropping the reason. The card's path if it changed, else None. No GitHub call: this
    runs on every prompt. Raises NotARepo or LedgerError when there's no card to find."""
    path = ledger.card_path(worktree)
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return None  # no card, or one that isn't UTF-8: re-rendering it would mangle its body
    lines, body = split_card(text)
    fields = read_frontmatter(lines)
    if fields.get("status") != "needs-you":
        return None
    # A detached HEAD (a restack stopped on a conflict) is the same work: keep the card's branch, PR and base.
    branch = git(worktree, "branch", "--show-current") or fields.get("branch") or "none"
    moved = branch != (fields.get("branch") or "none")  # the card followed its worktree to another branch
    front = Frontmatter(
        status="running",
        ticket=fields.get("ticket") or "none",
        updated=time.strftime(TIME_FORMAT),
        branch=branch,
        # The last branch's PR and base aren't this one's, and GitHub isn't asked here: the next card write fills them.
        pr="unknown" if moved else fields.get("pr") or "none",
        base=(stacks.parent_of(worktree, branch) if branch != "none" else None) or repo_base(worktree)
        if moved
        else fields.get("base") or repo_base(worktree),
    )
    write_atomically(path, render(front, body, extra_properties(lines)), if_absent=False)
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
        os.fchmod(fd, 0o644)  # mkstemp's 0600 would carry over to the card; notes are readable like any other file
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if if_absent:
            create_new(tmp, path, text)
        else:
            os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
