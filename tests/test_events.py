"""Tests for src/tower/events.py, the append-only event log every tower hook writes to."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tower import events


def make_event(ts: str = "2026-11-06T04:12:33.120Z", type: str = "prompt", **data: object) -> events.Event:
    """An event in the tower repo's feature worktree; only ts, type and data vary between tests."""
    return events.Event(
        ts=ts,
        type=type,
        session_id="s-1",
        repo="/code/tower",
        worktree="/code/tower/worktrees/feat-22",
        branch="feat-22",
        data=dict(data),
    )


def test_append_writes_one_json_line_per_event_with_the_common_fields_first(tmp_path: Path) -> None:
    events.append(make_event(type="session-start", source="startup"), tmp_path)
    events.append(make_event(type="prompt", prompt_chars=12), tmp_path)

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines[0] == (
        '{"ts":"2026-11-06T04:12:33.120Z","type":"session-start","session_id":"s-1","repo":"/code/tower",'
        '"worktree":"/code/tower/worktrees/feat-22","branch":"feat-22","data":{"source":"startup"}}'
    )
    assert json.loads(lines[1])["data"] == {"prompt_chars": 12}
    assert len(lines) == 2


WRITER = """
import sys
from pathlib import Path
from tower import events

writer, count, size, directory = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4])
for i in range(count):
    event = events.Event(
        ts="2026-11-06T04:12:33.120Z", type="prompt", session_id=writer, repo=None, worktree=None, branch=None,
        data={"i": i, "padding": writer * size},
    )
    events.append(event, directory)
"""


def test_concurrent_appends_from_several_processes_never_interleave_lines(tmp_path: Path) -> None:
    writers, per_writer, size = 8, 50, 20_000  # 20 KB lines: far bigger than any atomic-write guarantee
    procs = [
        subprocess.Popen([sys.executable, "-c", WRITER, f"w{n}", str(per_writer), str(size), str(tmp_path)])
        for n in range(writers)
    ]
    assert [proc.wait(timeout=60) for proc in procs] == [0] * writers

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == writers * per_writer
    seen: dict[str, list[int]] = {}
    for line in lines:
        record = json.loads(line)  # a torn or interleaved line fails here
        assert record["data"]["padding"] == record["session_id"] * size
        seen.setdefault(record["session_id"], []).append(record["data"]["i"])
    # every writer's events all arrived, in the order that writer appended them
    assert seen == {f"w{n}": list(range(per_writer)) for n in range(writers)}


def test_an_event_from_a_new_month_moves_the_old_month_to_its_own_file(tmp_path: Path) -> None:
    events.append(make_event(ts="2026-10-31T23:59:59.000Z", type="prompt"), tmp_path)
    events.append(make_event(ts="2026-10-31T23:59:59.500Z", type="stop"), tmp_path)
    events.append(make_event(ts="2026-11-01T00:00:00.000Z", type="answer"), tmp_path)

    october = (tmp_path / "events-2026-10.jsonl").read_text(encoding="utf-8").splitlines()
    current = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["type"] for line in october] == ["prompt", "stop"]
    assert [json.loads(line)["type"] for line in current] == ["answer"]


def test_an_event_from_an_earlier_month_does_not_rotate(tmp_path: Path) -> None:
    events.append(make_event(ts="2026-11-01T00:00:00.000Z", type="prompt"), tmp_path)
    events.append(make_event(ts="2026-10-31T23:59:59.000Z", type="stop"), tmp_path)  # the clock stepped back

    assert sorted(p.name for p in tmp_path.glob("events*.jsonl")) == ["events.jsonl"]
    assert len((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_rotation_never_overwrites_an_existing_month_file(tmp_path: Path) -> None:
    (tmp_path / "events-2026-10.jsonl").write_text("kept\n", encoding="utf-8")
    events.append(make_event(ts="2026-10-30T10:00:00.000Z", type="prompt"), tmp_path)
    events.append(make_event(ts="2026-11-02T10:00:00.000Z", type="answer"), tmp_path)

    assert (tmp_path / "events-2026-10.jsonl").read_text(encoding="utf-8") == "kept\n"
    assert json.loads((tmp_path / "events-2026-10.2.jsonl").read_text(encoding="utf-8"))["type"] == "prompt"


def test_tail_returns_the_last_n_lines_oldest_first(tmp_path: Path) -> None:
    for i in range(15):
        events.append(make_event(type="prompt", i=i), tmp_path)

    assert [json.loads(line)["data"]["i"] for line in events.tail(tmp_path, 3)] == [12, 13, 14]
    assert len(events.tail(tmp_path, 100)) == 15


def test_tail_of_a_missing_log_is_empty(tmp_path: Path) -> None:
    assert events.tail(tmp_path / "nowhere", 10) == []
