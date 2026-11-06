"""Tests for src/tower/cli.py, the `tower` command."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tower import cli, events

PLUGIN_JSON = Path(__file__).resolve().parent.parent / "plugin" / ".claude-plugin" / "plugin.json"


def test_version_is_the_plugins_version() -> None:
    """tower has one version, the plugin's; the console script installed in the dev venv prints it."""
    version = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    proc = subprocess.run(
        [str(Path(sys.executable).parent / "tower"), "--version"], capture_output=True, text=True, check=True
    )
    assert proc.stdout == f"tower {version}\n"


def test_events_tail_prints_the_last_events_as_json_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TOWER_DIR", str(tmp_path))
    for i in range(5):
        event = events.Event("2026-11-06T04:12:33.120Z", "prompt", "s-1", None, None, None, {"i": i})
        events.append(event, tmp_path)

    assert cli.main(["events", "tail", "-n", "2"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["data"]["i"] for line in printed] == [3, 4]


def test_events_tail_with_no_log_says_where_it_looked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TOWER_DIR", str(tmp_path))

    assert cli.main(["events", "tail"]) == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert f"no events yet in {tmp_path / 'events.jsonl'}" in err


def test_events_tail_rejects_a_negative_count(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["events", "tail", "-n", "-1"])
    assert exit_info.value.code == 2
    assert "must be 0 or more" in capsys.readouterr().err
