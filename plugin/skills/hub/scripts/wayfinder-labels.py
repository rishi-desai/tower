#!/usr/bin/env python3
"""wayfinder-labels.py: print the `gh label create` commands for this repo's missing wayfinder:* labels.

  wayfinder-labels.py

Run it from inside the repo (gh infers the repo from the checkout). `/wayfinder` labels its map
issue `wayfinder:map` and its tickets `wayfinder:research|prototype|grilling|task`, and fails on the
first issue if a label is missing. Nothing else creates them.

Prints one command per missing label, ready to run as-is, and nothing when all five exist. It never
creates labels itself: tower's approval hook only sees the command text the agent runs, so the hub
runs the printed commands directly, where the hook can ask the user. If `gh label list` fails, its
error goes to stderr and we exit non-zero with nothing on stdout.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

COLOR = "c5def5"
LABELS = {
    "wayfinder:map": "Wayfinder map: notes, decisions so far, fog",
    "wayfinder:research": "Wayfinder ticket: look something up",
    "wayfinder:prototype": "Wayfinder ticket: build something throwaway to test an idea",
    "wayfinder:grilling": "Wayfinder ticket: talk it through with the user",
    "wayfinder:task": "Wayfinder ticket: a small concrete step",
}
# gh label list returns 30 labels unless told otherwise; a repo with more would look like it's missing some.
LIST_LIMIT = "1000"


def existing_labels() -> set[str] | None:
    """Names of the repo's labels, or None (after passing gh's error through) if gh fails."""
    proc = subprocess.run(
        ["gh", "label", "list", "--limit", LIST_LIMIT, "--json", "name"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or f"gh label list exited {proc.returncode}\n")
        return None
    return {label["name"] for label in json.loads(proc.stdout)}


def main() -> int:
    existing = existing_labels()
    if existing is None:
        return 1
    for name, description in LABELS.items():
        if name not in existing:
            print(shlex.join(["gh", "label", "create", name, "--color", COLOR, "--description", description]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
