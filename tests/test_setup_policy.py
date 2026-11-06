"""Tests for setup.sh's config step: tower.conf (with the worktrees folder), and the machine's default profile in the
policy file.

Each test runs setup.sh with --no-plugin in a fake HOME, without a terminal (so it never prompts), and reads the
result back through tower's core; one drives the worktrees prompt through a pseudo-terminal.
"""

from __future__ import annotations

import os
import pty
import select
import subprocess
import time
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


def conf_lines(home: Path) -> list[str]:
    """tower.conf's lines, comments left out."""
    text = (home / ".claude" / "tower.conf").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def test_the_worktrees_folder_is_created_and_saved(home: Path) -> None:
    proc = setup(home, "--worktrees-dir", str(home / "worktrees") + "/")

    assert proc.returncode == 0, proc.stderr
    assert (home / "worktrees").is_dir()
    assert f"worktrees_dir={home / 'worktrees'}" in conf_lines(home)  # without the trailing /


def test_a_re_run_without_the_flag_keeps_the_worktrees_folder(home: Path) -> None:
    assert setup(home, "--worktrees-dir", str(home / "wt")).returncode == 0

    proc = setup(home)

    assert proc.returncode == 0, proc.stderr
    assert f"worktrees_dir={home / 'wt'}" in conf_lines(home)


def test_a_scripted_run_with_no_worktrees_folder_writes_none(home: Path) -> None:
    proc = setup(home)

    assert proc.returncode == 0, proc.stderr
    assert not [line for line in conf_lines(home) if line.startswith("worktrees_dir=")]
    assert "Workspace Directory" not in proc.stdout  # nothing to point Orca at


def test_a_relative_worktrees_folder_stops_before_anything_changes(home: Path) -> None:
    proc = setup(home, "--worktrees-dir", "./worktrees")

    assert proc.returncode == 2
    assert "must be an absolute path or ~/..." in proc.stderr
    assert not (home / ".claude" / "tower.conf").exists()


def test_a_dry_run_doesnt_create_the_worktrees_folder(home: Path) -> None:
    proc = setup(home, "--dry-run", "--worktrees-dir", str(home / "worktrees"))

    assert proc.returncode == 0, proc.stderr
    assert f"[dry-run] mkdir -p {home / 'worktrees'}" in proc.stdout
    assert not (home / "worktrees").exists()


def test_setup_says_where_the_policy_file_is_and_how_to_tune_it(home: Path) -> None:
    proc = setup(home, "--profile", "personal")

    assert proc.returncode == 0, proc.stderr
    assert f"Policy file: {home / '.claude' / 'tower' / 'policy.toml'}" in proc.stdout
    assert "add it under [profiles.personal] there" in proc.stdout


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="needs /bin/bash")
def test_the_worktrees_prompt_completes_paths_with_tab(home: Path) -> None:
    """In a terminal, under /bin/bash (3.2 on macOS): Tab completes the typed path, a folder that doesn't exist yet
    is created, and the answer lands in tower.conf. The other answers are flags, so this is the only prompt."""
    (home / "code-worktrees").mkdir()
    (home / "vault").mkdir()  # else setup asks whether to create it
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1", "TERM": "dumb"}
    args = ["--no-plugin", "--projects-dir", str(home / "vault"), "--wip-limit", "4", "--profile", "work"]
    pid, fd = pty.fork()
    if pid == 0:  # the child: setup.sh with the pty as its terminal
        os.execve("/bin/bash", ["/bin/bash", str(SETUP), *args], env)
    output = read_until(fd, b"worktrees")  # the prompt
    os.write(fd, str(home / "code-wo").encode() + b"\t")
    output += read_until(fd, b"code-worktrees/")  # readline completed it
    os.write(fd, b"all\r")
    output += read_until(fd, b"")  # to the end
    _, status = os.waitpid(pid, 0)
    os.close(fd)

    assert os.waitstatus_to_exitcode(status) == 0, output.decode(errors="replace")
    assert (home / "code-worktrees" / "all").is_dir()
    assert f"worktrees_dir={home / 'code-worktrees' / 'all'}" in conf_lines(home)


def read_until(fd: int, wanted: bytes, timeout: float = 20) -> bytes:
    """What the pty prints until wanted shows up (b"": until the child closes it); fails the test after timeout."""
    out = b""
    deadline = time.monotonic() + timeout
    while not wanted or wanted not in out:
        ready, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
        assert ready, f"timed out waiting for {wanted!r}; got {out!r}"
        try:
            chunk = os.read(fd, 4096)
        except OSError:  # Linux: EIO once the child has exited
            chunk = b""
        if not chunk:
            assert not wanted, f"setup.sh ended before printing {wanted!r}; got {out!r}"
            return out
        out += chunk
    return out
