"""tower's core: the deterministic part of tower. It records and computes facts; Claude writes only the prose.

The hooks import this package under whatever `python3` they find (3.9 on a stock Mac), and hub-scan.sh runs the `tower`
CLI the same way (plugin/scripts/run-tower.py), so the whole package stays 3.9-compatible: no 3.10+ syntax or
standard-library calls (ruff checks the syntax against 3.9).
"""

# tower's one version. It must equal plugin/.claude-plugin/plugin.json's (tests/test_cli.py checks).
__version__ = "0.9.1"
