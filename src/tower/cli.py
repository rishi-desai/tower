"""cli.py: the `tower` command.

tower --version            print tower's version
tower events tail [-n N]   print the last N events (default 10) of this month's log, as JSON lines
tower stats [--since 14d] [--repo [PATH]]
                           metrics per PR merged in the window, from the event log and GitHub (see stats.py);
                           every repo in the log, or only PATH's (default: this one) with --repo
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tower import __version__, events, github, hook_events, stats

SINCE_SPAN = re.compile(r"(\d+)([hdw])")
SINCE_UNITS = {"h": "hours", "d": "days", "w": "weeks"}
# Merges this far before the window are fetched too, only to cut a reused branch's events at its earlier merge.
BOUNDARY_LOOKBACK = timedelta(days=30)


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
    return timedelta(**{SINCE_UNITS[match.group(2)]: int(match.group(1))})


def parser() -> argparse.ArgumentParser:
    """The argument parser for every tower subcommand."""
    top = argparse.ArgumentParser(prog="tower", description="tower's core: facts about your parallel Claude sessions.")
    top.add_argument("--version", action="version", version=f"tower {__version__}")
    commands = top.add_subparsers(dest="command", required=True)
    events_parser = commands.add_parser("events", help="the event log tower's hooks write")
    events_commands = events_parser.add_subparsers(dest="events_command", required=True)
    tail = events_commands.add_parser("tail", help="print the last events of this month's log as JSON lines")
    tail.add_argument("-n", type=non_negative_int, default=10, help="how many events (default 10)")
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


def stats_command(since: timedelta, repo_path: str | None) -> int:
    """Print the metrics table for PRs merged in the last since, for repo_path's repo or every repo with recent events.
    Exit code: 2 if repo_path isn't in a git repo, 1 if gh failed for some repo, else 0. A repo whose folder is gone
    is skipped with a warning only."""
    directory = events.default_dir()
    log, damaged = events.read_all(directory)
    if not log:
        print(f"tower: no events yet in {directory}", file=sys.stderr)
        return 0
    cutoff = datetime.now(timezone.utc) - since
    if repo_path is not None:
        only = hook_events.place(repo_path)[0]
        if only is None:
            print(f"tower: {repo_path} isn't inside a git repo", file=sys.stderr)
            return 2
        repos = [only]
    else:
        recent = cutoff - BOUNDARY_LOOKBACK
        repos = sorted(
            {
                e.repo
                for e in log
                if e.repo is not None and (when := stats.parse_ts(e.ts)) is not None and when >= recent
            }
        )
    prs: list[stats.MergedPr] = []
    failed = False
    for repo in repos:
        if not Path(repo).is_dir():
            print(f"tower: skipped {repo}: the folder is gone", file=sys.stderr)
            continue
        try:
            prs += github.merged_prs(repo, cutoff - BOUNDARY_LOOKBACK, reopens_from=cutoff)
        except github.GhError as error:
            print(f"tower: skipped {repo}: {error}", file=sys.stderr)
            failed = True
    result = stats.compute(log, prs, merged_since=cutoff)
    print(stats.render(result))
    left_out = damaged + result.unreadable
    if left_out:
        print(f"tower: left out {left_out} damaged line(s) of the event log", file=sys.stderr)
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tower command with argv (default: the process's arguments); returns the exit code."""
    args = parser().parse_args(argv)
    if args.command == "events" and args.events_command == "tail":
        return events_tail(args.n)
    if args.command == "stats":
        return stats_command(args.since, args.repo)
    raise AssertionError(f"unhandled command: {args}")  # argparse's required subcommands make this unreachable
