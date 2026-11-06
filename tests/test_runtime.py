"""Tests for runtime slots: `tower env` gives each worktree a stable slot number and the variables a project's config
(~/.claude/tower/projects/<Project>.toml, under TOWER_DIR here) derives from it, so two workers' dev servers never
fight over one port.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from conftest import Fixture, git

from tower import cli

ROOT = Path(__file__).resolve().parent.parent


def write_config(repo: Fixture, text: str, project: str = "repo") -> Path:
    """The project's runtime config, where tower env looks for it."""
    path = repo.tower_dir / "projects" / f"{project}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tower_env(capsys: pytest.CaptureFixture[str], directory: Path, code: int = 0) -> tuple[str, str]:
    """Run `tower env --dir directory` in-process; its stdout and stderr."""
    assert cli.main(["env", "--dir", str(directory)]) == code
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_two_workers_get_different_ports_and_keep_them(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    write_config(repo, 'port_base = 3000\ndb_name = "poot_{slot}"\n')
    one = repo.add_worktree("feat-1")
    two = repo.add_worktree("feat-2")
    (two / "src").mkdir()  # any folder in the worktree

    assert tower_env(capsys, one)[0] == "export TOWER_SLOT=1\nexport PORT=3001\nexport DB_NAME=poot_1\n"
    assert tower_env(capsys, two / "src")[0] == "export TOWER_SLOT=2\nexport PORT=3002\nexport DB_NAME=poot_2\n"
    assert tower_env(capsys, one)[0].startswith("export TOWER_SLOT=1\n")  # stable on every later run
    assert tower_env(capsys, repo.main)[0] == "export TOWER_SLOT=0\nexport PORT=3000\nexport DB_NAME=poot_0\n"


def test_a_removed_worktree_frees_its_slot_without_moving_the_others(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(repo, "port_base = 3000\n")
    one = repo.add_worktree("feat-1")
    two = repo.add_worktree("feat-2")
    tower_env(capsys, one)
    tower_env(capsys, two)
    git(repo.main, "worktree", "remove", str(one))

    three = repo.add_worktree("feat-3")

    assert tower_env(capsys, two)[0].startswith("export TOWER_SLOT=2\n")
    assert tower_env(capsys, three)[0].startswith("export TOWER_SLOT=1\n")


def test_without_a_config_nothing_is_exported(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    out, err = tower_env(capsys, repo.add_worktree("feat-1"))

    assert out == ""
    assert f"no runtime config at {repo.tower_dir / 'projects' / 'repo.toml'}" in err
    assert not (repo.tower_dir / "slots").exists()  # no slot is taken for a project that doesn't use them


def test_extra_ports_env_and_the_seed_command(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    write_config(
        repo,
        "# Poot's dev servers\n"
        "port_base = 3000\n"
        "seed = 'cp -R {main}/data data'  # main-only data\n"
        "\n"
        "[ports]\n"
        "API_PORT = 8000\n"
        "\n"
        "[env]\n"
        'API_URL = "http://localhost:{port}/api"\n'
        "REDIS_DB = 7\n"
        'LABEL = "it\'s slot {slot} \\"quoted\\""\n',
    )
    one = repo.add_worktree("feat-1")

    out = tower_env(capsys, one)[0]

    assert out == (
        "export TOWER_SLOT=1\n"
        "export PORT=3001\n"
        "export API_PORT=8001\n"
        "export API_URL=http://localhost:3001/api\n"
        "export REDIS_DB=7\n"
        "export LABEL='it'\"'\"'s slot 1 \"quoted\"'\n"
        f"# seed (once, for what only the main checkout has): cp -R {repo.main}/data data\n"
    )
    exported = subprocess.run(
        ["sh", "-c", f'{out}\nprintf "%s|%s" "$LABEL" "$API_PORT"'], capture_output=True, text=True, check=True
    )
    assert exported.stdout == 'it\'s slot 1 "quoted"|8001'


def test_the_seed_command_quotes_a_main_checkout_path_with_spaces(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    spaced = tmp_path / "my code" / "repo"
    spaced.parent.mkdir()
    repo.main.rename(spaced)
    worktree = spaced / "worktrees" / "feat-1"
    git(spaced, "worktree", "add", "-q", "-b", "feat-1", str(worktree))
    write_config(repo, 'seed = "cp -R {main}/data data"\n')

    assert tower_env(capsys, spaced)[0] == "export TOWER_SLOT=0\n"  # the main checkout has the data: no seed line
    seed = tower_env(capsys, worktree)[0].splitlines()[-1]
    assert seed == f"# seed (once, for what only the main checkout has): cp -R '{spaced}'/data data"


@pytest.mark.parametrize(
    ("config", "error"),
    [
        ("port_base = 3000\nport_bsae = 3\n", "line 2: unknown setting port_bsae"),
        ("port_base = '3000'\n", "line 1: port_base must be a whole number"),
        ('db_name = "poot_{slt}"\n', "db_name: unknown placeholder {slt}"),
        ('db_name = "poot_{port}"\n', "db_name: {port} needs port_base"),
        ("[database]\n", "line 1: unknown table [database]"),
        ("[env]\nPORT = 1\n", "PORT is set twice"),
        ("[env]\nbad-name = 1\n", "line 2: isn't key = value"),
        ("port_base = 3000\nport_base = 4000\n", "line 2: port_base is set twice"),
        ("port_base = 65535\n", "PORT would be 65536"),
        ('db_name = "unterminated\n', "line 1: db_name's value isn't a quoted string or a whole number"),
        ('seed = "cp a b\\nrm -rf data"\n', "line 1: seed must be one line"),
    ],
)
def test_a_bad_config_says_what_and_where(
    repo: Fixture, capsys: pytest.CaptureFixture[str], config: str, error: str
) -> None:
    path = write_config(repo, config)

    out, err = tower_env(capsys, repo.add_worktree("feat-1"), code=1)

    assert out == ""
    assert f"tower: {path}" in err
    assert error in err


def test_project_mapping_picks_the_config(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    conf = Path(os.environ["TOWER_CONF"])
    conf.write_text(conf.read_text(encoding="utf-8") + "project.repo=Poot\n", encoding="utf-8")
    write_config(repo, "port_base = 5000\n", project="Poot")

    assert tower_env(capsys, repo.main)[0] == "export TOWER_SLOT=0\nexport PORT=5000\n"


def test_workers_starting_at_the_same_moment_never_share_a_slot(repo: Fixture) -> None:
    """Each session's hook asks for its slot as it starts; several at once must still get distinct slots."""
    write_config(repo, "port_base = 3000\n")
    paths = [repo.add_worktree(f"feat-{n}") for n in range(6)]
    run_tower = ROOT / "plugin" / "scripts" / "run-tower.py"

    procs = [
        subprocess.Popen(
            [sys.executable, "-I", str(run_tower), "env", "--dir", str(path)], stdout=subprocess.PIPE, text=True
        )
        for path in paths
    ]
    slots = [proc.communicate(timeout=60)[0].splitlines()[0] for proc in procs]

    assert sorted(slots) == [f"export TOWER_SLOT={n}" for n in range(1, 7)]


def test_a_damaged_slots_file_stops_instead_of_handing_out_slots_again(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(repo, "port_base = 3000\n")
    slots = repo.tower_dir / "slots" / "repo.json"
    slots.parent.mkdir(parents=True)
    slots.write_text("{not json", encoding="utf-8")

    err = tower_env(capsys, repo.add_worktree("feat-1"), code=1)[1]

    assert f"the slots file {slots} is damaged" in err
    assert slots.read_text(encoding="utf-8") == "{not json"


def free_port_block(size: int) -> int:
    """A base port whose next size ports were free a moment ago (good enough for a test on one machine)."""
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            base = int(probe.getsockname()[1])
        if base + size > 65535:
            continue
        try:
            for port in range(base, base + size + 1):
                with socket.socket() as check:
                    check.bind(("127.0.0.1", port))
        except OSError:
            continue
        return base
    raise RuntimeError("no free block of ports")


def test_two_workers_run_their_dev_servers_at_the_same_time(repo: Fixture) -> None:
    """The ticket's check: each worker starts the same dev server command, which reads PORT, from `tower env`'s
    exports; both serve at once, on different ports."""
    base = free_port_block(2)
    write_config(repo, f"port_base = {base}\n")
    run_tower = ROOT / "plugin" / "scripts" / "run-tower.py"
    servers = []
    try:
        for name in ("feat-1", "feat-2"):
            path = repo.add_worktree(name)
            (path / "whoami.txt").write_text(name, encoding="utf-8")
            command = (
                f'eval "$({sys.executable} -I {run_tower} env)" && '
                f'exec {sys.executable} -m http.server "$PORT" --bind 127.0.0.1'
            )
            servers.append(
                subprocess.Popen(["sh", "-c", command], cwd=path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            )
        served = {}
        for port in (base + 1, base + 2):
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/whoami.txt", timeout=2) as response:
                        served[port] = response.read().decode()
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.1)
        assert served == {base + 1: "feat-1", base + 2: "feat-2"}
        assert all(server.poll() is None for server in servers)  # neither died on a port clash
    finally:
        for server in servers:
            server.terminate()
            server.wait(timeout=10)
