"""setup.sh in a fake HOME: one `claude plugin install` gets everything tower ships, and an install from the days of
two plugins (tower and tower-pane) upgrades to one (#37, T18). Uses the real `claude` CLI, which mise installs for CI;
plugin installs from a local folder need no login or network."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_JSON = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
VERSION = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]

pytestmark = pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude CLI (mise install)")


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """The environment with HOME, git's global config and the XDG config folder (setup.sh's fallback for the global
    git ignore) moved into tmp_path, and tower's own overrides dropped, so nothing real is read or touched."""
    env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_CONFIG_DIR", "TOWER_CONF")}
    env["HOME"] = str(tmp_path / "home")
    env["GIT_CONFIG_GLOBAL"] = str(tmp_path / "gitconfig")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "home" / ".config")
    (tmp_path / "home").mkdir()
    (tmp_path / "projects").mkdir()
    return env


def claude(env: dict[str, str], *args: str) -> str:
    """Runs one `claude` command in the fake HOME; its stdout. Fails the test if it fails."""
    return subprocess.run(["claude", *args], env=env, capture_output=True, text=True, check=True, timeout=60).stdout


def run_setup(env: dict[str, str], tmp_path: Path, plugin_source: Path) -> None:
    """Runs setup.sh with every answer given as a flag, as a scripted re-run would; fails the test with its output."""
    setup = subprocess.run(
        [
            str(ROOT / "setup.sh"),
            *("--projects-dir", str(tmp_path / "projects"), "--wip-limit", "3"),
            *("--profile", "work", "--plugin-source", str(plugin_source)),
        ],
        check=False,  # the assert below shows setup's output when it fails
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert setup.returncode == 0, setup.stdout + setup.stderr


def installed_plugins(env: dict[str, str]) -> list[dict[str, object]]:
    """`claude plugin list --json` in the fake HOME."""
    plugins: list[dict[str, object]] = json.loads(claude(env, "plugin", "list", "--json"))
    return plugins


def write_marketplace(folder: Path, plugins: dict[str, str]) -> None:
    """A tower marketplace in folder listing plugins (name -> source folder relative to it)."""
    (folder / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    listed = [{"name": name, "source": source} for name, source in plugins.items()]
    manifest = {"name": "tower", "owner": {"name": "test"}, "plugins": listed}
    (folder / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest), encoding="utf-8")


def write_plugin(folder: Path, name: str, version: str) -> None:
    """An empty plugin standing in for an old release: only its manifest."""
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": name, "version": version}
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_a_fresh_install_gets_skills_hooks_and_the_pane_from_one_plugin(env: dict[str, str], tmp_path: Path) -> None:
    """A machine with nothing installed ends with one enabled tower@tower holding the skills, the command hooks, the
    pane mod and the core."""
    run_setup(env, tmp_path, ROOT)

    plugins = installed_plugins(env)
    assert [(p["id"], p["version"], p["enabled"]) for p in plugins] == [("tower@tower", VERSION, True)]
    installed = Path(str(plugins[0]["installPath"]))
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


@pytest.mark.parametrize("had_tower", [True, False], ids=["tower-and-pane", "pane-only"])
def test_an_install_from_two_plugins_ends_with_one(env: dict[str, str], tmp_path: Path, had_tower: bool) -> None:
    """A machine with the old tower-pane (and tower 0.10.0, or not) ends with only the current tower@tower once the
    marketplace has moved on: setup updates the marketplace before installing or updating, then drops the pane.

    A local-folder marketplace is read live, so this can't show the stale clone a GitHub marketplace keeps until
    `claude plugin marketplace update` (and `claude` takes no local git URL); setup updates it first for that case."""
    market = tmp_path / "market"
    write_plugin(market / "tower", "tower", "0.10.0")
    write_plugin(market / "pane", "tower-pane", "0.7.0")
    write_marketplace(market, {"tower": "./tower", "tower-pane": "./pane"})
    claude(env, "plugin", "marketplace", "add", str(market))
    claude(env, "plugin", "install", "tower-pane@tower")
    if had_tower:
        claude(env, "plugin", "install", "tower@tower")

    # The push: the marketplace now lists one plugin, this checkout's (copied, so lib/tower's symlink is followed).
    shutil.rmtree(market / "tower")
    shutil.rmtree(market / "pane")
    shutil.copytree(ROOT / "plugin", market / "tower", ignore=shutil.ignore_patterns("__pycache__"))
    write_marketplace(market, {"tower": "./tower"})
    run_setup(env, tmp_path, market)

    assert [(p["id"], p["version"]) for p in installed_plugins(env)] == [("tower@tower", VERSION)]
