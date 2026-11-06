"""Tests for src/tower/cli.py, the `tower` command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tower import cli, events, merge

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
          "body": "Closes #5 and fixes #6", "isCrossRepository": False}},
        {{"number": 8, "title": "Typo", "headRefName": "typo", "mergedAt": "{merged}",
          "body": "", "isCrossRepository": False}},
        {{"number": 9, "title": "From a fork", "headRefName": "feat-7", "mergedAt": "{merged}",
          "body": "", "isCrossRepository": True}},
    ]))
elif args[0] == "api" and "issues/5/events" in args[2]:
    print("{reopened}")
elif args[0] == "api" and "issues/6/events" in args[2]:
    sys.exit("HTTP 404: Not Found")
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
    assert err.startswith(f"tower: {repo}: reopens of #6 not counted: gh api in {repo}: HTTP 404")
    lines = out.splitlines()
    assert lines[0].split() == [
        "PR", "start→merged", "active", "waiting", "interrupts", "resume", "follow-ups/report", "rework", "title",
    ]  # fmt: skip
    assert lines[1].split() == ["app#7", "2h00m", "4m", "10m", "1", "10m", "0.5", "1", "Add", "search"]
    assert "1 merged PR with no events (made outside tower): app#8" in out


@pytest.mark.parametrize(
    ("since", "message"),
    [("two weeks", "like 14d, 2w or 36h"), ("99999w", "longer than 3650 days"), ("999999999w", "longer than")],
)
def test_stats_rejects_a_since_it_cannot_use(since: str, message: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["stats", "--since", since])
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


def test_stats_warns_about_a_repo_whose_folder_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TOWER_DIR", str(tmp_path))
    gone = tmp_path / "deleted-repo"
    events.append(events.Event(iso(datetime.now(timezone.utc)), "prompt", "s-1", str(gone), None, "feat-1"), tmp_path)

    assert cli.main(["stats"]) == 0
    out, err = capsys.readouterr()
    assert f"skipped {gone}: the folder is gone" in err
    assert out == "No merged PR in the window has events.\n"


@pytest.fixture
def policy_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout of acme/widget as the current folder, with tower's files in a temporary folder; returns the
    policy file's path (not written yet)."""
    checkout = tmp_path / "widget"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "remote", "add", "origin", "https://github.com/acme/widget"], check=True
    )
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("TOWER_DIR", str(tmp_path / "tower"))
    monkeypatch.setenv("TOWER_CONF", str(tmp_path / "tower.conf"))
    return tmp_path / "tower" / "policy.toml"


def test_policy_shows_the_profile_for_this_repo_and_why(policy_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy_home.parent.mkdir()
    policy_home.write_text('default_profile = "work"\n[repos]\n"acme/*" = "personal"\n', encoding="utf-8")

    assert cli.main(["policy"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("tower policy for acme/widget: personal\n")
    assert f'why: [repos] "acme/*" in {policy_home} matches acme/widget\n' in out
    assert "  push             force  only force pushes and branch deletes ask\n" in out
    assert "  merge_to_branch  auto   merging a PR into any other branch\n" in out
    assert "  wip_limit        3      the default\n" in out
    assert "problems: none\n" in out
    assert "tower policy use <profile> [--repo]" in out


def test_policy_lists_the_files_problems(policy_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy_home.parent.mkdir()
    policy_home.write_text('default_profile = "sandbox"\n[profiles.sandbox]\npush = "never"\n', encoding="utf-8")

    assert cli.main(["policy"]) == 0
    out = capsys.readouterr().out
    assert "  push             every  every push asks\n" in out
    assert (
        "\nproblems:\n  - [profiles.sandbox] push = \"never\" isn't one of every, force, none, so it's every\n" in out
    )


def test_policy_use_sets_the_default_profile(policy_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["policy", "use", "personal"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("tower: default profile: work -> personal\n")
    assert "tower policy for acme/widget: personal\n" in out
    assert 'default_profile = "personal"' in policy_home.read_text(encoding="utf-8")


def test_policy_use_with_repo_sets_this_repos_profile(policy_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["policy", "use", "personal"]) == 0
    assert cli.main(["policy", "use", "sandbox", "--repo"]) == 0
    out = capsys.readouterr().out
    assert "tower: acme/widget: personal -> sandbox\n" in out
    assert '"acme/widget" = "sandbox"' in policy_home.read_text(encoding="utf-8")


def test_policy_use_refuses_an_unknown_profile(policy_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["policy", "use", "yolo"]) == 1
    assert capsys.readouterr().err == ('tower: "yolo" isn\'t a profile; the profiles are personal, sandbox, work\n')
    assert not policy_home.exists()


def test_policy_use_repo_needs_a_github_origin(
    policy_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli.main(["policy", "use", "sandbox", "--repo"]) == 1
    assert "isn't in a repo with a GitHub origin" in capsys.readouterr().err


# `tower merge`: a fake gh with one open PR, #5 (feat -> main); FAKE_GH_STACK=1 puts it in a stack whose merge never
# finishes. Each call is logged to FAKE_GH_LOG.
MERGE_GH = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
stacked = os.environ.get("FAKE_GH_STACK") == "1"
if args[:3] == ["pr", "view", "5"]:
    print(json.dumps({{"url": "https://github.com/acme/widget/pull/5"}}))
elif args[:2] == ["pr", "view"]:
    sys.exit("no pull requests found for branch \\"feat\\"")
elif args[:4] == ["api", "--hostname", "github.com", "repos/acme/widget/pulls/5"]:
    print(json.dumps({{"number": 5, "state": "open", "merged": False, "base": {{"ref": "main"}},
                      "head": {{"ref": "feat", "sha": "abc", "repo": {{"full_name": "acme/widget"}}}},
                      "stack": {{"base": {{"ref": "main"}}, "position": 1, "size": 2}} if stacked else None}}))
elif args[-6:-2] == ["-f", "state=open", "-f", "base=feat"] or args[-6:-2] == ["-f", "state=closed", "-f", "base=feat"]:
    print("[]")  # no PR is based on its branch, open or merged
elif args[:4] == ["api", "--hostname", "github.com", "repos/acme/widget"]:
    print(json.dumps({{"allow_merge_commit": True, "allow_squash_merge": True, "delete_branch_on_merge": False}}))
elif args[:2] == ["pr", "merge"]:
    print("Squashed and merged pull request acme/widget#5", file=sys.stderr)
elif "merge-async" in args[-1] or "PUT" in args:
    print(json.dumps({{"status": "pending", "details": {{"uuid": "u-1"}}}}))
else:
    sys.exit("fake gh: unexpected " + " ".join(args))
"""


@pytest.fixture
def merge_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fake gh for `tower merge` first on PATH; returns the log of its calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(MERGE_GH.format(python=sys.executable), encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.chdir(tmp_path)
    return tmp_path / "gh.log"


def test_merge_squash_merges_the_pr_with_gh_and_relays_what_gh_said(
    merge_gh: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["merge", "5"]) == 0

    out = capsys.readouterr().out
    assert out == "tower: Squashed and merged pull request acme/widget#5\ntower: merged PR #5 into main with a squash\n"
    calls = [json.loads(line) for line in merge_gh.read_text(encoding="utf-8").splitlines()]
    assert calls[-1] == ["pr", "merge", "5", "--squash", "--delete-branch", "--match-head-commit", "abc"]


def test_merge_exits_1_with_the_reason_when_nothing_was_merged(
    merge_gh: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["merge"]) == 1

    assert capsys.readouterr().err == 'tower: couldn\'t find the PR: no pull requests found for branch "feat"\n'


def test_merge_exits_3_when_github_is_still_merging_at_the_timeout(
    merge_gh: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKE_GH_STACK", "1")
    monkeypatch.setattr(merge, "MERGE_TIMEOUT_SECONDS", 0)

    assert cli.main(["merge", "5"]) == 3

    assert "run tower merge 5 again" in capsys.readouterr().err


@pytest.mark.parametrize("pr", ["#5", "five", "0", "-5", "５"])
def test_merge_takes_only_a_pr_number(pr: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["merge", pr])

    assert exited.value.code == 2
    assert "isn't a PR number" in capsys.readouterr().err
