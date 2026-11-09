"""Tests for `tower doctor`: each check, given a broken dependency, fails (or warns) and names the fix.

The tools doctor runs (orca, gh, claude, git-spice, mise) are fake shell scripts in a bin folder that is the whole
PATH, apart from git, so nothing on this machine is asked. tower.conf and the tower folder are temp files.
"""

from __future__ import annotations

import os
import shutil
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import git

from tower import cli, doctor, events, hook_events


@pytest.fixture
def bin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty bin folder that is all of PATH, except git (linked in, for the repo checks)."""
    folder = tmp_path / "bin"
    folder.mkdir()
    git_path = shutil.which("git")
    assert git_path is not None
    (folder / "git").symlink_to(git_path)
    monkeypatch.setenv("PATH", str(folder))
    return folder


def fake_tool(bin_dir: Path, name: str, script: str) -> None:
    """A fake `name` command running the sh script (its arguments are "$@")."""
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def write_conf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """tower.conf with text, through TOWER_CONF."""
    conf = tmp_path / "tower.conf"
    conf.write_text(text, encoding="utf-8")
    monkeypatch.setenv("TOWER_CONF", str(conf))


def test_a_vault_folder_that_does_not_exist_fails_and_says_to_fix_tower_conf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_conf(tmp_path, monkeypatch, f"projects_dir={tmp_path / 'gone'}\n")
    check = doctor.check_vault()
    assert check.status == "fail"
    assert str(tmp_path / "gone") in check.detail
    assert "projects_dir" in check.fix and "setup.sh" in check.fix


def test_an_existing_vault_folder_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_conf(tmp_path, monkeypatch, f"projects_dir={tmp_path}\n")
    assert doctor.check_vault().status == "ok"


def test_no_orca_command_warns_and_says_how_the_hub_starts_workers_without_it(bin_dir: Path) -> None:
    check = doctor.check_orca()
    assert check.status == "warn"
    assert "not on PATH" in check.detail
    assert "Orca" in check.fix


def test_orca_not_reachable_warns_and_says_to_open_the_app(bin_dir: Path) -> None:
    fake_tool(bin_dir, "orca", """echo '{"ok": true, "result": {"runtime": {"reachable": false}}}'""")
    check = doctor.check_orca()
    assert check.status == "warn"
    assert "not reachable" in check.detail
    assert "open the Orca app" in check.fix


def test_orca_reachable_passes(bin_dir: Path) -> None:
    fake_tool(bin_dir, "orca", """echo '{"ok": true, "result": {"runtime": {"reachable": true}}}'""")
    assert doctor.check_orca().status == "ok"


def fake_gh(bin_dir: Path, logged_in: bool = True, permission: str = "WRITE") -> None:
    """A fake gh: `auth status` passes when logged_in, and `repo view` reports permission as viewerPermission."""
    fake_tool(
        bin_dir,
        "gh",
        f"""case "$1 $2" in
  "auth status") {"echo 'Logged in to github.com account me'" if logged_in else "echo 'not logged in' >&2; exit 1"} ;;
  "repo view") echo '{{"viewerPermission": "{permission}"}}' ;;
  *) exit 2 ;;
esac""",
    )


def github_checkout(tmp_path: Path) -> Path:
    """A git repo whose origin is github.com/owner/repo."""
    folder = tmp_path / "checkout"
    folder.mkdir()
    git(folder, "init", "-q")
    git(folder, "remote", "add", "origin", "https://github.com/owner/repo.git")
    return folder


def test_no_gh_command_fails_and_says_to_install_and_log_in(bin_dir: Path, tmp_path: Path) -> None:
    check = doctor.check_gh(tmp_path)
    assert check.status == "fail"
    assert "gh auth login" in check.fix


def test_gh_logged_out_fails_and_says_to_log_in(bin_dir: Path, tmp_path: Path) -> None:
    fake_gh(bin_dir, logged_in=False)
    check = doctor.check_gh(tmp_path)
    assert check.status == "fail"
    assert "not logged in" in check.detail
    assert "gh auth login" in check.fix


def test_gh_on_an_account_that_cannot_push_here_fails_and_says_to_switch(bin_dir: Path, tmp_path: Path) -> None:
    fake_gh(bin_dir, permission="READ")
    check = doctor.check_gh(github_checkout(tmp_path))
    assert check.status == "fail"
    assert "owner/repo" in check.detail and "READ" in check.detail
    assert "gh auth switch" in check.fix


def test_gh_on_an_account_that_can_push_here_passes(bin_dir: Path, tmp_path: Path) -> None:
    fake_gh(bin_dir, permission="WRITE")
    assert doctor.check_gh(github_checkout(tmp_path)).status == "ok"


def test_gh_logged_in_outside_a_github_repo_passes(bin_dir: Path, tmp_path: Path) -> None:
    fake_gh(bin_dir, permission="READ")  # never asked: no repo to ask about
    assert doctor.check_gh(tmp_path).status == "ok"


def fake_claude(bin_dir: Path, plugins: str = "[]", agents: str = "[]") -> None:
    """A fake claude printing plugins for `plugin list --json` and agents for `agents --json`."""
    (bin_dir / "plugins.json").write_text(plugins, encoding="utf-8")
    (bin_dir / "agents.json").write_text(agents, encoding="utf-8")
    fake_tool(
        bin_dir,
        "claude",
        f"""case "$1 $2" in
  "plugin list") /bin/cat "{bin_dir / "plugins.json"}" ;;
  "agents --json") /bin/cat "{bin_dir / "agents.json"}" ;;
  *) exit 2 ;;
esac""",
    )


def test_the_plugin_not_installed_fails_and_says_to_install_it(bin_dir: Path) -> None:
    fake_claude(bin_dir, plugins='[{"id": "other@market", "enabled": true}]')
    check = doctor.check_plugin()
    assert check.status == "fail"
    assert "not installed" in check.detail
    assert "claude plugin install tower@tower" in check.fix


def test_the_plugin_disabled_fails_and_says_to_enable_it(bin_dir: Path) -> None:
    fake_claude(bin_dir, plugins='[{"id": "tower@tower", "enabled": false}]')
    check = doctor.check_plugin()
    assert check.status == "fail"
    assert "disabled" in check.detail
    assert "claude plugin enable tower@tower" in check.fix


def test_the_plugin_enabled_passes(bin_dir: Path) -> None:
    fake_claude(bin_dir, plugins='[{"id": "tower@tower", "enabled": true, "version": "0.13.2"}]')
    assert doctor.check_plugin().status == "ok"


def test_no_claude_command_fails_the_plugin_check_and_says_to_install_claude_code(bin_dir: Path) -> None:
    check = doctor.check_plugin()
    assert check.status == "fail"
    assert "Claude Code" in check.fix


def test_claude_agents_printing_something_that_is_not_a_list_fails_and_says_to_update(bin_dir: Path) -> None:
    fake_claude(bin_dir, agents='{"sessions": []}')
    check = doctor.check_agents()
    assert check.status == "fail"
    assert "format" in check.detail
    assert "claude update" in check.fix and "rishi-desai/tower" in check.fix


def test_claude_agents_entries_without_a_folder_fail_as_a_format_change(bin_dir: Path) -> None:
    fake_claude(bin_dir, agents='[{"id": "s1", "kind": "interactive", "status": "busy"}]')
    check = doctor.check_agents()
    assert check.status == "fail"
    assert "cwd" in check.detail
    assert "claude update" in check.fix


def test_claude_agents_listing_sessions_passes(bin_dir: Path, tmp_path: Path) -> None:
    fake_claude(bin_dir, agents=f'[{{"id": "s1", "kind": "interactive", "cwd": "{tmp_path}", "status": "busy"}}]')
    check = doctor.check_agents()
    assert check.status == "ok"
    assert "1 session" in check.detail


def test_git_spice_missing_fails_and_says_how_to_install_it(bin_dir: Path) -> None:
    check = doctor.check_git_spice()
    assert check.status == "fail"
    assert "brew install git-spice" in check.fix


def test_git_spice_that_does_not_run_fails_with_its_error(bin_dir: Path) -> None:
    fake_tool(
        bin_dir, "git-spice", "echo 'mise ERROR No version is set for shim: git-spice' >&2\necho 'more' >&2; exit 1"
    )
    check = doctor.check_git_spice()
    assert check.status == "fail"
    assert "No version is set for shim" in check.detail
    assert "git-spice" in check.fix


def test_git_spice_installed_only_as_gs_warns_since_tower_runs_git_spice(bin_dir: Path) -> None:
    fake_tool(bin_dir, "gs", "echo 'git-spice 0.33.0'")
    check = doctor.check_git_spice()
    assert check.status == "warn"
    assert "git-spice" in check.fix


def test_a_gs_that_is_ghostscript_does_not_count_as_git_spice(bin_dir: Path) -> None:
    fake_tool(bin_dir, "gs", "echo '10.04.0'")
    check = doctor.check_git_spice()
    assert check.status == "fail"
    assert "gs there is another program" in check.detail
    assert "brew install git-spice" in check.fix


def test_mise_missing_fails_and_says_how_to_install_it(bin_dir: Path) -> None:
    check = doctor.check_mise()
    assert check.status == "fail"
    assert "brew install mise" in check.fix


def test_mise_installed_passes(bin_dir: Path) -> None:
    fake_tool(bin_dir, "mise", "echo '2026.10.17 macos-arm64 (2026-10-20)'")
    check = doctor.check_mise()
    assert check.status == "ok"
    assert "2026.10.17" in check.detail


def write_policy(text: str) -> Path:
    """policy.toml with text in this test's tower folder (TOWER_DIR, set for every test by conftest)."""
    path = Path(os.environ["TOWER_DIR"]) / "policy.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_policy_file_that_does_not_parse_fails_with_the_line_to_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_conf(tmp_path, monkeypatch, "")
    write_policy('default_profile = "personal"\nwip_limit = \n')
    check = doctor.check_policy()
    assert check.status == "fail"
    assert "line 2" in check.detail
    assert "policy.toml" in check.fix and "tower policy" in check.fix


def test_a_policy_value_that_is_not_allowed_fails_and_names_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_conf(tmp_path, monkeypatch, "")
    write_policy('default_profile = "personal"\n[profiles.personal]\npush = "sometimes"\n')
    check = doctor.check_policy()
    assert check.status == "fail"
    assert 'push = "sometimes"' in check.detail
    assert "policy.toml" in check.fix


def test_no_policy_file_warns_and_says_how_to_choose_a_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_conf(tmp_path, monkeypatch, "")
    check = doctor.check_policy()
    assert check.status == "warn"
    assert "tower policy use" in check.fix
    assert not (Path(os.environ["TOWER_DIR"]) / "policy.toml").exists()  # doctor never writes one


def test_a_valid_policy_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_conf(tmp_path, monkeypatch, "")
    write_policy('default_profile = "personal"\n')
    check = doctor.check_policy()
    assert check.status == "ok"
    assert "personal" in check.detail


NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def log_event(minutes_ago: float) -> None:
    """One prompt event in this test's event log, minutes_ago before NOW."""
    ts = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    events.append(events.Event(ts, "prompt", "s1", None, None, None), events.default_dir())


def test_no_event_log_warns_and_says_to_start_a_session(bin_dir: Path) -> None:
    check = doctor.check_hooks(NOW)
    assert check.status == "warn"
    assert "no events" in check.detail
    assert "start a Claude Code session" in check.fix and "/hooks" in check.fix


def test_no_event_in_the_last_hour_warns_with_when_the_last_one_was(bin_dir: Path) -> None:
    log_event(minutes_ago=180)
    check = doctor.check_hooks(NOW)
    assert check.status == "warn"
    assert "3h ago" in check.detail
    assert "/hooks" in check.fix


def test_an_event_in_the_last_hour_passes(bin_dir: Path) -> None:
    log_event(minutes_ago=180)
    log_event(minutes_ago=5)
    check = doctor.check_hooks(NOW)
    assert check.status == "ok"
    assert "5m ago" in check.detail


def healthy_machine(bin_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every dependency in place except Orca, which is down (a warning, not a failure)."""
    fake_tool(bin_dir, "orca", "exit 1")
    fake_gh(bin_dir)
    fake_claude(bin_dir, plugins='[{"id": "tower@tower", "enabled": true}]')
    fake_tool(bin_dir, "git-spice", "echo 'git-spice 0.33.0'")
    fake_tool(bin_dir, "mise", "echo '2026.10.17'")
    write_conf(tmp_path, monkeypatch, f"projects_dir={tmp_path}\n")
    write_policy('default_profile = "personal"\n')
    events.append(events.Event(hook_events.now_utc(), "prompt", "s1", None, None, None), events.default_dir())
    monkeypatch.chdir(tmp_path)


def test_tower_doctor_with_only_warnings_exits_0_and_lists_every_check(
    bin_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy_machine(bin_dir, tmp_path, monkeypatch)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    for name in ("orca", "gh", "vault", "plugin", "git-spice", "mise", "policy", "agents", "hooks"):
        assert f" {name} " in out
    assert "warn  orca" in out and "fix: open the Orca app" in out
    assert out.rstrip().endswith("tower doctor: no problems, 1 warning")


def test_tower_doctor_with_a_failure_exits_1_and_names_its_fix(
    bin_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy_machine(bin_dir, tmp_path, monkeypatch)
    (bin_dir / "mise").unlink()
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  mise" in out and "fix: brew install mise" in out
    assert out.rstrip().endswith("tower doctor: 1 problem, 1 warning")


def test_a_tower_conf_that_is_not_utf8_fails_the_vault_check_and_says_to_fix_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file may well set projects_dir: it just couldn't be read, so setting it again wouldn't help (#86)."""
    conf = tmp_path / "tower.conf"
    conf.write_bytes(b"projects_dir=\xff\n")
    monkeypatch.setenv("TOWER_CONF", str(conf))
    check = doctor.check_vault()
    assert check.status == "fail"
    assert "UTF-8" in check.detail
    assert check.fix == f"fix or remove {conf}, then run setup.sh again"


def test_a_tower_conf_that_is_not_utf8_doesnt_fail_the_policy_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vault check already fails for it: the policy file itself has no problem (#86)."""
    conf = tmp_path / "tower.conf"
    conf.write_bytes(b"projects_dir=\xff\n")
    monkeypatch.setenv("TOWER_CONF", str(conf))
    write_policy('default_profile = "personal"\n')
    check = doctor.check_policy()
    assert check.status == "ok"
    assert "personal" in check.detail


def test_a_tower_conf_without_projects_dir_fails_the_vault_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_conf(tmp_path, monkeypatch, "wip_limit=3\n")
    check = doctor.check_vault()
    assert check.status == "fail"
    assert "no projects_dir" in check.detail
    assert "setup.sh" in check.fix


def test_gh_that_cannot_read_the_repo_fails_and_says_to_check_the_network_then_switch(
    bin_dir: Path, tmp_path: Path
) -> None:
    checkout = github_checkout(tmp_path)
    fake_tool(
        bin_dir,
        "gh",
        """case "$1 $2" in
  "auth status") exit 0 ;;
  *) echo 'GraphQL: Could not resolve to a Repository' >&2; exit 1 ;;
esac""",
    )
    check = doctor.check_gh(checkout)
    assert check.status == "fail"
    assert "Could not resolve" in check.detail
    assert "reachable" in check.fix and "gh auth switch" in check.fix


def test_gh_where_git_does_not_run_fails_and_says_to_check_git(bin_dir: Path, tmp_path: Path) -> None:
    fake_gh(bin_dir)
    (bin_dir / "git").unlink()
    check = doctor.check_gh(tmp_path)
    assert check.status == "fail"
    assert "remote -v" in check.fix


def test_claude_plugin_list_failing_fails_the_plugin_check(bin_dir: Path) -> None:
    fake_tool(bin_dir, "claude", "echo 'boom' >&2; exit 1")
    check = doctor.check_plugin()
    assert check.status == "fail"
    assert "boom" in check.detail
    assert "claude plugin list" in check.fix


def test_claude_plugin_list_in_a_new_format_fails_as_a_format_change(bin_dir: Path) -> None:
    fake_claude(bin_dir, plugins='{"plugins": [{"id": "tower@tower", "enabled": true}]}')
    check = doctor.check_plugin()
    assert check.status == "fail"
    assert "unexpected" in check.detail
    assert "claude update" in check.fix


def test_no_claude_command_fails_the_agents_check_and_says_to_install_claude_code(bin_dir: Path) -> None:
    check = doctor.check_agents()
    assert check.status == "fail"
    assert "Claude Code" in check.fix


def test_no_policy_file_with_old_approvals_in_tower_conf_says_what_they_become(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_conf(tmp_path, monkeypatch, "push_approval=every\npr_approval=every\n")
    check = doctor.check_policy()
    assert check.status == "warn"
    assert "become profile work" in check.detail
    assert "tower policy use" in check.fix


def test_an_event_only_in_last_months_log_still_counts_just_after_rotation(bin_dir: Path) -> None:
    log_event(minutes_ago=15)
    directory = events.default_dir()
    (directory / events.LOG_NAME).rename(directory / "events-2026-10.jsonl")
    check = doctor.check_hooks(NOW)
    assert check.status == "ok"
    assert "15m ago" in check.detail


def test_a_broken_git_spice_shim_does_not_hide_a_working_gs(bin_dir: Path) -> None:
    fake_tool(bin_dir, "git-spice", "echo 'mise ERROR No version is set for shim: git-spice' >&2; exit 1")
    fake_tool(bin_dir, "gs", "echo 'git-spice 0.33.0'")
    assert doctor.check_git_spice().status == "warn"  # found as gs, though tower's kickoffs run git-spice


def test_the_plugin_enabled_in_one_scope_and_disabled_in_another_passes(bin_dir: Path) -> None:
    plugins = '[{"id": "tower@tower", "scope": "user", "enabled": false}, {"id": "tower@tower", "scope": "project",'
    fake_claude(bin_dir, plugins=plugins + ' "enabled": true}]')
    assert doctor.check_plugin().status == "ok"


def test_gh_too_old_for_active_fails_and_says_to_upgrade(bin_dir: Path, tmp_path: Path) -> None:
    fake_tool(bin_dir, "gh", "echo 'unknown flag: --active' >&2; exit 1")
    check = doctor.check_gh(tmp_path)
    assert check.status == "fail"
    assert "brew upgrade gh" in check.fix


def test_gh_repo_view_printing_something_unexpected_is_not_called_an_access_problem(
    bin_dir: Path, tmp_path: Path
) -> None:
    checkout = github_checkout(tmp_path)
    fake_tool(bin_dir, "gh", 'case "$1 $2" in "auth status") exit 0 ;; *) echo "not json" ;; esac')
    check = doctor.check_gh(checkout)
    assert check.status == "fail"
    assert "unexpected" in check.detail
    assert "gh auth switch" not in check.fix


def test_a_policy_without_default_profile_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_conf(tmp_path, monkeypatch, "")
    write_policy('[repos]\n"owner/repo" = "personal"\n')
    check = doctor.check_policy()
    assert check.status == "warn"
    assert "no default_profile" in check.detail
    assert "tower policy use" in check.fix


def test_an_event_log_that_cannot_be_read_fails_and_writes_nothing(bin_dir: Path) -> None:
    directory = events.default_dir()
    (directory / events.LOG_NAME).mkdir(parents=True)  # a folder where the log should be
    check = doctor.check_hooks(NOW)
    assert check.status == "fail"
    assert str(directory) in check.fix
    assert not (directory / events.LOCK_NAME).exists()
