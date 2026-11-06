"""Tests for `tower state`: facts gathered from a real repo, worktrees, cards, sessions and events.

Each test builds a throwaway repo with worktrees, a ledger in a fake vault (through TOWER_CONF), and a fake `claude`
on PATH whose `agents --json` prints the sessions the test chose. The rules themselves are in test_state_rules.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from conftest import Fixture, git, interactive

from tower import cli, events, ledger

ROOT = Path(__file__).resolve().parent.parent
HUB_SCAN = ROOT / "plugin" / "skills" / "hub" / "scripts" / "hub-scan.sh"
PANE_TEST = ROOT / "pane" / "tests" / "tower-pane.test.tsx"


def tower_state(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, object]:
    """Run `tower state --json` in-process and parse what it prints."""
    assert cli.main(["state", "--json", *args]) == 0
    result: dict[str, object] = json.loads(capsys.readouterr().out)
    return result


def rows(result: dict[str, object]) -> dict[str, dict[str, object]]:
    """The worktree rows by label."""
    worktrees = result["worktrees"]
    assert isinstance(worktrees, list)
    return {row["label"]: row for row in worktrees}


def test_three_busy_workers_with_no_cards_hold_three_slots(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    paths = [repo.add_worktree(f"feat-{n}") for n in (1, 2, 3)]
    repo.sessions(*(interactive(f"w{n}", path, "busy") for n, path in enumerate(paths, 1)))

    result = tower_state(capsys)

    assert (result["slots"], result["wip_limit"], result["at_limit"]) == (3, 3, True)
    assert [row["status"] for row in rows(result).values()] == ["main", "running", "running", "running"]
    assert rows(result)["feat-1"]["reason"] == "no card yet, but session w1 is busy"


def test_a_busy_worker_with_a_stale_needs_you_card_is_running(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path = repo.add_worktree("feat-7")
    repo.write_card("feat-7", "needs-you", age_hours=3)
    repo.sessions(interactive("w7", path / "src", "busy"))  # a session in a subfolder still belongs to the worktree

    row = rows(tower_state(capsys))["feat-7"]

    assert (row["status"], row["holds_slot"]) == ("running", True)
    assert row["reason"] == "the card says needs-you but session w7 is busy: you already answered"
    assert row["card"] == {"status": "needs-you", "ticket": "#42", "updated": "2026-11-06T09:00", "pr": "none"}


def test_a_waiting_session_beats_a_running_card_and_is_named(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    path = repo.add_worktree("feat-8")
    repo.write_card("feat-8", "running")
    repo.sessions(interactive("w8", path, "waiting", waitingFor="approve Bash"))

    row = rows(tower_state(capsys))["feat-8"]

    assert (row["status"], row["holds_slot"]) == ("needs-you", True)
    assert row["reason"] == "session w8 is waiting on you (approve Bash); the card says running"
    assert row["sessions"] == [
        {"name": "w8", "kind": "interactive", "activity": "waiting", "waiting_for": "approve Bash"}
    ]


def test_unshipped_work_is_reported_apart_from_slots(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    repo.add_worktree("feat-1", commits=3)
    repo.write_card("feat-1", "blocked")
    repo.add_worktree("feat-2", commits=0)
    repo.write_card("feat-2", "done")

    result = tower_state(capsys)

    assert result["slots"] == 0
    assert result["unshipped"] == [{"label": "feat-1", "commits": 3}]
    assert result["base"] == "main"
    assert rows(result)["feat-2"]["status"] == "ready-to-clean"  # done, every commit on main, nothing uncommitted


def test_a_done_worktree_with_uncommitted_changes_is_not_ready_to_clean(
    repo: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path = repo.add_worktree("feat-2", commits=0)
    repo.write_card("feat-2", "done")
    (path / "notes.txt").write_text("not committed\n", encoding="utf-8")

    row = rows(tower_state(capsys))["feat-2"]

    assert (row["status"], row["dirty"]) == ("done", True)


def test_cards_without_a_worktree_are_orphans(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    repo.add_worktree("feat/9-x")
    repo.write_card("feat/9-x", "running")  # feat__9-x.md: claimed by the worktree
    repo.write_card("feat/3-gone", "done")
    (repo.ledger / "_backlog.md").write_text("- [ ] an idea\n", encoding="utf-8")

    result = tower_state(capsys)

    assert rows(result)["feat/9-x"]["status"] == "running"
    assert result["orphan_cards"] == [
        {"name": "feat__3-gone", "status": "done", "path": str(repo.ledger / "feat__3-gone.md")}
    ]


def test_without_claude_the_event_log_stands_in_for_live_sessions(
    repo: Fixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    busy_path = repo.add_worktree("feat-1")
    waiting_path = repo.add_worktree("feat-2")
    ended_path = repo.add_worktree("feat-3")
    repo.write_card("feat-3", "needs-you")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # git stays, the fake claude goes
    now = time.time()

    def log(session: str, place: Path, event_type: str, minutes_ago: float, **data: object) -> None:
        """Record one event for session in place, minutes_ago before now."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - minutes_ago * 60))
        events.append(
            events.Event(ts, event_type, session, str(repo.main), str(place), None, dict(data)), repo.tower_dir
        )

    log("s-busy", busy_path, "prompt", 5)
    log("s-wait", waiting_path, "prompt", 10)
    log("s-wait", waiting_path, "needs-you", 2, notification_type="permission_prompt")
    log("s-old", waiting_path, "prompt", 60 * 25)  # older than a day: may have crashed, ignored
    log("s-end", ended_path, "prompt", 9)
    log("s-end", ended_path, "session-end", 1)

    result = tower_state(capsys)

    assert result["sessions_source"] == "events"
    assert result["notes"] == ["claude CLI not on PATH, so live sessions weren't read"]
    statuses = {label: row["status"] for label, row in rows(result).items()}
    assert statuses == {"main": "main", "feat-1": "running", "feat-2": "needs-you", "feat-3": "needs-you"}
    assert (
        rows(result)["feat-3"]["reason"] == "the card says needs-you; no session here is waiting on you on this machine"
    )
    assert result["slots"] == 3


def test_an_idle_prompt_notification_is_not_waiting_on_you(
    repo: Fixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo.add_worktree("feat-1")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    event = events.Event(
        ts, "needs-you", "s-1", str(repo.main), str(path), "feat-1", {"notification_type": "idle_prompt"}
    )
    events.append(event, repo.tower_dir)

    assert rows(tower_state(capsys))["feat-1"]["status"] == "no-card"


def test_with_no_session_data_at_all_it_groups_from_cards(
    repo: Fixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.add_worktree("feat-1")
    repo.write_card("feat-1", "running")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert cli.main(["state"]) == 0
    text = capsys.readouterr().out

    assert "(no live session data: grouped from cards alone)" in text
    assert "NOTE: claude CLI not on PATH, so live sessions weren't read" in text


def test_a_broken_claude_agents_degrades_with_its_reason(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    repo.agents_json.write_text("not json", encoding="utf-8")
    result = tower_state(capsys)
    assert result["sessions_source"] == "none"
    assert result["notes"] == ["claude agents --json printed something unexpected (its format may have changed)"]


def test_without_a_ledger_it_still_reports_sessions(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = repo.add_worktree("feat-1")
    repo.sessions(interactive("w1", path, "busy"))
    (tmp_path / "tower.conf").write_text("wip_limit=4\n", encoding="utf-8")

    result = tower_state(capsys)

    assert result["ledger"] is None
    assert result["notes"] == [
        f"no projects_dir in {tmp_path / 'tower.conf'} (run setup.sh on this machine), so no cards were read"
    ]
    assert (result["slots"], result["wip_limit"]) == (1, 4)


def test_outside_a_repo_it_says_so_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["state", "--repo", str(tmp_path)]) == 1
    assert f"tower: not a git repo: {tmp_path}" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("branch", "card"), [("feat-1", "feat-1.md"), ("feat/42-x", "feat__42-x.md"), ("a/b/c", "a__b__c.md")]
)
def test_a_slash_in_a_branch_becomes_a_double_underscore_in_its_card_name(
    repo: Fixture, branch: str, card: str
) -> None:
    """The names cards have always had (ledger-lib.sh's, before the core): a "/" would make a subfolder."""
    assert ledger.card_name(branch, "", repo.main) == card


def test_a_detached_worktree_finds_its_card(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    path = repo.add_worktree("feat-1")
    git(path, "checkout", "-q", "--detach")
    short = git(path, "rev-parse", "--short", "HEAD").strip()
    (repo.ledger / f"detached-{short}.md").write_text("---\nstatus: blocked\n---\n", encoding="utf-8")

    row = rows(tower_state(capsys))[f"(detached {git(path, 'rev-parse', 'HEAD')[:7]})"]

    assert row["status"] == "blocked"


def hub_and_pane_fixture(repo: Fixture, tmp_path: Path) -> str:
    """Run hub-scan.sh --no-github on the fixture both the hub and the pane are tested with; paths shortened.

    Three workers, each a case where the hub and the pane used to disagree: a busy one with no card, a busy one whose
    card still says needs-you, and a waiting one whose card says running.
    """
    feat1, feat7, feat8 = repo.add_worktree("feat-1"), repo.add_worktree("feat-7"), repo.add_worktree("feat-8")
    repo.write_card("feat-7", "needs-you", ticket="#7")
    repo.write_card("feat-8", "running", ticket="#8")
    repo.sessions(
        interactive("hub-1", repo.main, "idle"),
        interactive("w1", feat1, "busy"),
        interactive("w7", feat7, "busy"),
        interactive("w8", feat8, "waiting", waitingFor="approve Bash"),
    )
    proc = subprocess.run(["bash", str(HUB_SCAN), "--no-github"], capture_output=True, text=True, check=True)
    report = proc.stdout
    for long, short in ((repo.ledger, "/vault/repo/Ledger"), (repo.main, "/p")):
        report = report.replace(os.path.realpath(long), short).replace(str(long), short)
    return report


def test_the_hub_and_the_pane_are_tested_on_the_same_report(repo: Fixture, tmp_path: Path) -> None:
    """hub-scan.sh's output for the shared fixture is, byte for byte, the report the pane's test feeds the pane
    (HUB_AND_PANE in pane/tests/tower-pane.test.tsx), so the pane test proves the pane shows the hub's statuses.
    If the report's format changes on purpose, paste the new output there."""
    report = hub_and_pane_fixture(repo, tmp_path)

    pane_test = PANE_TEST.read_text(encoding="utf-8")
    start = pane_test.index("const HUB_AND_PANE = `") + len("const HUB_AND_PANE = `")
    assert report == pane_test[start : pane_test.index("`", start)]
    assert "- feat-1 | status: running | card: none" in report
    assert "- feat-7 | status: running | card: needs-you" in report
    assert "- feat-8 | status: needs-you | card: running" in report
    assert "WIP: 3 of 3 (execution slots: running + needs-you, main excluded) — AT LIMIT" in report


def test_just_after_midnight_on_the_1st_last_months_events_still_count(tmp_path: Path) -> None:
    """The log rotates monthly, so the 24h window can reach into last month's file."""
    from datetime import datetime, timezone

    from tower import state

    worktree = str(tmp_path / "wt")
    rotated = tmp_path / "events-2026-11.jsonl"
    late = events.Event("2026-11-30T23:50:00.000Z", "prompt", "s-late", "/r", worktree, "feat-1")
    rotated.write_text(late.to_json() + "\n", encoding="utf-8")
    events.append(events.Event("2026-12-01T00:10:00.000Z", "stop", "s-early", "/r", worktree, "feat-1"), tmp_path)

    now = datetime(2026, 12, 1, 0, 30, tzinfo=timezone.utc).timestamp()
    found = state.sessions_from_events(tmp_path, [os.path.realpath(worktree)], now)

    activities = {s.name: s.activity for s in found[os.path.realpath(worktree)]}
    assert activities == {"s-late": "busy", "s-early": "idle"}


def test_a_pipe_in_a_card_never_splits_a_report_row(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    """An agent that copied the status-card template's `status: running | needs-you | ...` line literally."""
    repo.add_worktree("feat-1")
    repo.write_card("feat-1", "running | needs-you | waiting-ci | blocked | done")

    assert cli.main(["state"]) == 0
    row = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("- feat-1 | status:"))

    fields = row.split(" | ")
    assert fields[1:3] == ["status: no-card", "card: running / needs-you / waiting-ci / blocked / done"]
    assert len(fields) == 7  # label, status, card, ticket, updated, reason, path


def test_an_empty_project_mapping_falls_back_to_the_repo_name(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """As ledger-lib.sh's tower_conf_get does: an empty value means the default."""
    conf = tmp_path / "tower.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + "project.repo=\n", encoding="utf-8")
    assert tower_state(capsys)["ledger"] == str(repo.ledger)
