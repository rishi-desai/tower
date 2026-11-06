"""setup.sh in a fake HOME: one `claude plugin install` gets everything tower ships (#37, T18). Uses the real
`claude` CLI, which mise installs for CI; plugin installs from a local folder need no login or network."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_JSON = ROOT / "plugin" / ".claude-plugin" / "plugin.json"

pytestmark = pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude CLI (mise install)")


def fake_home_env(tmp_path: Path) -> dict[str, str]:
    """The environment with HOME and git's global config moved into tmp_path, so nothing real is touched."""
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CONFIG_DIR"}
    env["HOME"] = str(tmp_path / "home")
    env["GIT_CONFIG_GLOBAL"] = str(tmp_path / "gitconfig")
    (tmp_path / "home").mkdir()
    return env


def test_a_fresh_install_gets_skills_hooks_and_the_pane_from_one_plugin(tmp_path: Path) -> None:
    env = fake_home_env(tmp_path)
    (tmp_path / "projects").mkdir()
    setup = subprocess.run(
        [
            str(ROOT / "setup.sh"),
            *("--projects-dir", str(tmp_path / "projects"), "--wip-limit", "3"),
            *("--push-approval", "every", "--pr-approval", "every", "--plugin-source", str(ROOT)),
        ],
        check=False,  # the assert below shows setup's output when it fails
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert setup.returncode == 0, setup.stdout + setup.stderr

    listed = subprocess.run(
        ["claude", "plugin", "list", "--json"], env=env, capture_output=True, text=True, check=True, timeout=60
    )
    plugins = json.loads(listed.stdout)
    version = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    assert [(p["id"], p["version"], p["enabled"]) for p in plugins] == [("tower@tower", version, True)]

    installed = Path(plugins[0]["installPath"])
    hooks = json.loads((installed / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert hooks["modules"] == ["./register.tsx"]  # the pane mod
    assert "PreToolUse" in hooks["hooks"]  # the approval hook beside it
    for shipped in (
        "hooks/register.tsx",
        "types/index.d.ts",
        "scripts/run-tower.py",
        "lib/tower/__init__.py",
        "skills/hub/SKILL.md",
    ):
        assert (installed / shipped).is_file(), shipped
