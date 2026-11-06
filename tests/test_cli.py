"""Tests for src/tower/cli.py, the `tower` command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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


FAKE_GH = """#!{python}
import json, sys
args = sys.argv[1:]
if args[:2] == ["pr", "list"]:
    print(json.dumps([
        {{"number": 7, "title": "Add search", "headRefName": "feat-7", "mergedAt": "{merged}",
          "body": "Closes #5", "closingIssuesReferences": []}},
        {{"number": 8, "title": "Typo", "headRefName": "typo", "mergedAt": "{merged}",
          "body": "", "closingIssuesReferences": []}},
    ]))
elif args[0] == "api" and "issues/5/events" in args[2]:
    print("{reopened}")
else:
    sys.exit("fake gh: unexpected " + " ".join(args))
"""


def iso(when: datetime) -> str:
    """when as the log writes ts: ISO 8601 UTC with milliseconds and a Z."""
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_stats_prints_one_row_per_merged_pr_from_the_log_and_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "app"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    merged = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=1)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        FAKE_GH.format(python=sys.executable, merged=iso(merged), reopened=iso(merged + timedelta(hours=1))),
        encoding="utf-8",
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TOWER_DIR", str(tmp_path / "tower"))
    start = merged - timedelta(hours=2)
    for offset, event_type in [(0, "prompt"), (30, "stop"), (40, "prompt"), (60, "stop")]:
        when = iso(start + timedelta(minutes=offset))
        events.append(events.Event(when, event_type, "s-1", str(repo), str(repo), "feat-7"), tmp_path / "tower")

    assert cli.main(["stats", "--since", "3d"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    lines = out.splitlines()
    assert lines[0].split() == [
        "PR", "start→merged", "active", "waiting", "interrupts", "resume", "follow-ups/report", "rework", "title",
    ]  # fmt: skip
    assert lines[1].split() == ["app#7", "2h00m", "4m", "10m", "1", "10m", "0.5", "1", "Add", "search"]
    assert "1 merged PR with no events (made outside tower): app#8" in out


def test_stats_rejects_a_since_it_cannot_read(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["stats", "--since", "two weeks"])
    assert exit_info.value.code == 2
    assert "like 14d, 2w or 36h" in capsys.readouterr().err
