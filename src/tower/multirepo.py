"""multirepo.py: `tower state --all`, the effective state of every repo this machine knows.

A known repo is one with events in the event log (each event names its repo's main checkout), the repo it runs in
(the hub's, even before the log has any event from it), or a Ledger folder in the vault (<projects_dir>/<project>/
Ledger). Each repo with a checkout is gathered by state.gather(), exactly as
`tower state --repo` would, so a repo's statuses never differ between the two. A Ledger folder names a project, not a
checkout: one that no such repo maps to (no session has run there since the log began) is listed as cards only,
with the statuses its cards claim and no worktree facts; an empty one has nothing to report and is left out.

Work and personal stay apart because the event log and tower.conf (and so the vault) are per machine: this only ever
sees the repos of the machine it runs on.

Like state.py, it runs under whatever `python3` the plugin finds, so it stays 3.9-compatible.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tower import events, ledger, sessions, state
from tower.gitrepo import NotARepo, list_worktrees

# The statuses the ALL REPOS summary counts per repo, most urgent first; repos are listed in this order of urgency.
SUMMARY_STATUSES = ("needs-you", "blocked", "running", "waiting-ci", "done")


@dataclass(frozen=True)
class CardsOnly:
    """A project with a Ledger folder but no checkout tower knows: its cards and the statuses they claim."""

    project: str
    ledger: str
    cards: list[state.OrphanCard]


@dataclass(frozen=True)
class AllReport:
    """Every known repo's report, most urgent first, the cards-only projects, and what couldn't be read."""

    repos: list[state.Report]
    cards_only: list[CardsOnly]
    notes: list[str] = field(default_factory=list)

    @property
    def slots(self) -> int:
        """Execution slots in use across every repo."""
        return sum(r.slots for r in self.repos)


def gather_all(here: Path | None = None, with_pr: bool = False, now: float | None = None) -> AllReport:
    """Find every known repo and gather each one's effective state; here is a folder whose repo counts as known (the
    caller's working folder), ignored outside a git repo. Never raises for one bad repo: a folder that's gone or isn't
    a git repo any more is skipped and noted."""
    notes: list[str] = []
    try:
        conf: dict[str, str] | None = ledger.read_conf(ledger.conf_path())
    except ledger.LedgerError as error:
        notes.append(f"{error}, so Ledger folders weren't looked for")
        conf = None

    agents = sessions.read_agents()  # one `claude agents --json` for every repo, not one per repo
    reports: list[state.Report] = []
    gathered: set[str] = set()
    gone: list[str] = []
    for path in known_checkouts(here, notes):
        if not os.path.isdir(path):
            gone.append(path)
            continue
        try:
            report = state.gather(Path(path), with_pr=with_pr, now=now, agents_read=agents)
        except NotARepo:
            notes.append(f"skipped {path}: it's in the event log but isn't a git repo any more")
            continue
        # Events name the main checkout, but one naming a worktree would lead to the same repo: list it once.
        if os.path.realpath(report.main_checkout) not in gathered:
            gathered.add(os.path.realpath(report.main_checkout))
            reports.append(report)
    if gone:
        notes.append(
            f"skipped {state.plural(len(gone), 'repo')} from the event log whose folder is gone: {', '.join(gone)}"
        )

    claimed = {os.path.realpath(r.ledger) for r in reports if r.ledger is not None}
    found = [] if conf is None else ledger_projects(conf, notes)
    cards_only = [c for c in found if c.cards and os.path.realpath(c.ledger) not in claimed]
    reports.sort(key=urgency)
    cards_only.sort(key=urgency)
    return AllReport(repos=reports, cards_only=cards_only, notes=notes)


def known_checkouts(here: Path | None, notes: list[str]) -> list[str]:
    """The main checkout of here's repo (if any), then every logged repo, each once: two paths to the same folder (a
    symlink, macOS's /private/var) count as one."""
    paths = logged_repos(notes)
    if here is not None:
        try:
            paths.insert(0, list_worktrees(here)[0].path)
        except NotARepo:
            pass  # run outside a repo: only the log and the vault say what's known
    seen: set[str] = set()
    unique = []
    for path in paths:
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            unique.append(path)
    return unique


def logged_repos(notes: list[str]) -> list[str]:
    """Every distinct repo path in the event log (all months), sorted."""
    try:
        log, _ = events.read_all(events.default_dir())
    except OSError as error:
        notes.append(
            f"couldn't read the event log ({error.strerror or type(error).__name__}), so no repos came from it"
        )
        return []
    return sorted({e.repo for e in log if e.repo})


def ledger_projects(conf: dict[str, str], notes: list[str]) -> list[CardsOnly]:
    """Every project folder in the vault that has a Ledger folder, with its cards; none, and a note, when projects_dir
    isn't set up or can't be listed."""
    projects_dir = conf.get("projects_dir", "")
    if not projects_dir or not os.path.isdir(projects_dir):
        notes.append(
            f"no projects_dir in {ledger.conf_path()} (or it doesn't exist), so Ledger folders weren't looked for"
        )
        return []
    found = []
    try:
        projects = sorted(p for p in Path(projects_dir).iterdir() if (p / "Ledger").is_dir())
    except OSError as error:
        notes.append(
            f"couldn't list {projects_dir} ({error.strerror or type(error).__name__}), so Ledger folders weren't looked for"
        )
        return []
    for project in projects:
        folder = project / "Ledger"
        found.append(CardsOnly(project.name, str(folder), state.orphan_cards(folder, set(), notes)))
    return found


def urgency(entry: state.Report | CardsOnly) -> tuple[object, ...]:
    """Sort key shared by repos and cards-only projects, so they interleave: the most needs-you first, then blocked,
    running, and so on; then by name and path."""
    counts = status_counts(entry)
    if isinstance(entry, CardsOnly):
        return (*(-counts[s] for s in SUMMARY_STATUSES), entry.project, entry.ledger)
    return (*(-counts[s] for s in SUMMARY_STATUSES), entry.repo, entry.main_checkout)


def status_counts(entry: state.Report | CardsOnly) -> dict[str, int]:
    """How many of a repo's worktrees are in each effective status, or of a cards-only project's cards claim each."""
    statuses = (
        [o.status for o in entry.cards] if isinstance(entry, CardsOnly) else [v.status for _, v in entry.worktrees]
    )
    counts = dict.fromkeys(state.STATUSES, 0)
    for status in statuses:
        if status in counts:
            counts[status] += 1
    return counts


# ---------- output ----------


def to_json(report: AllReport) -> dict[str, object]:
    """The report as `tower state --all --json` prints it: each repo exactly as `tower state --json` prints it."""
    return {
        "slots": report.slots,
        "notes": report.notes,
        "repos": [state.to_json(r) for r in report.repos],
        "cards_only": [
            {"project": c.project, "ledger": c.ledger, "cards": [asdict(card) for card in c.cards]}
            for c in report.cards_only
        ],
    }


def render_text(report: AllReport) -> str:
    """The report as `tower state --all` prints it: an ALL REPOS summary, then each repo's own `tower state` sections
    under a `== REPO: <name> ==` line, or a cards-only project's cards under `== CARDS ONLY: <name> ==`, most urgent
    first (a cards-only project ranks by what its cards claim)."""
    entries: list[state.Report | CardsOnly] = sorted([*report.repos, *report.cards_only], key=urgency)
    out = ["== ALL REPOS (most urgent first) =="]
    out += [summary_row(entry) for entry in entries]
    if not entries:
        out.append("(none: no repo in the event log and no Ledger folder with cards in the vault)")
    out.append(f"WIP across repos: {report.slots} (the limit counts per repo)")
    out.extend(f"NOTE: {note}" for note in report.notes)
    for entry in entries:
        out += ["", *entry_section(entry)]
    return "\n".join(out) + "\n"


def summary_row(entry: state.Report | CardsOnly) -> str:
    """One ALL REPOS row: how many worktrees (or cards, for a cards-only project) are in each status, and where."""
    counts = status_counts(entry)
    if isinstance(entry, CardsOnly):
        claims = [f"{s}: {counts[s]}" for s in SUMMARY_STATUSES if counts[s]]
        listed = f"cards only: {state.plural(len(entry.cards), 'card')}, no checkout in the event log"
        return " | ".join([f"- {state.cell(entry.project)}", listed, *claims, entry.ledger])
    limit = " AT LIMIT" if entry.slots >= entry.wip_limit else ""
    fields = [f"- {state.cell(entry.repo)}", *(f"{s}: {counts[s]}" for s in SUMMARY_STATUSES)]
    fields += [f"housekeeping: {len(state.cleanup(entry))}", f"WIP: {entry.slots} of {entry.wip_limit}{limit}"]
    return " | ".join([*fields, entry.main_checkout])


def entry_section(entry: state.Report | CardsOnly) -> list[str]:
    """A repo's own `tower state` sections, or a cards-only project's cards, under its heading line."""
    if isinstance(entry, CardsOnly):
        rows = [f"- {state.cell(o.name)} | card: {state.cell(o.status) or '?'} | {o.path}" for o in entry.cards]
        return [
            f"== CARDS ONLY: {state.cell(entry.project)} | {entry.ledger} ==",
            "(no checkout of this project is in the event log: start a session there, or run tower state --repo)",
            *rows,
        ]
    return [f"== REPO: {state.cell(entry.repo)} | {entry.main_checkout} ==", state.render_text(entry).rstrip("\n")]
