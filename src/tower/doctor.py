"""doctor.py: `tower doctor`, one check per thing tower depends on, each naming its fix when it isn't right.

Read-only: it runs tools' status commands and reads files, never writes (so the policy check doesn't migrate
tower.conf's old approvals the way `tower policy` does). A check fails when tower can't work as it should; it only
warns when the hub has a fallback (Orca) or the fact can't be known for sure (no hook event in the last hour, which is
also true of an hour with no Claude session; no policy file, which leaves every repo on the strictest profile).
setup.sh runs it last, and the hub runs it when a scan fails.

Like the rest of src/tower, it runs under the plugin's `python3` (3.9 on a stock Mac): standard library only.
"""

from __future__ import annotations

import json
import subprocess
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from tower import events, ledger, policy, sessions
from tower.gitrepo import COMMAND_TIMEOUT_SECONDS
from tower.state import parse_ts

OK, WARN, FAIL = "ok", "warn", "fail"
PLUGIN_ID = "tower@tower"  # setup.sh installs tower from the marketplace it adds, also named tower
INSTALL_CLAUDE = "install Claude Code (https://claude.com/claude-code) so the claude command is on PATH"
FORMAT_FIX = (
    "run claude update; if it still fails, Claude Code changed the format, so open an issue at"
    " https://github.com/rishi-desai/tower/issues with this line"
)
HOOKS_WINDOW = timedelta(hours=1)
TAIL_LINES = 50  # the newest event is among the log's last lines; more covers a few damaged ones
HOOKS_FIX = (
    "start a Claude Code session (claude) and send it a prompt, then run tower doctor again; if there is still no"
    " event, run /hooks in that session to check that tower's hooks are listed"
)
CAN_PUSH = ("WRITE", "MAINTAIN", "ADMIN")  # gh repo view's viewerPermission values that allow a push


@dataclass(frozen=True)
class Check:
    """One check's result: what it checked (name), ok/warn/fail, what it found, and how to fix it (when not ok)."""

    name: str
    status: str
    detail: str
    fix: str = ""


def run_tool(*args: str) -> tuple[subprocess.CompletedProcess[str] | None, str]:
    """Run one tool's status command; its result, or None and why it didn't run (not on PATH, timed out, ...)."""
    try:
        return subprocess.run(
            list(args), check=False, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS
        ), ""
    except FileNotFoundError:
        return None, f"{args[0]} not on PATH"
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"{' '.join(args)} didn't run ({type(error).__name__})"


def last_line(proc: subprocess.CompletedProcess[str]) -> str:
    """The last line a failed command printed to stderr (else stdout), or its exit code: why it failed, in brief."""
    lines = proc.stderr.strip().splitlines() or proc.stdout.strip().splitlines()
    return lines[-1] if lines else f"exit {proc.returncode}"


def check_orca() -> Check:
    """Orca (the worktree app the hub starts workers in) is reachable: `orca status --json` says
    result.runtime.reachable. Only a warning: without it the hub falls back to two paste steps."""
    fix = "open the Orca app (or install it); until then the hub starts workers with two paste steps instead"
    proc, why = run_tool("orca", "status", "--json")
    if proc is None:
        return Check("orca", WARN, why, fix)
    try:
        reachable = json.loads(proc.stdout)["result"]["runtime"]["reachable"] is True
    except (ValueError, KeyError, TypeError):
        reachable = False
    if proc.returncode != 0 or not reachable:
        detail = f"orca status failed ({last_line(proc)})" if proc.returncode else "Orca's runtime is not reachable"
        return Check("orca", WARN, detail, fix)
    return Check("orca", OK, "Orca is reachable")


def check_gh(folder: Path) -> Check:
    """gh is logged in, and inside a checkout with a GitHub origin, to an account that can push to it (the right
    account: workers push, open PRs and merge as whoever gh is logged in as)."""
    proc, why = run_tool("gh", "auth", "status", "--active", "--hostname", "github.com")
    if proc is None:
        return Check("gh", FAIL, why, "install the GitHub CLI (brew install gh), then run: gh auth login")
    if proc.returncode != 0:
        why_not = last_line(proc)
        if "unknown flag" in why_not:  # --active is new in gh 2.40
            return Check("gh", FAIL, f"gh is too old ({why_not})", "upgrade gh: brew upgrade gh")
        fix = "run: gh auth login (unless the line above says GitHub couldn't be reached: then check the network)"
        return Check("gh", FAIL, f"gh is not logged in to github.com ({why_not})", fix)
    try:
        origin = policy.remote_slugs(folder)[0]
    except policy.RepoUnknown as error:
        detail = f"gh is logged in, but which repo this is isn't known ({error})"
        return Check("gh", FAIL, detail, "check that git -C <this folder> remote -v runs, then run tower doctor again")
    if origin is None:
        return Check("gh", OK, "gh is logged in (no GitHub origin here, so no repo to check access to)")
    switch = f"run gh auth switch to an account that can push to {origin} (gh auth login adds one)"
    proc, why = run_tool("gh", "repo", "view", origin, "--json", "viewerPermission")
    if proc is None or proc.returncode != 0:
        # The account may not see the repo, or GitHub may be out of reach: gh's own line says which.
        detail = f"gh can't read {origin} ({last_line(proc) if proc else why})"
        return Check("gh", FAIL, detail, f"if GitHub is reachable from here, {switch}")
    try:
        permission = json.loads(proc.stdout)["viewerPermission"] or "no"
    except (ValueError, KeyError, TypeError):
        detail = f"gh repo view printed something unexpected for {origin}: {proc.stdout.strip()[:80]!r}"
        return Check("gh", FAIL, detail, "upgrade gh (brew upgrade gh), then run tower doctor again")
    if permission not in CAN_PUSH:
        return Check("gh", FAIL, f"gh's active account has {permission} access to {origin}, so it can't push", switch)
    return Check("gh", OK, f"gh is logged in with {permission} access to {origin}")


def check_plugin() -> Check:
    """The tower plugin (skills, hooks, pane) is installed and enabled, by `claude plugin list --json`."""
    proc, why = run_tool("claude", "plugin", "list", "--json")
    if proc is None:
        return Check("plugin", FAIL, why, INSTALL_CLAUDE)
    if proc.returncode != 0:
        return Check("plugin", FAIL, f"claude plugin list failed ({last_line(proc)})", "run: claude plugin list")
    try:
        listed = json.loads(proc.stdout)
    except ValueError:
        listed = None
    if not isinstance(listed, list):
        return Check("plugin", FAIL, "claude plugin list --json printed something unexpected", FORMAT_FIX)
    found = [p for p in listed if isinstance(p, dict) and p.get("id") == PLUGIN_ID]
    if not found:
        return Check("plugin", FAIL, f"{PLUGIN_ID} is not installed", f"run: claude plugin install {PLUGIN_ID}")
    # One entry per scope it's installed in (user, project, local); enabled in any of them is enough.
    enabled = [p for p in found if p.get("enabled") is True or p.get("projectEnabled") is True]
    if not enabled:
        return Check("plugin", FAIL, f"{PLUGIN_ID} is disabled", f"run: claude plugin enable {PLUGIN_ID}")
    version = enabled[0].get("version")
    return Check("plugin", OK, f"{PLUGIN_ID} {version} is enabled" if version else f"{PLUGIN_ID} is enabled")


def check_agents() -> Check:
    """`claude agents --json` (the live sessions the scans read) still parses as tower expects: a list of objects,
    each with a cwd. Its format is undocumented, so this is where a Claude Code update that changes it shows first."""
    listed, why = sessions.read_agents()
    if listed is None:
        return Check("agents", FAIL, why, INSTALL_CLAUDE if "not on PATH" in why else FORMAT_FIX)
    unplaced = sum(1 for entry in listed if not isinstance(entry, dict) or not isinstance(entry.get("cwd"), str))
    if unplaced:
        detail = (
            f"{unplaced} of {len(listed)} entries in claude agents --json have no cwd (its format may have changed)"
        )
        return Check("agents", FAIL, detail, FORMAT_FIX)
    return Check("agents", OK, f"claude agents --json lists {len(listed)} session(s)")


def check_installed(name: str, commands: tuple[str, ...], install: str, version_must_mention: str = "") -> Check:
    """name's tool runs: the first of commands on PATH answers `--version`, with output that says
    version_must_mention (for a command name another program also uses, as Ghostscript does gs)."""
    broken = ""  # the first command on PATH that didn't run, and why: reported only if no later one works
    others = []  # notes on commands on PATH that turned out to be another program
    for command in commands:
        proc, _ = run_tool(command, "--version")
        if proc is None:
            continue
        if proc.returncode != 0:
            first = next(iter(proc.stderr.strip().splitlines() + proc.stdout.strip().splitlines()), "")
            broken = broken or f"{command} is on PATH but doesn't run ({first or f'exit {proc.returncode}'})"
            continue
        if version_must_mention in proc.stdout:
            version = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
            return Check(name, OK, version or f"{command} runs")
        others.append(f"{command} there is another program")
    if broken:  # a mise shim with no global version lands here too: `mise use -g` (in install) is its fix
        return Check(name, FAIL, broken, install)
    note = f" ({'; '.join(others)})" if others else ""
    return Check(name, FAIL, f"{' or '.join(commands)} not on PATH{note}", install)


def check_git_spice() -> Check:
    """git-spice, which submits and restacks stacked PRs, is installed as `git-spice`, the name tower's kickoffs and
    Housekeeping lines run. Before 0.25 it was also `gs`: one found only under that name is a warning."""
    install = "brew install git-spice (or: mise use -g aqua:abhinav/git-spice)"
    found = check_installed("git-spice", ("git-spice",), install, version_must_mention="git-spice")
    if found.status == OK:
        return found
    as_gs = check_installed("git-spice", ("gs",), install, version_must_mention="git-spice")
    if as_gs.status != OK:
        return check_installed("git-spice", ("git-spice", "gs"), install, version_must_mention="git-spice")
    fix = "upgrade it (brew upgrade git-spice, or mise use -g aqua:abhinav/git-spice), so git-spice is on PATH"
    return Check("git-spice", WARN, f"{as_gs.detail}, but only as gs: tower's kickoffs run git-spice", fix)


def check_mise() -> Check:
    """mise, which installs the tool versions a repo pins in mise.toml, is installed."""
    return check_installed("mise", ("mise",), "brew install mise (https://mise.jdx.dev)")


def check_policy() -> Check:
    """The policy file (which actions ask first, repo by repo) reads with no problems. Read without migrating
    tower.conf's old approvals, so doctor writes nothing."""
    loaded = policy.load(migrate_conf=False)
    path = policy.display_path(loaded.path)
    if loaded.problems:
        fix = f"edit {path} as each problem says (tower policy lists them with their lines)"
        return Check("policy", FAIL, "; ".join(loaded.problems), fix)
    if not loaded.exists:
        migrated = policy.migration_profile()
        if migrated:
            detail = f"no policy file at {path} yet; tower.conf's old approvals become profile {migrated} when tower policy first runs"
        else:
            detail = f"no policy file at {path}, so every repo gets the strictest profile ({policy.STRICTEST})"
        fix = "run in your own terminal: tower policy use <profile> (or re-run setup.sh)"
        return Check("policy", WARN, detail, fix)
    if loaded.default_profile is None:
        detail = f"{path} has no default_profile, so a repo no [repos] entry names gets {policy.STRICTEST}"
        return Check("policy", WARN, detail, "run in your own terminal: tower policy use <profile>")
    return Check("policy", OK, f"{path} reads cleanly; default_profile is {loaded.default_profile}")


def newest_event_times(log: Path) -> list[datetime]:
    """The times of the events among log's last lines ([] when it doesn't exist). Read without the log's lock (which
    would create the lock file): a line being appended is at worst skipped as damaged."""
    try:
        with open(log, "rb") as handle:
            lines = deque(handle, maxlen=TAIL_LINES)
    except FileNotFoundError:
        return []
    parsed = (events.parse_line(line) for line in lines)
    return [ts for ts in (parse_ts(event.ts) for event in parsed if event) if ts is not None]


def check_hooks(now: datetime) -> Check:
    """tower's hooks are firing: the event log has an event from the last hour before now (an aware datetime). Only a
    warning: an hour with no Claude session has none either. A log that can't be read fails."""
    directory = events.default_dir()
    try:
        times = newest_event_times(directory / events.LOG_NAME)
        rotated = sorted(directory.glob("events-*.jsonl"), key=lambda path: path.stat().st_mtime)
        if not times and rotated:  # this month's log is new or empty, just after it rotated: last month's holds it
            times = newest_event_times(rotated[-1])
    except OSError as error:
        detail = f"can't read the event log in {directory} ({error.strerror or type(error).__name__})"
        return Check("hooks", FAIL, detail, f"make {directory} and its files readable by you")
    if not times:
        return Check("hooks", WARN, f"no events in {directory}", HOOKS_FIX)
    age = max(now - max(times), timedelta(0))  # an event stamped ahead of this clock counts as just now
    late = age > HOOKS_WINDOW
    ago = f"{int(age.total_seconds() // 3600)}h ago" if late else f"{int(age.total_seconds() // 60)}m ago"
    if late:
        return Check("hooks", WARN, f"no event in the last hour (the last was {ago})", HOOKS_FIX)
    return Check("hooks", OK, f"the last event was {ago}")


def check_vault() -> Check:
    """The vault folder holding every project's Ledger (projects_dir in tower.conf) exists."""
    conf_path = ledger.conf_path()
    fix = f"set projects_dir in {conf_path} to your vault's projects folder, or re-run setup.sh"
    try:
        conf = ledger.read_conf(conf_path)
    except ledger.ConfUnreadable as error:  # it may well set projects_dir: it just couldn't be read
        return Check("vault", FAIL, str(error), f"fix or remove {conf_path}, then run setup.sh again")
    try:
        root = ledger.projects_root(conf)
    except ledger.LedgerError as error:  # unset or missing
        return Check("vault", FAIL, str(error), fix)
    return Check("vault", OK, f"projects_dir is {root}")


def run_all(folder: Path, now: datetime) -> list[Check]:
    """Every check, in the order the report lists them; folder picks the repo the gh check asks about."""
    return [
        check_orca(),
        check_gh(folder),
        check_vault(),
        check_plugin(),
        check_git_spice(),
        check_mise(),
        check_policy(),
        check_agents(),
        check_hooks(now),
    ]


def failed(checks: list[Check]) -> int:
    """How many checks failed: the problems that make `tower doctor` exit 1 (warnings don't count)."""
    return sum(1 for check in checks if check.status == FAIL)


def render(checks: list[Check]) -> str:
    """The report: one line per check (status, name, what it found), a `fix:` line under each that isn't ok, and a
    last line counting problems (failures) and warnings."""
    width = max(len(check.name) for check in checks)
    lines = []
    for check in checks:
        label = "FAIL" if check.status == FAIL else check.status
        lines.append(f"{label:<4}  {check.name:<{width}}  {check.detail}")
        if check.status != OK:
            lines.append(f"{'':<4}  {'':<{width}}  fix: {check.fix}")
    problem_count = failed(checks)
    warned = sum(1 for check in checks if check.status == WARN)
    problems = "no problems" if not problem_count else f"{problem_count} problem{'s' if problem_count > 1 else ''}"
    warnings = f"{warned} warning{'s' if warned != 1 else ''}"
    lines.append(f"tower doctor: {problems}, {warnings}")
    return "\n".join(lines) + "\n"
