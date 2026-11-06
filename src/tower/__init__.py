"""tower's core: the deterministic part of tower. It records and computes facts; Claude writes only the prose.

The hooks import this package under whatever `python3` they find (3.9 on a stock Mac), so this file and the modules the
hooks use (events, hook_events) stay 3.9-compatible. The `tower` CLI needs 3.11+.
"""

# tower's one version. It must equal plugin/.claude-plugin/plugin.json's (tests/test_cli.py checks).
__version__ = "0.8.0"
