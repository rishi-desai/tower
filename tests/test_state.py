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
RUN_TOWER = ROOT / "plugin" / "scripts" / "run-tower.py"
PANE_FIXTURE = ROOT / "plugin" / "tests" / "fixtures" / "tower-state.ts"


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


def test_a_merged_pr_is_ready_to_clean_only_when_the_worktree_has_nothing_past_its_head(
    repo: Fixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Squash-merged branches: none of their commits are on main, so the PR's head commit (headRefOid) is what the
    worktree's HEAD is compared with. A head this clone doesn't have can't be compared, so it isn't ready-to-clean."""
    heads = {}
    for branch in ("squashed", "more-work", "head-elsewhere", "pulled-main", "not-a-sha"):
        path = repo.add_worktree(branch, commits=2)
        repo.write_card(branch, "done")
        heads[branch] = git(path, "rev-parse", "HEAD").strip()
    git(repo.main / "worktrees" / "more-work", "commit", "-q", "--allow-empty", "-m", "after the merge")
    # main moves on and pulled-main merges it in: the merge commit is past the PR's head and may hold conflict fixes,
    # so it stays (a cautious "no", not a lost commit)
    git(repo.main, "commit", "-q", "--allow-empty", "-m", "squashed PRs")
    git(repo.main / "worktrees" / "pulled-main", "merge", "-q", "--no-edit", "main")
    heads["head-elsewhere"] = "0123456789abcdef0123456789abcdef01234567"
    heads["not-a-sha"] = "--all"  # never reaches git as an option
    listed = [
        {
            "number": n,
            "state": "MERGED",
            "headRefName": b,
            "headRefOid": heads[b],
            "url": f"https://github.com/o/r/pull/{n}",
        }
        for n, b in enumerate(heads, start=7)
    ]
    gh = tmp_path / "bin" / "gh"
    gh.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(listed)}\nEOF\n", encoding="utf-8")
    gh.chmod(0o755)

    found = rows(tower_state(capsys, "--pr"))

    assert (found["squashed"]["status"], found["squashed"]["reason"]) == ("ready-to-clean", "PR #7 is merged")
    assert (found["more-work"]["status"], found["more-work"]["reason"]) == (
        "done",
        "the card says done; PR #8 is merged but the worktree has 1 commit the PR doesn't include",
    )
    assert (found["head-elsewhere"]["status"], found["head-elsewhere"]["reason"]) == (
        "done",
        "the card says done; PR #9 is merged but the worktree couldn't be checked for commits the PR doesn't include",
    )
    assert (found["pulled-main"]["status"], found["pulled-main"]["reason"]) == (
        "done",
        "the card says done; PR #10 is merged but the worktree has 2 commits the PR doesn't include",
    )
    assert found["not-a-sha"]["reason"] == (
        "the card says done; PR #11 is merged but the worktree couldn't be checked for commits the PR doesn't include"
    )
    not_a_sha_pr = found["not-a-sha"]["pr"]
    assert isinstance(not_a_sha_pr, dict) and not_a_sha_pr["head_oid"] is None


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


def shared_fixture(repo: Fixture) -> None:
    """The repo both the hub and the pane are tested on: one worker for each way a status can be decided.

    feat-1 is busy with no card; feat-3's card says needs-you and no session contradicts it; feat-5 has an idle
    session and no card; feat-7 is busy with a stale needs-you card (you already answered); feat-8 waits on an
    approval and feat-9 on a question, both with running cards. feat/2-gone's card outlived its worktree. The
    machine's policy is the personal profile.
    """
    repo.tower_dir.mkdir(exist_ok=True)
    (repo.tower_dir / "policy.toml").write_text('default_profile = "personal"\n', encoding="utf-8")
    paths = {name: repo.add_worktree(name) for name in ("feat-1", "feat-3", "feat-5", "feat-7", "feat-8", "feat-9")}
    repo.write_card("feat-3", "needs-you", ticket="#3")
    repo.write_card("feat-7", "needs-you", ticket="#7")
    repo.write_card("feat-8", "running", ticket="#8")
    repo.write_card("feat-9", "running", ticket="#9")
    repo.write_card("feat/2-gone", "done", ticket="#2")
    repo.sessions(
        interactive("hub-1", repo.main, "idle"),
        interactive("w1", paths["feat-1"], "busy"),
        interactive("w5", paths["feat-5"], "idle"),
        interactive("w7", paths["feat-7"], "busy"),
        interactive("w8", paths["feat-8"], "waiting", waitingFor="approve Bash"),
        interactive("w9", paths["feat-9"], "waiting", waitingFor="input needed"),
    )


def shortened(value: object, repo: Fixture) -> object:
    """value with the fixture's temporary paths replaced by the short ones the pane's fixture uses."""
    if isinstance(value, dict):
        return {key: shortened(item, repo) for key, item in value.items()}
    if isinstance(value, list):
        return [shortened(item, repo) for item in value]
    if isinstance(value, str):
        for long, short in ((repo.ledger, "/vault/repo/Ledger"), (repo.main, "/p"), (repo.tower_dir, "/tower")):
            value = value.replace(os.path.realpath(long), short).replace(str(long), short)
    return value


PANE_FIXTURE_START = "export const SHARED: TowerState = "


def test_the_pane_is_tested_on_tower_states_own_output(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    """`tower state --json` for the shared fixture is exactly what the pane's tests feed the pane (SHARED in
    plugin/tests/fixtures/tower-state.ts), so the pane test proves the pane draws what the core decided. If the JSON
    changes on purpose, paste the new output (printed below on failure) there."""
    shared_fixture(repo)
    produced = shortened(tower_state(capsys), repo)

    fixture = PANE_FIXTURE.read_text(encoding="utf-8")
    pasted = json.loads(fixture[fixture.index(PANE_FIXTURE_START) + len(PANE_FIXTURE_START) :])
    assert produced == pasted, "paste this into SHARED:\n" + json.dumps(produced, indent=2)
    statuses = {row["label"]: row["status"] for row in pasted["worktrees"]}
    assert statuses == {
        "main": "main",
        "feat-1": "running",
        "feat-3": "needs-you",
        "feat-5": "no-card",
        "feat-7": "running",
        "feat-8": "needs-you",
        "feat-9": "needs-you",
    }


def test_the_panes_issue_limit_matches_tower_scans() -> None:
    """The pane says "+ N or more" when `tower scan` listed as many issues as its `gh issue list --limit`
    allows."""
    from tower import scan

    pane = (ROOT / "plugin" / "hooks" / "register.tsx").read_text(encoding="utf-8")
    assert int(pane.split("const SCAN_ISSUE_LIMIT = ")[1].split(" ")[0]) == scan.ISSUE_LIMIT


def test_the_hub_groups_the_shared_fixture_as_the_pane_does(repo: Fixture) -> None:
    """`tower scan`'s GROUPS (what the hub reports from) put the same worktrees under NEEDS YOU and RUNNING as the
    statuses in the pane's fixture: the hub and the pane can't disagree."""
    shared_fixture(repo)
    report = subprocess.run(
        ["python3", "-I", str(RUN_TOWER), "scan", "--no-github"], capture_output=True, text=True, check=True
    ).stdout

    groups = report[report.index("== GROUPS") :].split("\n\n")[0].splitlines()
    rows_in = {
        title: [line[2:].split(" | ")[0] for line in groups[groups.index(title) + 1 : groups.index(after)]]
        for title, after in (("NEEDS YOU", "BLOCKED"), ("RUNNING", "WAITING ON CI OR REVIEW"))
    }
    assert rows_in == {"NEEDS YOU": ["feat-3", "feat-8", "feat-9"], "RUNNING": ["feat-1", "feat-7"]}
    assert "WIP: 5 of 3 (execution slots: running + needs-you, main excluded) — AT LIMIT" in report


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
    """As setup.sh reads the config (ledger.conf_get): an empty value means the default."""
    conf = tmp_path / "tower.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + "project.repo=\n", encoding="utf-8")
    assert tower_state(capsys)["ledger"] == str(repo.ledger)


def test_the_policy_sets_the_wip_limit_and_is_reported(repo: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    """A profile's wip_limit outranks tower.conf's; the policy and any problems in its file are in the JSON."""
    repo.tower_dir.mkdir(exist_ok=True)
    (repo.tower_dir / "policy.toml").write_text(
        'default_profile = "work"\n[profiles.work]\nwip_limit = 5\ncomment = "auto"\n', encoding="utf-8"
    )

    result = tower_state(capsys)

    assert result["wip_limit"] == 5
    policy = result["policy"]
    assert isinstance(policy, dict)
    assert (policy["profile"], policy["push"], len(policy["problems"])) == ("work", "every", 1)
    assert "the policy file has 1 problem; `tower policy` lists them" in result["notes"]  # type: ignore[operator]


def test_the_hub_scan_names_the_policy(repo: Fixture) -> None:
    shared_fixture(repo)
    report = subprocess.run(
        ["python3", "-I", str(RUN_TOWER), "scan", "--no-github"], capture_output=True, text=True, check=True
    ).stdout

    policy_line = f"Policy: personal (default_profile in {repo.tower_dir / 'policy.toml'}) | push: force"
    assert (
        f"\n{policy_line} | merge_to_main: ask | merge_to_branch: auto | comments: ask | issue_writes: auto" in report
    )
    assert "PR approval" not in report
