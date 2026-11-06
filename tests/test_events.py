"""Tests for src/tower/events.py, the append-only event log every tower hook writes to."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tower import events


def make_event(ts: str = "2026-11-06T04:12:33.120Z", event_type: str = "prompt", **data: object) -> events.Event:
    """An event in the tower repo's feature worktree; only ts, type and data vary between tests."""
    return events.Event(
        ts=ts,
        type=event_type,
        session_id="s-1",
        repo="/code/tower",
        worktree="/code/tower/worktrees/feat-22",
        branch="feat-22",
        data=dict(data),
    )


def test_append_writes_one_json_line_per_event_with_the_common_fields_first(tmp_path: Path) -> None:
    events.append(make_event(event_type="session-start", source="startup"), tmp_path)
    events.append(make_event(event_type="prompt", prompt_chars=12), tmp_path)

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines[0] == (
        '{"ts":"2026-11-06T04:12:33.120Z","type":"session-start","session_id":"s-1","repo":"/code/tower",'
        '"worktree":"/code/tower/worktrees/feat-22","branch":"feat-22","data":{"source":"startup"}}'
    )
    assert json.loads(lines[1])["data"] == {"prompt_chars": 12}
    assert len(lines) == 2


WRITER = """
import sys
from datetime import datetime, timezone
from pathlib import Path
import pytest

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
    events.append(make_event(ts="2026-10-31T23:59:59.000Z", event_type="prompt"), tmp_path)
    events.append(make_event(ts="2026-10-31T23:59:59.500Z", event_type="stop"), tmp_path)
    events.append(make_event(ts="2026-11-01T00:00:00.000Z", event_type="answer"), tmp_path)

    october = (tmp_path / "events-2026-10.jsonl").read_text(encoding="utf-8").splitlines()
    current = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["type"] for line in october] == ["prompt", "stop"]
    assert [json.loads(line)["type"] for line in current] == ["answer"]


def test_an_event_from_an_earlier_month_does_not_rotate(tmp_path: Path) -> None:
    events.append(make_event(ts="2026-11-01T00:00:00.000Z", event_type="prompt"), tmp_path)
    events.append(make_event(ts="2026-10-31T23:59:59.000Z", event_type="stop"), tmp_path)  # the clock stepped back

    assert sorted(p.name for p in tmp_path.glob("events*.jsonl")) == ["events.jsonl"]
    assert len((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_rotation_never_overwrites_an_existing_month_file(tmp_path: Path) -> None:
    (tmp_path / "events-2026-10.jsonl").write_text("kept\n", encoding="utf-8")
    events.append(make_event(ts="2026-10-30T10:00:00.000Z", event_type="prompt"), tmp_path)
    events.append(make_event(ts="2026-11-02T10:00:00.000Z", event_type="answer"), tmp_path)

    assert (tmp_path / "events-2026-10.jsonl").read_text(encoding="utf-8") == "kept\n"
    assert json.loads((tmp_path / "events-2026-10.2.jsonl").read_text(encoding="utf-8"))["type"] == "prompt"


def test_tail_returns_the_last_n_lines_oldest_first(tmp_path: Path) -> None:
    for i in range(15):
        events.append(make_event(event_type="prompt", i=i), tmp_path)

    assert [json.loads(line)["data"]["i"] for line in events.tail(tmp_path, 3)] == [12, 13, 14]
    assert len(events.tail(tmp_path, 100)) == 15


def test_tail_of_a_missing_log_is_empty(tmp_path: Path) -> None:
    assert events.tail(tmp_path / "nowhere", 10) == []


def test_a_log_with_a_damaged_first_line_still_rotates_by_its_last_write(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text('{"ts":"2026-10-0\n', encoding="utf-8")  # torn first line
    october = datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
    os.utime(log, (october, october))
    events.append(make_event(ts="2026-11-02T10:00:00.000Z", event_type="answer"), tmp_path)

    assert (tmp_path / "events-2026-10.jsonl").read_text(encoding="utf-8") == '{"ts":"2026-10-0\n'
    assert json.loads(log.read_text(encoding="utf-8"))["type"] == "answer"


def set_mtime(path: Path, year: int, month: int) -> None:
    """Pretend path was last written in the given month (UTC)."""
    when = datetime(year, month, 20, tzinfo=timezone.utc).timestamp()
    os.utime(path, (when, when))


@pytest.mark.parametrize(
    "first_line",
    [b'{"ts":null}\n', b'{"ts":123}\n', b'{"ts":"yesterday"}\n', b"\xff\xfe not utf-8\n"],
    ids=["null-ts", "number-ts", "word-ts", "not-utf8"],
)
def test_a_first_line_without_a_readable_month_falls_back_to_the_last_write(tmp_path: Path, first_line: bytes) -> None:
    log = tmp_path / "events.jsonl"
    log.write_bytes(first_line)
    set_mtime(log, 2026, 10)
    events.append(make_event(ts="2026-11-02T10:00:00.000Z", event_type="answer"), tmp_path)

    assert (tmp_path / "events-2026-10.jsonl").read_bytes() == first_line
    assert json.loads(log.read_text(encoding="utf-8"))["type"] == "answer"


def test_tail_skips_over_undecodable_bytes_instead_of_crashing(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").write_bytes(b'\xff\xfe junk\n{"type":"stop"}\n')

    assert events.tail(tmp_path, 1) == ['{"type":"stop"}']


def test_an_unpaired_surrogate_in_a_payload_is_still_logged(tmp_path: Path) -> None:
    events.append(make_event(event_type="needs-you", message="cut emoji \ud83d"), tmp_path)

    [line] = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["data"]["message"] == "cut emoji \ud83d"


def test_read_all_returns_every_month_in_ts_order_and_counts_damaged_lines(tmp_path: Path) -> None:
    events.append(make_event(ts="2026-10-30T10:00:00.000Z", event_type="prompt"), tmp_path)
    events.append(make_event(ts="2026-11-02T10:00:01.000Z", event_type="stop"), tmp_path)
    events.append(make_event(ts="2026-11-02T10:00:00.000Z", event_type="answer"), tmp_path)  # landed out of ts order
    with open(tmp_path / "events.jsonl", "ab") as log:
        log.write(b'{"ts":"2026-11-0\n\xff\xfe junk\n[1]\n')  # torn, not UTF-8, not an object

    read, damaged = events.read_all(tmp_path)

    assert [(event.ts, event.type) for event in read] == [
        ("2026-10-30T10:00:00.000Z", "prompt"),
        ("2026-11-02T10:00:00.000Z", "answer"),
        ("2026-11-02T10:00:01.000Z", "stop"),
    ]
    assert read[0] == make_event(ts="2026-10-30T10:00:00.000Z", event_type="prompt")
    assert damaged == 3


def test_read_all_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert events.read_all(tmp_path / "nowhere") == ([], 0)
