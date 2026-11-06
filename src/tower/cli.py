"""cli.py: the `tower` command.

tower --version            print tower's version
tower events tail [-n N]   print the last N events (default 10) of this month's log, as JSON lines
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from tower import __version__, events


def count(text: str) -> int:
    """argparse type for -n: a whole number, 0 or more."""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {value}")
    return value


def parser() -> argparse.ArgumentParser:
    """The argument parser for every tower subcommand."""
    top = argparse.ArgumentParser(prog="tower", description="tower's core: facts about your parallel Claude sessions.")
    top.add_argument("--version", action="version", version=f"tower {__version__}")
    commands = top.add_subparsers(dest="command", required=True)
    events_parser = commands.add_parser("events", help="the event log tower's hooks write")
    events_commands = events_parser.add_subparsers(dest="events_command", required=True)
    tail = events_commands.add_parser("tail", help="print the last events of this month's log as JSON lines")
    tail.add_argument("-n", type=count, default=10, help="how many events (default 10)")
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tower command with argv (default: the process's arguments); returns the exit code."""
    args = parser().parse_args(argv)
    if args.command == "events" and args.events_command == "tail":
        return events_tail(args.n)
    raise AssertionError(f"unhandled command: {args}")  # argparse's required subcommands make this unreachable
