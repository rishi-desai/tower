"""Tests for src/tower/policy.py: which approval profile applies to a repo, and what each of its keys says.

Every test points TOWER_DIR (where policy.toml lives) and TOWER_CONF at temporary files, so nothing here reads or
writes this machine's real settings.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tower import policy

# What the issue (#26) says each built-in profile does, written out key by key rather than read from the code.
WORK = {
    "push": "every",
    "merge_to_main": "ask",
    "merge_to_branch": "ask",
    "comments": "ask",
    "issue_writes": "ask",
    "pr_writes": "ask",
    "cleanup": "ask",
}
PERSONAL = {
    "push": "force",
    "merge_to_main": "ask",
    "merge_to_branch": "auto",
    "comments": "ask",
    "issue_writes": "auto",
    "pr_writes": "auto",
    "cleanup": "ask",
}
SANDBOX = {
    "push": "none",
    "merge_to_main": "auto",
    "merge_to_branch": "auto",
    "comments": "auto",
    "issue_writes": "auto",
    "pr_writes": "auto",
    "cleanup": "auto",
}
BUILT_INS = {"work": WORK, "personal": PERSONAL, "sandbox": SANDBOX}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty tower dir and no tower.conf; returns the tower dir (where policy.toml goes)."""
    tower_dir = tmp_path / "tower"
    tower_dir.mkdir()
    monkeypatch.setenv("TOWER_DIR", str(tower_dir))
    monkeypatch.setenv("TOWER_CONF", str(tmp_path / "tower.conf"))
    return tower_dir


def write_policy(home: Path, text: str) -> Path:
    """Write policy.toml in the tower dir; returns its path."""
    path = home / "policy.toml"
    path.write_text(text, encoding="utf-8")
    return path


def gates(effective: policy.Effective) -> dict[str, str]:
    """The seven approval keys of an effective policy, by name."""
    return {key: getattr(effective, key) for key in WORK}


def test_with_no_policy_file_the_strictest_profile_applies(home: Path) -> None:
    effective = policy.load().for_repo("acme/widget")

    assert effective.profile == "work"
    assert gates(effective) == WORK
    assert "no policy file" in effective.why
    assert not (home / "policy.toml").exists()  # nothing to migrate, so nothing is written


@pytest.mark.parametrize("name", list(BUILT_INS))
def test_each_built_in_profile_has_the_keys_the_issue_gives_it(home: Path, name: str) -> None:
    write_policy(home, f'default_profile = "{name}"\n')

    effective = policy.load().for_repo(None)

    assert effective.profile == name
    assert gates(effective) == BUILT_INS[name]
    assert effective.problems == ()


@pytest.mark.parametrize("name", list(BUILT_INS))
@pytest.mark.parametrize("default", ["work", "sandbox"])
def test_a_repo_override_beats_default_profile(home: Path, name: str, default: str) -> None:
    write_policy(home, f'default_profile = "{default}"\n\n[repos]\n"acme/widget" = "{name}"\n')

    loaded = policy.load()

    assert gates(loaded.for_repo("acme/widget")) == BUILT_INS[name]
    assert '[repos] "acme/widget"' in loaded.for_repo("acme/widget").why
    assert gates(loaded.for_repo("acme/other")) == BUILT_INS[default]
    assert gates(loaded.for_repo(None)) == BUILT_INS[default]


def test_an_exact_repo_beats_a_glob_and_a_longer_glob_beats_a_shorter_one(home: Path) -> None:
    write_policy(
        home,
        'default_profile = "work"\n[repos]\n"*" = "personal"\n"acme/*" = "sandbox"\n"Acme/Widget" = "work"\n'
        '"acme/w?dget-*" = "personal"\n',
    )

    loaded = policy.load()

    assert loaded.for_repo("acme/widget").profile == "work"  # exact, whatever the letter case
    assert loaded.for_repo("acme/widget-2").profile == "personal"  # 12 literal characters beat acme/*'s 5
    assert loaded.for_repo("acme/gadget").profile == "sandbox"
    assert loaded.for_repo("someone/else").profile == "personal"


def test_equally_specific_globs_take_the_stricter_value_of_each_key(home: Path) -> None:
    write_policy(home, '[repos]\n"acme/*" = "sandbox"\n"*/widg*" = "personal"\n')  # 5 literal characters each

    effective = policy.load().for_repo("acme/widget")

    assert gates(effective) == PERSONAL  # personal is stricter than sandbox on every key
    assert effective.profile == "sandbox+personal"


def test_a_profile_table_changes_only_the_keys_it_names(home: Path) -> None:
    write_policy(home, 'default_profile = "personal"\n[profiles.personal]\ncomments = "auto"\nwip_limit = 5\n')

    effective = policy.load().for_repo(None)

    assert gates(effective) == {**PERSONAL, "comments": "auto"}
    assert (effective.wip_limit, effective.wip_limit_from) == (5, f"[profiles.personal] in {home / 'policy.toml'}")


def test_a_new_profile_takes_the_strictest_value_for_every_key_it_leaves_out(home: Path) -> None:
    write_policy(home, 'default_profile = "client"\n[profiles.client]\npush = "force"\nissue_writes = "auto"\n')

    effective = policy.load().for_repo(None)

    assert effective.profile == "client"
    assert gates(effective) == {**WORK, "push": "force", "issue_writes": "auto"}


def test_wip_limit_comes_from_tower_conf_when_the_profile_has_none(home: Path) -> None:
    (home.parent / "tower.conf").write_text("wip_limit=7\n", encoding="utf-8")
    write_policy(home, 'default_profile = "sandbox"\n')

    effective = policy.load().for_repo(None)

    assert (effective.wip_limit, effective.wip_limit_from) == (7, f"wip_limit in {home.parent / 'tower.conf'}")


@pytest.mark.parametrize(
    ("text", "expected", "problem"),
    [
        pytest.param(
            'default_profile = "persnal"\n', WORK, 'default_profile "persnal" isn\'t a profile', id="unknown default"
        ),
        pytest.param("default_profile = 3\n", WORK, "isn't a profile name", id="default not a string"),
        pytest.param(
            'default_profile = "personal"\n[profiles.personal]\npush = "sometimes"\n',
            {**PERSONAL, "push": "every"},
            "push = \"sometimes\" isn't one of every, force, none, so it's every",
            id="invalid push",
        ),
        pytest.param(
            'default_profile = "sandbox"\n[profiles.sandbox]\ncomments = true\n',
            {**SANDBOX, "comments": "ask"},
            "comments = true isn't one of ask, auto, so it's ask",
            id="invalid ask-or-auto value",
        ),
        pytest.param(
            'default_profile = "sandbox"\n[profiles.sandbox]\ncomment = "auto"\n',
            SANDBOX,
            "unknown key 'comment'",
            id="misspelt key",
        ),
        pytest.param(
            'default_profile = "sandbox"\n[repos]\n"acme/widget" = "sandbx"\n',
            WORK,
            '[repos] "acme/widget" names "sandbx", which isn\'t a profile',
            id="unknown profile in repos",
        ),
        pytest.param(
            'default_profile = "sandbox"\n[repos]\n"acme/widget" = 1\n',
            WORK,
            '[repos] "acme/widget" = 1 isn\'t a profile name',
            id="repos value not a string",
        ),
        pytest.param(
            'default_profile = "sandbox"\npush = "none"\n', SANDBOX, "unknown or misplaced key 'push'", id="stray key"
        ),
        pytest.param("[repos]\n", WORK, "", id="no default_profile"),
    ],
)
def test_invalid_or_unknown_values_get_the_strictest_and_are_reported(
    home: Path, text: str, expected: dict[str, str], problem: str
) -> None:
    write_policy(home, text)

    effective = policy.load().for_repo("acme/widget")

    assert gates(effective) == expected
    assert any(problem in p for p in effective.problems) if problem else effective.problems == ()


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        pytest.param(b'default_profile = "sandbox"\nrepos = ["a"]\n', "line 2: expected a string", id="array"),
        pytest.param(b'default_profile = "sandbox"\nprofiles.x.push = "none"\n', "line 2: dotted keys", id="dotted"),
        pytest.param(
            b'default_profile = "sandbox"\ndefault_profile = "work"\n',
            "line 2: 'default_profile' is defined twice",
            id="twice",
        ),
        pytest.param(b'default_profile = "sandbox\n', "line 1: a string with no closing quote", id="unclosed"),
        pytest.param(b'default_profile = "sandbox" x\n', "line 1: unexpected text", id="trailing text"),
        pytest.param(b'default_profile = "\xff"\n', "isn't UTF-8", id="not utf-8"),
        pytest.param(b"[repos]\n[repos]\n", "line 2: table [repos] is defined twice", id="table twice"),
        pytest.param(b"[[repos]]\n", "line 1: arrays of tables", id="array of tables"),
    ],
)
def test_a_file_that_cant_be_read_gives_every_repo_the_strictest_profile(
    home: Path, content: bytes, problem: str
) -> None:
    (home / "policy.toml").write_bytes(content)

    effective = policy.load().for_repo("acme/widget")

    assert gates(effective) == WORK
    assert "can't be used" in effective.why
    assert any(problem in p for p in effective.problems) or problem in effective.why


# --- The TOML subset: what it reads must mean what TOML says, and what TOML forbids it must refuse --------

VALID_TOML = [
    'default_profile = "personal"\n',
    "# a comment\n\ndefault_profile = 'sandbox'   # trailing comment\n",
    '[profiles.client]\npush = "force"\nwip_limit = 4\n\n[repos]\n"acme/*" = "work"\n\'b/c\' = "sandbox"\n',
    '[profiles."my client"]\ncomments = "auto"\n[profiles.x-y_z]\ncleanup = "ask"\n',
    "[ profiles . work ]\nwip_limit = +1_000\n",
    "[a.b]\nx = 1\n[a]\ny = -0\n",
    'k = "esc \\" \\\\ \\t \\n \\u00e9 \\U0001F600"\nflag = true\noff = false\n',
    '"" = "empty key"\n\r\nwindows = "line end"\r\n',
    "k = 'literal \\n stays'\n",
]
INVALID_TOML = [
    'k = "bad \\q escape"\n',
    "k = 01\n",
    "k = 1__0\n",
    "k = _1\n",
    "k = 1_\n",
    'k = "\\u00"\n',
    'k = "\\uD800"\n',
    'k = "a\tb\x01"\n',
    "a b = 1\n",
    "= 1\n",
    "k =\n",
    "k = true1\n",
    "k = 1\n[k]\n",
    'k = "x" # bell \x07\n',
    "[a]\nx = 1\n[a]\n",
    "[]\n",
    "k = 1\nk = 2\n",
]
UNSUPPORTED_TOML = [  # valid TOML a policy file has no use for: reported, not read
    "k = [1, 2]\n",
    "k = { a = 1 }\n",
    "a.b = 1\n",
    "k = 1.5\n",
    "k = 2026-11-06\n",
    'k = """multi"""\n',
    "k = 0x1f\n",
    "[[k]]\n",
]


@pytest.mark.parametrize("text", VALID_TOML)
def test_the_toml_reader_agrees_with_tomllib(text: str) -> None:
    tomllib = pytest.importorskip("tomllib")
    assert policy.parse_toml(text) == tomllib.loads(text)


@pytest.mark.parametrize("text", INVALID_TOML)
def test_the_toml_reader_refuses_what_tomllib_refuses(text: str) -> None:
    tomllib = pytest.importorskip("tomllib")
    with pytest.raises(tomllib.TOMLDecodeError):
        tomllib.loads(text)
    with pytest.raises(policy.TomlError):
        policy.parse_toml(text)


@pytest.mark.parametrize("text", UNSUPPORTED_TOML)
def test_the_toml_reader_refuses_toml_a_policy_doesnt_use(text: str) -> None:
    with pytest.raises(policy.TomlError):
        policy.parse_toml(text)


# --- Migration from tower.conf ----------------------------------------------------------------------------


def write_conf(home: Path, text: str) -> None:
    """Write the tower.conf the home fixture points TOWER_CONF at."""
    (home.parent / "tower.conf").write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("conf", "expected_profile", "expected"),
    [
        ("push_approval=every\npr_approval=every\n", "work", WORK),
        ("projects_dir=/v\npush_approval=none\npr_approval=none\n", "migrated", {
            **WORK, "push": "none", "merge_to_main": "auto", "merge_to_branch": "auto", "pr_writes": "auto"
        }),
        ("push_approval=force\n", "migrated", {**WORK, "push": "force"}),
        ("pr_approval=none \n", "migrated", {
            **WORK, "merge_to_main": "auto", "merge_to_branch": "auto", "pr_writes": "auto"
        }),
        ("push_approval=sometimes\npr_approval=whenever\n", "work", WORK),
    ],
)  # fmt: skip
def test_old_tower_conf_approvals_are_migrated_into_a_policy_file_that_keeps_them(
    home: Path, conf: str, expected_profile: str, expected: dict[str, str]
) -> None:
    write_conf(home, conf)

    effective = policy.load().for_repo(None)

    assert (effective.profile, gates(effective)) == (expected_profile, expected)
    assert (home / "policy.toml").exists()
    assert effective.problems == ()
    write_conf(home, "push_approval=none\npr_approval=none\n")  # from now on tower.conf's are ignored
    assert gates(policy.load().for_repo(None)) == expected


def test_a_policy_file_already_there_wins_over_tower_conf(home: Path) -> None:
    write_conf(home, "push_approval=none\npr_approval=none\n")
    write_policy(home, 'default_profile = "work"\n')

    assert gates(policy.load().for_repo(None)) == WORK


def test_migration_that_cant_write_still_keeps_the_old_approvals(home: Path) -> None:
    write_conf(home, "push_approval=none\npr_approval=every\n")
    home.chmod(0o500)
    try:
        effective = policy.load().for_repo(None)
    finally:
        home.chmod(0o700)

    assert gates(effective) == {**WORK, "push": "none"}
    assert not (home / "policy.toml").exists()
    assert any("couldn't write" in p for p in effective.problems)


# --- tower policy use -------------------------------------------------------------------------------------


def test_use_with_no_file_creates_one_with_that_default_profile(home: Path) -> None:
    message = policy.use("personal")

    assert policy.load().for_repo("acme/widget").profile == "personal"
    assert "personal" in message


def test_use_changes_only_the_default_profile_line(home: Path) -> None:
    original = (
        "# my notes\n\ndefault_profile = 'work'  # was work\n\n[profiles.client]\npush = \"force\"\n\n"
        '[repos]\n"acme/*" = "client"\n'
    )
    write_policy(home, original)

    policy.use("client")

    text = (home / "policy.toml").read_text(encoding="utf-8")
    assert text == original.replace("default_profile = 'work'  # was work", 'default_profile = "client"')
    assert policy.load().for_repo(None).profile == "client"


def test_use_adds_default_profile_after_the_opening_comments_when_missing(home: Path) -> None:
    write_policy(home, "# notes\n# more\n\n[repos]\n")

    policy.use("sandbox")

    text = (home / "policy.toml").read_text(encoding="utf-8")
    assert text.startswith('# notes\n# more\n\ndefault_profile = "sandbox"\n')
    assert policy.load().for_repo(None).profile == "sandbox"


def test_use_for_a_repo_adds_or_replaces_its_line_in_repos(home: Path) -> None:
    write_policy(home, 'default_profile = "personal"\n')

    policy.use("work", "acme/widget")
    assert policy.load().for_repo("acme/widget").profile == "work"
    policy.use("sandbox", "acme/widget")
    loaded = policy.load()

    assert loaded.for_repo("acme/widget").profile == "sandbox"
    assert loaded.for_repo(None).profile == "personal"
    assert (home / "policy.toml").read_text(encoding="utf-8").count("acme/widget") == 1


def test_use_for_a_repo_replaces_a_line_written_in_other_letter_case(home: Path) -> None:
    write_policy(home, 'default_profile = "work"\n\n[repos]\n"Acme/Widget" = "work"  # client\n"b/*" = "work"\n')

    policy.use("personal", "acme/widget")

    text = (home / "policy.toml").read_text(encoding="utf-8")
    assert '"Acme/Widget"' not in text and '"b/*" = "work"' in text
    assert policy.load().for_repo("acme/widget").profile == "personal"


def test_use_migrates_tower_conf_first(home: Path) -> None:
    write_conf(home, "push_approval=none\npr_approval=none\n")

    policy.use("personal")

    loaded = policy.load()
    assert loaded.for_repo(None).profile == "personal"
    assert "migrated" in loaded.profiles  # still there to switch back to


@pytest.mark.parametrize(
    ("text", "args", "error"),
    [
        ('default_profile = "work"\n', ("persnal",), "isn't a profile"),
        ('default_profile = "work\n', ("personal",), "can't be used"),
    ],
)
def test_use_refuses_an_unknown_profile_or_a_broken_file_and_changes_nothing(
    home: Path, text: str, args: tuple[str], error: str
) -> None:
    write_policy(home, text)

    with pytest.raises(policy.PolicyError, match=error):
        policy.use(*args)
    assert (home / "policy.toml").read_text(encoding="utf-8") == text


# --- Which repo -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("git@github.com:Acme/Widget.git", "acme/widget"),
        ("https://github.com/acme/widget", "acme/widget"),
        ("https://github.com/acme/widget.git/", "acme/widget"),
        ("https://user:token@github.com/acme/widget.git", "acme/widget"),
        ("ssh://git@github.example.com:22/acme/widget.git", "acme/widget"),
        ("git@github.example.com:group/sub/widget.git\n", "sub/widget"),
        ("/srv/git/widget.git", None),
        ("file:///srv/git/acme/widget.git", None),
        ("../widget", None),
        ("https://github.com/acme", None),
    ],
)
def test_the_repo_slug_comes_from_the_origin_url(url: str, slug: str | None) -> None:
    assert policy.slug_from_url(url) == slug


@pytest.mark.parametrize(
    ("value", "slug"),
    [
        ("acme/widget", "acme/widget"),
        ("github.com/Acme/Widget", "acme/widget"),
        ("https://ghe.io/a/b", "a/b"),
        ("https://github.com/Acme/Other/pull/5", "acme/other"),
        ("https://github.com/acme/other.git", "acme/other"),
        ("https://github.com/acme", None),
    ],
)
def test_the_repo_slug_of_a_gh_repo_flag(value: str, slug: str) -> None:
    assert policy.slug_from_repo_arg(value) == slug


def test_for_checkout_reads_the_repo_from_git(home: Path, tmp_path: Path) -> None:
    checkout = tmp_path / "widget"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    write_policy(home, 'default_profile = "personal"\n[repos]\n"acme/*" = "sandbox"\n')
    assert policy.for_checkout(checkout) == (policy.load().for_repo(None), None)  # no origin yet

    subprocess.run(
        ["git", "-C", str(checkout), "remote", "add", "origin", "git@github.com:acme/widget.git"], check=True
    )
    effective, slug = policy.for_checkout(checkout)

    assert (effective.profile, slug) == ("sandbox", "acme/widget")
    assert policy.for_checkout(tmp_path / "not-a-repo-or-missing")[1] is None


def test_a_tower_conf_that_isnt_utf8_is_a_problem_not_a_crash(home: Path) -> None:
    (home.parent / "tower.conf").write_bytes(b"push_approval=\xff\n")

    effective = policy.load().for_repo(None)

    assert gates(effective) == WORK
    assert any("isn't UTF-8" in p for p in effective.problems)


@pytest.mark.parametrize(
    ("conf", "policy_text", "expected"),
    [
        (None, None, "work"),
        ("push_approval=every\npr_approval=every\n", None, "work"),
        ("push_approval=none\n", None, "migrated"),
        ("push_approval=none\n", 'default_profile = "sandbox"\n', "sandbox"),
        (None, "[repos]\n", "work"),
    ],
)
def test_current_default_says_what_setup_would_keep_without_writing(
    home: Path, conf: str | None, policy_text: str | None, expected: str
) -> None:
    if conf is not None:
        write_conf(home, conf)
    if policy_text is not None:
        write_policy(home, policy_text)

    assert policy.current_default() == expected
    assert (home / "policy.toml").exists() == (policy_text is not None)


def test_available_profiles_include_migrated_only_while_it_is_pending(home: Path) -> None:
    assert policy.available_profiles() == ["personal", "sandbox", "work"]
    write_conf(home, "pr_approval=none\n")
    assert policy.available_profiles() == ["migrated", "personal", "sandbox", "work"]
    write_policy(home, '[profiles.client]\npush = "force"\n')
    assert policy.available_profiles() == ["client", "personal", "sandbox", "work"]
