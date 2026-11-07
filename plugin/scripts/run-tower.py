#!/usr/bin/env python3
"""run-tower.py: runs the `tower` command from the plugin, with nothing installed.

  python3 -I run-tower.py scan [--no-github]      (or any other `tower` subcommand)

The skills run it for `scan`, `adopt-scan`, `rules`, `card write`, workers' `merge` and the hub's `doctor`, setup.sh for `policy` and `doctor`, the card-reminder hook for `card-path`, and the
tower pane for `state --json` and `scan`. Like record-event.py, it reaches
tower's core through the plugin's `lib/tower` symlink (a marketplace install copies the link's target), so it works under whatever `python3` is
on PATH, 3.9 included. It isn't named tower.py: Python puts this folder first on sys.path, and a tower.py here would
shadow the package.
"""

from __future__ import annotations

import os
import sys

# From this file's place, never $CLAUDE_PLUGIN_ROOT: when the tower pane runs this, that can be the pane's own
# plugin, which has no lib/.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "lib"))
    from tower import cli

    sys.exit(cli.main(sys.argv[1:]))
