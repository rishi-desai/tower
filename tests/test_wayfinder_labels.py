"""Tests for plugin/skills/hub/scripts/wayfinder-labels.py, run against a fake `gh` on PATH."""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "plugin/skills/hub/scripts/wayfinder-labels.py"
WAYFINDER_LABELS = [
    "wayfinder:map",
    "wayfinder:research",
    "wayfinder:prototype",
    "wayfinder:grilling",
    "wayfinder:task",
]

# A stand-in for gh: `label list` returns $FAKE_GH_LABELS (one name per line) and, like the real gh,
# returns only the first 30 unless --limit says otherwise. Every call is appended to $FAKE_GH_LOG.
FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    with open(os.environ["FAKE_GH_LOG"], "a") as log:
        log.write(json.dumps(args) + "\\n")
    if os.environ.get("FAKE_GH_FAIL"):
        print("no git remotes found", file=sys.stderr)
        sys.exit(1)
    if args[:2] == ["label", "list"]:
        limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 30
        names = [n for n in os.environ.get("FAKE_GH_LABELS", "").split("\\n") if n][:limit]
        print(json.dumps([{"name": n} for n in names]))
    """
)


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a fake gh first on PATH; return the file it logs its calls to."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "gh.log"
    log.touch()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    return log


def run_script(existing: list[str], path: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run wayfinder-labels.py with EXISTING as the repo's labels (and PATH, if given)."""
    env = {**os.environ, "FAKE_GH_LABELS": "\n".join(existing)}
    if path is not None:
        env["PATH"] = path
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env, check=False)


def created_labels(log: Path) -> list[str]:
    """Label names the fake gh was asked to create, in order."""
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return [call[2] for call in calls if call[:2] == ["label", "create"]]


def printed_labels(stdout: str) -> list[str]:
    """Label names in the script's printed `gh label create <name> ...` commands, in order."""
    return [shlex.split(line)[3] for line in stdout.splitlines()]


def test_prints_a_create_command_for_every_label_when_none_exist(fake_gh: Path) -> None:
    result = run_script(["bug", "ready-for-agent"])
    assert result.returncode == 0, result.stderr
    assert printed_labels(result.stdout) == WAYFINDER_LABELS


def test_prints_only_the_missing_labels(fake_gh: Path) -> None:
    result = run_script(["wayfinder:map", "wayfinder:task", "bug"])
    assert result.returncode == 0, result.stderr
    assert printed_labels(result.stdout) == [
        "wayfinder:research",
        "wayfinder:prototype",
        "wayfinder:grilling",
    ]


def test_prints_nothing_when_all_five_exist(fake_gh: Path) -> None:
    result = run_script(["bug", *WAYFINDER_LABELS])
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_sees_labels_past_gh_default_limit_of_30(fake_gh: Path) -> None:
    result = run_script([f"filler-{i}" for i in range(40)] + WAYFINDER_LABELS)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_printed_commands_run_as_is_and_create_the_labels(fake_gh: Path) -> None:
    printed = run_script([]).stdout
    subprocess.run(["bash", "-c", printed], check=True, env=os.environ.copy())
    assert created_labels(fake_gh) == WAYFINDER_LABELS


def test_never_creates_labels_itself(fake_gh: Path) -> None:
    run_script([])
    assert created_labels(fake_gh) == []


def test_fails_loudly_when_gh_fails(fake_gh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_GH_FAIL", "1")
    result = run_script([])
    assert result.returncode != 0
    assert result.stdout == ""
    assert "no git remotes found" in result.stderr


def test_matches_existing_labels_case_insensitively(fake_gh: Path) -> None:
    result = run_script(
        ["Wayfinder:Map", "WAYFINDER:RESEARCH", "wayfinder:prototype", "wayfinder:grilling", "wayfinder:task"]
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_says_so_in_one_line_when_gh_is_missing(tmp_path: Path) -> None:
    result = run_script([], path=str(tmp_path))
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "gh (GitHub CLI) is not installed or not on PATH\n"
