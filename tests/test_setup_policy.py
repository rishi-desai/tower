"""Tests for setup.sh's config step: tower.conf, and the machine's default profile in the policy file.

Each test runs setup.sh with --no-plugin in a fake HOME, without a terminal (so it never prompts), and reads the
result back through tower's core.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tower import policy

SETUP = Path(__file__).resolve().parent.parent / "setup.sh"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake HOME, with tower's core in this process pointed at its tower.conf and policy file."""
    fake = tmp_path / "home"
    (fake / ".claude").mkdir(parents=True)
    monkeypatch.setenv("TOWER_CONF", str(fake / ".claude" / "tower.conf"))
    monkeypatch.setenv("TOWER_DIR", str(fake / ".claude" / "tower"))
    return fake


def setup(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run setup.sh --no-plugin with ARGS in the fake HOME, with no terminal."""
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["bash", str(SETUP), "--no-plugin", "--projects-dir", str(home / "vault"), "--wip-limit", "4", *args],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_new_machine_gets_the_profile_it_asked_for(home: Path) -> None:
    proc = setup(home, "--profile", "personal")

    assert proc.returncode == 0, proc.stderr
    assert "default profile: work -> personal" in proc.stdout
    assert policy.load().for_repo(None).profile == "personal"
    conf = (home / ".claude" / "tower.conf").read_text(encoding="utf-8")
    assert "wip_limit=4" in conf
    assert "push_approval" not in conf and "pr_approval" not in conf


def test_old_approvals_are_migrated_before_tower_conf_is_rewritten(home: Path) -> None:
    (home / ".claude" / "tower.conf").write_text(
        "projects_dir=/old\nwip_limit=10\npush_approval=none\npr_approval=none\nproject.poot=Poot\n", encoding="utf-8"
    )

    proc = setup(home)  # no --profile and no terminal: keep the current one

    assert proc.returncode == 0, proc.stderr
    repo_policy = policy.load().for_repo(None)
    assert (repo_policy.profile, repo_policy.push, repo_policy.merge_to_main, repo_policy.comments) == (
        "migrated",
        "none",
        "auto",
        "ask",
    )
    conf = (home / ".claude" / "tower.conf").read_text(encoding="utf-8")
    assert "push_approval" not in conf and "project.poot=Poot" in conf


def test_a_dry_run_changes_nothing(home: Path) -> None:
    old = "projects_dir=/old\npush_approval=force\n"
    (home / ".claude" / "tower.conf").write_text(old, encoding="utf-8")

    proc = setup(home, "--dry-run", "--profile", "sandbox")

    assert proc.returncode == 0, proc.stderr
    assert "[dry-run] migrate push_approval/pr_approval" in proc.stdout
    assert "[dry-run] tower policy use sandbox" in proc.stdout
    assert (home / ".claude" / "tower.conf").read_text(encoding="utf-8") == old
    assert not (home / ".claude" / "tower" / "policy.toml").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--profile", "yolo"), "profile must be one of: personal sandbox work; got 'yolo'"),
        (("--push-approval", "none"), "--push-approval was replaced by --profile"),
    ],
)
def test_a_bad_profile_or_an_old_flag_stops_before_anything_changes(
    home: Path, args: tuple[str, str], message: str
) -> None:
    proc = setup(home, *args)

    assert proc.returncode == 2
    assert message in proc.stderr
    assert not (home / ".claude" / "tower.conf").exists()
