"""cli.py: the `tower` command.

tower --version            print tower's version
tower events tail [-n N]   print the last N events (default 10) of this month's log, as JSON lines
tower state [--repo PATH] [--json] [--pr] [--save-agents FILE]
                           each worktree's effective state (see state.py); text as hub-scan.sh prints it, or JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tower import __version__, events, state


def non_negative_int(text: str) -> int:
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
    tail.add_argument("-n", type=non_negative_int, default=10, help="how many events (default 10)")
    state_parser = commands.add_parser("state", help="each worktree's effective state: one truth for hub, pane and WIP")
    state_parser.add_argument("--repo", type=Path, default=Path("."), help="any folder in the repo (default: here)")
    state_parser.add_argument("--json", action="store_true", help="print JSON instead of the hub's text sections")
    state_parser.add_argument("--pr", action="store_true", help="also ask GitHub (gh) for each branch's PR")
    state_parser.add_argument(
        "--save-agents", type=Path, metavar="FILE", help="also write `claude agents --json`'s output here (hub-scan.sh)"
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


def show_state(repo: Path, as_json: bool, with_pr: bool, save_agents: Path | None) -> int:
    """Print the repo's effective state; exit 1 (and say why) outside a git repo."""
    try:
        report = state.gather(repo, with_pr=with_pr, save_agents=save_agents)
    except state.NotARepo as error:
        print(f"tower: {error}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(state.to_json(report), indent=2))
    else:
        sys.stdout.write(state.render_text(report))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tower command with argv (default: the process's arguments); returns the exit code."""
    args = parser().parse_args(argv)
    if args.command == "events" and args.events_command == "tail":
        return events_tail(args.n)
    if args.command == "state":
        return show_state(args.repo, args.json, args.pr, args.save_agents)
    raise AssertionError(f"unhandled command: {args}")  # argparse's required subcommands make this unreachable
