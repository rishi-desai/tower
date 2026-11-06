"""cli.py: the `tower` command.

tower --version            print tower's version
tower events tail [-n N]   print the last N events (default 10) of this month's log, as JSON lines
tower state [--repo PATH] [--json] [--pr]
                           each worktree's effective state (see state.py), as text or JSON
tower scan [--no-github]   the hub's view of this repo: state, live sessions, GitHub, backlog (see scan.py)
tower adopt-scan           facts for bringing an in-flight repo under tower (see adopt.py)
tower card-path [DIR]      where the worktree holding DIR (default: here) keeps its status card (see ledger.py)
tower rules                link this project's personal rules into this worktree; prints the rules file (see rules.py)
tower stats [--since 14d] [--repo [PATH]]
                           metrics per PR merged in the window, from the event log and GitHub (see stats.py);
                           every repo in the log, or only PATH's (default: this one) with --repo
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tower import __version__, adopt, events, github, hook_events, ledger, rules, scan, state, stats
from tower.gitrepo import NotARepo

SINCE_SPAN = re.compile(r"(\d+)([hdw])")
SINCE_UNITS = {"h": "hours", "d": "days", "w": "weeks"}
# Merges this far before the window are fetched too, only to cut a reused branch's events at its earlier merge.
BOUNDARY_LOOKBACK = timedelta(days=30)
MAX_SINCE = timedelta(days=3650)  # a longer --since overflows datetime arithmetic, and no log is that old


def non_negative_int(text: str) -> int:
    """argparse type for -n: a whole number, 0 or more."""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {value}")
    return value


def since_span(text: str) -> timedelta:
    """argparse type for --since: a count and a unit, h(ours), d(ays) or w(eeks), like 14d."""
    match = SINCE_SPAN.fullmatch(text)
    if match is None:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a span like 14d, 2w or 36h")
    try:
        span = timedelta(**{SINCE_UNITS[match.group(2)]: int(match.group(1))})
    except OverflowError:
        span = MAX_SINCE + timedelta(days=1)
    if span > MAX_SINCE:
        raise argparse.ArgumentTypeError(f"{text!r} is longer than {MAX_SINCE.days} days")
    return span


def parser() -> argparse.ArgumentParser:
    """The argument parser for every tower subcommand."""
    top = argparse.ArgumentParser(prog="tower", description="tower's core: facts about your parallel Claude sessions.")
    top.add_argument("--version", action="version", version=f"tower {__version__}")
    commands = top.add_subparsers(dest="command", required=True)
    events_parser = commands.add_parser("events", help="the event log tower's hooks write")
    events_commands = events_parser.add_subparsers(dest="events_command", required=True)
    tail = events_commands.add_parser("tail", help="print the last events of this month's log as JSON lines")
    tail.add_argument("-n", type=non_negative_int, default=10, help="how many events (default 10)")
    state_parser = commands.add_parser("state", help="each worktree's effective state: one truth for hub, pane and WIP")
    state_parser.add_argument("--repo", type=Path, default=Path("."), help="any folder in the repo (default: here)")
    state_parser.add_argument("--json", action="store_true", help="print JSON instead of the hub's text sections")
    state_parser.add_argument("--pr", action="store_true", help="also ask GitHub (gh) for each branch's PR")
    scan_parser = commands.add_parser("scan", help="the hub's view of this repo: state, sessions, GitHub, backlog")
    scan_parser.add_argument("--no-github", action="store_true", help="skip GitHub (for frequent refreshes: the pane)")
    commands.add_parser("adopt-scan", help="read-only facts for bringing an in-flight repo under tower")
    card_parser = commands.add_parser("card-path", help="where this worktree's status card lives")
    card_parser.add_argument("dir", nargs="?", type=Path, default=Path("."), help="a folder in the worktree")
    commands.add_parser("rules", help="link this project's personal rules file into this worktree")
    stats_parser = commands.add_parser("stats", help="metrics per merged PR, from the event log and GitHub")
    stats_parser.add_argument(
        "--since", type=since_span, default=timedelta(days=14), help="how far back merges count (default 14d)"
    )
    stats_parser.add_argument(
        "--repo", nargs="?", const=".", metavar="PATH", help="only this checkout's repo (default with no PATH: here)"
    )
    return top


def events_tail(n: int) -> int:
    """Print the last n events, oldest first; with no log yet, say where it looked."""
    directory = events.default_dir()
    lines = events.tail(directory, n)
    if not lines and not (directory / events.LOG_NAME).exists():
        print(f"tower: no events yet in {directory / events.LOG_NAME}", file=sys.stderr)
    for line in lines:
        print(line)
    return 0


def show_state(repo: Path, as_json: bool, with_pr: bool) -> int:
    """Print the repo's effective state; exit 1 (and say why) outside a git repo."""
    try:
        report = state.gather(repo, with_pr=with_pr)
    except NotARepo as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(state.to_json(report), indent=2))
    else:
        sys.stdout.write(state.render_text(report))
    return 0


def stats_command(since: timedelta, repo_path: str | None) -> int:
    """Print the metrics table for PRs merged in the last since, for repo_path's repo or every repo in the log.
    Exit code: 2 if repo_path isn't in a git repo, 1 if gh failed for some repo, else 0. A repo whose folder is gone,
    and a ticket whose reopens can't be read, are skipped with a warning only."""
    directory = events.default_dir()
    log, damaged = events.read_all(directory)
    if not log:
        print(f"tower: no events yet in {directory}", file=sys.stderr)
        return 0
    cutoff = datetime.now(timezone.utc) - since
    if repo_path is not None:
        try:
            only = hook_events.place(repo_path)[0]
        except subprocess.TimeoutExpired:
            print(f"tower: git timed out in {repo_path}", file=sys.stderr)
            return 2
        if only is None:
            print(f"tower: {repo_path} isn't inside a git repo", file=sys.stderr)
            return 2
        repos = [only]
    else:
        repos = sorted({e.repo for e in log if e.repo is not None})
    prs: list[stats.MergedPr] = []
    reopens: dict[tuple[str, int], list[datetime]] = {}
    failed = False
    for repo in repos:
        if not Path(repo).is_dir():
            print(f"tower: skipped {repo}: the folder is gone", file=sys.stderr)
            continue
        try:
            repo_prs = github.merged_prs(repo, cutoff - BOUNDARY_LOOKBACK)
        except github.GhError as error:
            print(f"tower: skipped {repo}: {error}", file=sys.stderr)
            failed = True
            continue
        prs += repo_prs
        # Only tickets of PRs in the window: an earlier PR's reopens never show.
        tickets = {ticket for p in repo_prs if p.merged_at >= cutoff for ticket in p.closes}
        repo_reopens, warnings = github.ticket_reopens(repo, tickets)
        reopens.update({(repo, ticket): times for ticket, times in repo_reopens.items()})
        for warning in warnings:
            print(f"tower: {repo}: {warning}", file=sys.stderr)
    result = stats.compute(log, prs, reopens, merged_since=cutoff)
    print(stats.render(result))
    left_out = damaged + result.unreadable
    if left_out:
        print(f"tower: left out {left_out} damaged line(s) of the event log", file=sys.stderr)
    return 1 if failed else 0


def show_scan(args: argparse.Namespace) -> int:
    """Print the scan, card path or rules path the command asks for; exit 1, saying why, when the repo or the ledger
    can't be found or the rules can't be linked safely."""
    here = Path(".")
    try:
        if args.command == "scan":
            sys.stdout.write(scan.hub_scan(here, github=not args.no_github))
        elif args.command == "adopt-scan":
            sys.stdout.write(adopt.adopt_scan(here))
        elif args.command == "card-path":
            print(ledger.card_path(args.dir))
        else:
            rules_file, warnings = rules.link_rules(here)
            for warning in warnings:
                print(f"tower: {warning}", file=sys.stderr)
            print(rules_file)
    except (NotARepo, ledger.LedgerError, rules.RulesError) as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tower command with argv (default: the process's arguments); returns the exit code."""
    args = parser().parse_args(argv)
    if args.command == "events" and args.events_command == "tail":
        return events_tail(args.n)
    if args.command == "state":
        return show_state(args.repo, args.json, args.pr)
    if args.command in ("scan", "adopt-scan", "card-path", "rules"):
        return show_scan(args)
    if args.command == "stats":
        return stats_command(args.since, args.repo)
    raise AssertionError(f"unhandled command: {args}")  # argparse's required subcommands make this unreachable
