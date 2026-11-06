"""policy.py: tower's approval policy, which of an agent's actions ask you first and which don't, per repo.

The policy file is ~/.claude/tower/policy.toml (in $TOWER_DIR when that's set):

    default_profile = "personal"        # this machine's profile

    [profiles.personal]                 # change some keys of a built-in profile, or define a new profile
    wip_limit = 4

    [repos]                             # per-repo overrides: "owner/repo", or a glob on it
    "acme/*" = "work"
    "rishi-desai/scratch" = "sandbox"

Keys, strictest value first:
    push             every | force | none   which git pushes ask: all, only force pushes and deletes, or none
    merge_to_main    ask | auto             merging a PR that lands on its repo's default branch
    merge_to_branch  ask | auto             merging a PR that lands on any other branch (an integration branch)
A PR lands on its base, but a PR in a GitHub stack lands on the stack's base: merging it merges every PR below it.
    comments         ask | auto             PR and issue comments and reviews
    issue_writes     ask | auto             creating, editing, closing, reopening (and so on) issues
    pr_writes        ask | auto             opening, editing, readying, closing and reopening PRs
    cleanup          ask | auto             removing finished worktrees (`tower clean`, T10)
    wip_limit        a whole number, 1+     the WIP limit; when the profile doesn't set it, tower.conf's (else 3)

Built-in profiles: work (everything asks), personal (only force pushes, merges into the default branch, comments and
cleanup ask) and sandbox (nothing asks). A [profiles.<built-in>] table changes only the keys it names; a new
profile's missing keys take the strictest value.

Which profile a repo gets: the [repos] pattern matching its `owner/repo` (an exact name beats a glob, and the glob with
more literal characters beats the others; a tie takes the stricter value of each key), else default_profile, else the
built-in strictest, work. A checkout is judged by every repo its remotes' fetch and push URLs point at, the stricter
value of each key across them, so re-pointing or adding a remote can only tighten its policy. An unknown profile name gives work, an invalid value
the strictest value for its key, and a file that can't be read or parsed gives work for every repo. Each of these is
listed in `problems`, which `tower policy` prints. wip_limit isn't an approval, so an invalid one falls back to
tower.conf's instead of a "strictest" number.

Migration: tower.conf used to hold push_approval and pr_approval. When there is no policy file and tower.conf has
either, load() writes one that keeps exactly what they did; from then on tower.conf's values are ignored.

Whatever a profile says, the approval hook (plugin/scripts/confirm-public.py) asks before an agent changes this file,
tower.conf, a rule file or CLAUDE.local.md, or runs `tower policy use`: that safety floor holds in every profile,
sandbox included, so an agent can't loosen its own rules.

The hooks import this module under macOS's Python 3.9, which has no tomllib, so the file is read by parse_toml(), a
small reader for the part of TOML a policy needs: tables, bare or quoted keys, strings, whole numbers, true/false and
comments. Anything else is a problem, never a guess (docs/adr/0004-policy-profiles-and-the-toml-subset.md).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from tower import ledger
from tower.events import default_dir

POLICY_NAME = "policy.toml"
# Each approval key's values, strictest first.
KEYS: dict[str, tuple[str, ...]] = {
    "push": ("every", "force", "none"),
    "merge_to_main": ("ask", "auto"),
    "merge_to_branch": ("ask", "auto"),
    "comments": ("ask", "auto"),
    "issue_writes": ("ask", "auto"),
    "pr_writes": ("ask", "auto"),
    "cleanup": ("ask", "auto"),
}
STRICTEST_VALUES = {key: values[0] for key, values in KEYS.items()}
BUILT_IN: dict[str, dict[str, str]] = {
    "work": dict(STRICTEST_VALUES),
    "personal": {
        "push": "force",
        "merge_to_main": "ask",
        "merge_to_branch": "auto",
        "comments": "ask",
        "issue_writes": "auto",
        "pr_writes": "auto",
        "cleanup": "ask",
    },
    "sandbox": {"push": "none", **{key: "auto" for key in KEYS if key != "push"}},
}
STRICTEST = "work"
GLOB_CHARS = "*?["
GIT_TIMEOUT_SECONDS = 3  # the approval hook runs this before every checked command
LEGACY_PUSH = ("every", "force", "none")  # tower.conf's push_approval values
LEGACY_PR = ("every", "none")  # tower.conf's pr_approval values


class PolicyError(Exception):
    """`tower policy use` can't make the change: an unknown profile, no repo to name, or a file that needs fixing."""


class RepoUnknown(Exception):
    """git couldn't be run, or timed out, so which repo a folder belongs to isn't known."""


def policy_path() -> Path:
    """The policy file: policy.toml in tower's folder ($TOWER_DIR, else ~/.claude/tower)."""
    return default_dir() / POLICY_NAME


def display_path(path: Path) -> str:
    """path as reports print it, with the home folder as ~."""
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home) :] if home != os.sep and text.startswith(home + os.sep) else text


# --- What applies -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoPolicy:
    """The policy that applies to one repo: its profile, why that one, each key's value, and the file's problems."""

    profile: str
    why: str
    push: str
    merge_to_main: str
    merge_to_branch: str
    comments: str
    issue_writes: str
    pr_writes: str
    cleanup: str
    wip_limit: int
    wip_limit_from: str
    problems: tuple[str, ...] = ()

    def value(self, key: str) -> str:
        """The value of one approval key (a KEYS name)."""
        result: str = getattr(self, key)
        return result

    def asks(self, key: str) -> bool:
        """Whether an ask|auto key asks (anything but auto does). push isn't one: it has three values."""
        return self.value(key) != "auto"


def make_repo_policy(
    profile: str, why: str, values: dict[str, str], wip: tuple[int, str], problems: tuple[str, ...]
) -> RepoPolicy:
    """A RepoPolicy from a full set of key values (a KEYS name to one of its values)."""
    return RepoPolicy(profile, why, **values, wip_limit=wip[0], wip_limit_from=wip[1], problems=problems)


def stricter(first: RepoPolicy, second: RepoPolicy) -> RepoPolicy:
    """The stricter value of each key of two policies, for an action that touches both of their repos."""
    if first == second:
        return first
    values = {key: min(first.value(key), second.value(key), key=KEYS[key].index) for key in KEYS}
    profile = first.profile if first.profile == second.profile else f"{first.profile}+{second.profile}"
    why = f"the stricter of {first.profile} ({first.why}) and {second.profile} ({second.why}), key by key"
    wip = min((first.wip_limit, first.wip_limit_from), (second.wip_limit, second.wip_limit_from))
    problems = first.problems + tuple(p for p in second.problems if p not in first.problems)
    return make_repo_policy(profile, why, values, wip, problems)


@dataclass(frozen=True)
class Policy:
    """The policy file as read: every profile (built-ins included) with all its keys, the repo overrides, and the
    problems found. broken says why the file can't be used at all (then every repo gets the strictest profile)."""

    path: Path
    exists: bool
    default_profile: str | None = None
    profiles: dict[str, dict[str, str]] = field(default_factory=lambda: {k: dict(v) for k, v in BUILT_IN.items()})
    profile_wip: dict[str, int] = field(default_factory=dict)  # the wip_limit a profile's table sets
    repos: tuple[tuple[str, str | None], ...] = ()  # (pattern, profile name; None when it isn't a string)
    problems: tuple[str, ...] = ()
    broken: str = ""
    machine_wip: tuple[int, str] = (3, "the default")

    def for_repo(self, slug: str | None) -> RepoPolicy:
        """The policy for the repo whose `owner/repo` is slug (None: no GitHub remote, so no override applies)."""
        if self.broken:
            return self.strictest(f"{display_path(self.path)} can't be used ({self.broken}), so the built-in strictest")
        if not self.exists:
            return self.strictest(f"no policy file ({display_path(self.path)}), so the built-in strictest")
        matched = self.matching_repos(slug) if slug else []
        if len(matched) > 1:
            first, *rest = (self.resolve(name, why) for name, why in matched)
            for other in rest:
                first = stricter(first, other)
            return first
        if matched:
            return self.resolve(*matched[0])
        if self.default_profile is None:
            return self.strictest(f"no usable default_profile in {display_path(self.path)}, so the built-in strictest")
        return self.resolve(self.default_profile, f"default_profile in {display_path(self.path)}")

    def for_repos(self, slugs: list[str]) -> RepoPolicy:
        """The policy for a checkout whose remotes point at the repos SLUGS: the stricter of theirs, key by key, so a
        remote added or re-pointed can only tighten it; with none, the default profile."""
        if not slugs:
            return self.for_repo(None)
        combined = self.for_repo(slugs[0])
        for slug in slugs[1:]:
            combined = stricter(combined, self.for_repo(slug))
        return combined

    def for_folder(self, folder: Path) -> tuple[RepoPolicy, str | None]:
        """The policy for the checkout holding folder (every remote counts, see for_repos), and its origin's
        `owner/repo` (None without one); the built-in strictest when git can't say."""
        if not folder.exists():  # not cloned yet, perhaps: it could become any repo
            return self.strictest(f"{folder} doesn't exist, so which repo it holds isn't known: the strictest"), None
        try:
            origin, slugs = remote_slugs(folder)
        except RepoUnknown as error:
            return self.strictest(f"which repo this is isn't known ({error}), so the built-in strictest"), None
        return self.for_repos(slugs), origin

    def with_repos(self, base: RepoPolicy, names: list[str]) -> RepoPolicy:
        """The stricter, key by key, of base and the policy of each repo NAMES names (gh -R values, URLs)."""
        combined = base
        for name in names:
            combined = stricter(combined, self.for_repo(slug_from_repo_arg(name)))
        return combined

    def with_policy(self, base: RepoPolicy, other: RepoPolicy) -> RepoPolicy:
        """The stricter, key by key, of base and other (another checkout's policy, for a command that reaches it)."""
        return stricter(base, other)

    def profiles_in_play(self) -> list[RepoPolicy]:
        """The policy of every profile a repo can get from this file: default_profile's (or the strictest) and each
        [repos] entry's, for a command that may reach any repo without saying which."""
        if self.broken or not self.exists:
            return [self.for_repo(None)]
        return [self.for_repo(None), *(self.resolve(name, "a [repos] entry") for _, name in self.repos)]

    def matching_repos(self, slug: str) -> list[tuple[str | None, str]]:
        """The (profile, why) of the most specific [repos] patterns matching slug: several only on a tie."""
        scored = []
        for pattern, name in self.repos:
            lowered = pattern.lower()
            is_glob = any(char in lowered for char in GLOB_CHARS)
            if (fnmatch.fnmatchcase(slug, lowered) if is_glob else lowered == slug) and pattern:
                score = (not is_glob, sum(1 for char in lowered if char not in GLOB_CHARS))
                why = f"[repos] {json.dumps(pattern)} in {display_path(self.path)} matches {slug}"
                scored.append((score, name, why))
        if not scored:
            return []
        best = max(score for score, _, _ in scored)
        return [(name, why) for score, name, why in scored if score == best]

    def resolve(self, name: str | None, why: str) -> RepoPolicy:
        """The policy of the profile called name (None or unknown: the built-in strictest), chosen because of why."""
        if name is None or name not in self.profiles:
            label = "a value that isn't a string" if name is None else json.dumps(name)
            return self.strictest(f"{why}, but {label} isn't a profile, so the built-in strictest")
        wip = self.machine_wip
        if name in self.profile_wip:
            wip = (self.profile_wip[name], f"[profiles.{name}] in {display_path(self.path)}")
        return make_repo_policy(name, why, self.profiles[name], wip, self.problems)

    def strictest(self, why: str) -> RepoPolicy:
        """The built-in strictest profile, work, chosen because of why."""
        return make_repo_policy(STRICTEST, why, dict(STRICTEST_VALUES), self.machine_wip, self.problems)


def load(path: Path | None = None, migrate_conf: bool = True) -> Policy:
    """Read the policy file (default: policy_path()) and tower.conf. Never raises: what can't be read is a problem.

    With no policy file, tower.conf's old push_approval/pr_approval are migrated into a new one first (see migrate()),
    unless migrate_conf is False (setup.sh's dry run): then the file counts as missing.
    """
    path = policy_path() if path is None else path
    problems: list[str] = []
    try:
        conf = ledger.read_conf(ledger.conf_path())
    except ledger.LedgerError as error:
        problems.append(f"{error}, so its settings weren't used")
        conf = {}
    except UnicodeDecodeError:
        problems.append(f"{display_path(ledger.conf_path())} isn't UTF-8 text, so its settings weren't used")
        conf = {}
    machine_wip = (ledger.wip_limit(conf), f"wip_limit in {display_path(ledger.conf_path())}")
    if not conf.get("wip_limit", "").isdigit():
        machine_wip = (machine_wip[0], "the default")
    try:
        text: str | None = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = migrate(path, conf, problems) if migrate_conf else None
    except (OSError, UnicodeDecodeError) as error:
        problems.append(f"can't read {display_path(path)}")
        return Policy(path, True, problems=tuple(problems), broken=error_reason(error), machine_wip=machine_wip)
    if text is None:
        return Policy(path, False, problems=tuple(problems), machine_wip=machine_wip)
    try:
        document = parse_toml(text)
    except TomlError as error:
        problems.append(f"{display_path(path)}: {error}")
        return Policy(path, True, problems=tuple(problems), broken=str(error), machine_wip=machine_wip)
    return from_document(path, document, problems, machine_wip)


def error_reason(error: Exception) -> str:
    """A short, plain reason for an OS or decoding error."""
    if isinstance(error, UnicodeDecodeError):
        return "it isn't UTF-8 text"
    return getattr(error, "strerror", None) or type(error).__name__


def from_document(path: Path, document: dict[str, object], problems: list[str], machine_wip: tuple[int, str]) -> Policy:
    """A Policy from a parsed policy file, adding a problem for every unknown key, name or invalid value."""
    profiles = {name: dict(values) for name, values in BUILT_IN.items()}
    profile_wip: dict[str, int] = {}
    repos: list[tuple[str, str | None]] = []
    default_profile: str | None = None
    for key, value in document.items():
        if key == "default_profile":
            default_profile = value if isinstance(value, str) else None
            if not isinstance(value, str):
                problems.append(
                    f"default_profile = {toml_value(value)} isn't a profile name, so the built-in strictest"
                )
        elif key == "profiles" and isinstance(value, dict):
            for name, table in value.items():
                if not isinstance(table, dict):
                    problems.append(f"profiles.{name} isn't a [profiles.{name}] table, so it was ignored")
                    continue
                profiles[name] = read_profile(name, table, profile_wip, problems)
        elif key == "repos" and isinstance(value, dict):
            for pattern, name in value.items():
                if not isinstance(name, str):
                    problems.append(f"[repos] {json.dumps(pattern)} = {toml_value(name)} isn't a profile name")
                repos.append((pattern, name if isinstance(name, str) else None))
        else:
            problems.append(f"unknown or misplaced key {key!r} in {display_path(path)}, so it was ignored")
    for pattern, name in repos:
        if isinstance(name, str) and name not in profiles:
            problems.append(f"[repos] {json.dumps(pattern)} names {json.dumps(name)}, which isn't a profile")
    if default_profile is not None and default_profile not in profiles:
        problems.append(f"default_profile {json.dumps(default_profile)} isn't a profile, so the built-in strictest")
    return Policy(
        path,
        True,
        default_profile=default_profile,
        profiles=profiles,
        profile_wip=profile_wip,
        repos=tuple(repos),
        problems=tuple(problems),
        machine_wip=machine_wip,
    )


def read_profile(
    name: str, table: dict[str, object], profile_wip: dict[str, int], problems: list[str]
) -> dict[str, str]:
    """One [profiles.<name>] table as a full set of keys: a built-in's values, or the strictest, under the table's."""
    values = dict(BUILT_IN.get(name, STRICTEST_VALUES))
    for key, value in table.items():
        if key == "wip_limit":
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                profile_wip[name] = value
            else:
                problems.append(f"[profiles.{name}] wip_limit = {toml_value(value)} isn't a whole number from 1 up")
        elif key in KEYS:
            if value in KEYS[key]:
                values[key] = str(value)
            else:
                values[key] = KEYS[key][0]
                allowed = ", ".join(KEYS[key])
                problems.append(
                    f"[profiles.{name}] {key} = {toml_value(value)} isn't one of {allowed}, so it's {KEYS[key][0]}"
                )
        else:
            problems.append(f"[profiles.{name}] has an unknown key {key!r}, so it was ignored")
    return values


def toml_value(value: object) -> str:
    """value as it would be written in TOML, for problems."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        return "a table"
    return str(value)


# --- Which repo ---------------------------------------------------------------------------------------

SCHEME_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]*@)?[^/]+/(?P<path>.+)")
SCP_URL = re.compile(r"(?:[^@/:]+@)?[^/:]+:(?!/)(?P<path>.+)")


def slug_from_url(url: str) -> str | None:
    """The lower-cased `owner/repo` of a remote URL (https://, ssh:// or git@host:owner/repo), or None for a local
    path or file:// remote, which names no GitHub repo."""
    url = url.strip()
    if url.lower().startswith("file:"):
        return None
    match = SCHEME_URL.fullmatch(url) or SCP_URL.fullmatch(url)
    return slug_from_path(match.group("path")) if match else None


def slug_from_path(path: str) -> str | None:
    """The lower-cased last two parts of owner/repo-like text (host/owner/repo.git), or None with fewer than two."""
    parts = [part for part in path.strip().rstrip("/").split("/") if part]
    if len(parts) < 2:
        return None
    repo = parts[-1].removesuffix(".git")
    return f"{parts[-2]}/{repo}".lower() if repo else None


def slug_from_repo_arg(value: str) -> str | None:
    """The lower-cased `owner/repo` a command names: OWNER/REPO or HOST/OWNER/REPO (gh's -R), a git URL in scp form
    ([user@]host:owner/repo.git), or a URL whose path starts with owner/repo (a PR, a clone URL) or with
    repos/owner/repo (GitHub's API). A query or fragment is ignored."""
    value = value.strip()
    if "://" in value:
        match = SCHEME_URL.fullmatch(value)
        if match is None:
            return None
        parts = [part for part in re.split(r"[?#]", match.group("path"), maxsplit=1)[0].split("/") if part]
        parts = parts[1:] if parts[:1] == ["repos"] else parts
        return slug_from_path("/".join(parts[:2]))
    if SCP_URL.fullmatch(value):
        return slug_from_url(value)
    return slug_from_path(value)


def remote_slugs(folder: Path) -> tuple[str | None, list[str]]:
    """The `owner/repo` of the checkout holding folder's origin (None without one), and of every repo its remotes'
    fetch and push URLs point at, origin's first, each once; ([]) outside a git repo. `git remote -v` applies
    insteadOf and pushInsteadOf, so they are the URLs git really uses. Raises RepoUnknown when git can't be run or
    times out."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(folder), "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RepoUnknown(f"git remote -v failed: {type(error).__name__}") from error
    if proc.returncode != 0:
        return None, []
    origin: str | None = None
    slugs: list[str] = []
    for line in proc.stdout.splitlines():
        name, _, rest = line.partition("\t")
        slug = slug_from_url(rest.rsplit(" (", 1)[0])
        if slug is None:
            continue
        if name == "origin" and origin is None:
            origin = slug
        if slug not in slugs:
            slugs.append(slug)
    if origin is not None:
        slugs.remove(origin)
        slugs.insert(0, origin)
    return origin, slugs


def for_checkout(folder: Path) -> tuple[RepoPolicy, str | None]:
    """The policy for the checkout holding folder, and its origin's `owner/repo` (see Policy.for_folder)."""
    return load().for_folder(folder)


# --- Reports ------------------------------------------------------------------------------------------

KEY_HELP = {
    "merge_to_main": "merging a PR into its repo's default branch",
    "merge_to_branch": "merging a PR into any other branch",
    "comments": "PR and issue comments and reviews",
    "issue_writes": "creating, editing, closing and reopening issues",
    "pr_writes": "opening, editing, readying, closing and reopening PRs",
    "cleanup": "removing finished worktrees",
}
PUSH_HELP = {"every": "every push asks", "force": "only force pushes and branch deletes ask", "none": "no push asks"}
# For the session's context: what each key lets through without asking, and what it asks for.
SHORT_HELP = {
    "merge_to_main": "merging PRs into the default branch",
    "merge_to_branch": "merging PRs into other branches",
    "comments": "PR and issue comments and reviews",
    "issue_writes": "creating, editing and closing issues",
    "pr_writes": "opening, editing and closing PRs",
    "cleanup": "cleaning up finished worktrees",
}
FLOOR_NOTE = (
    "The safety floor holds in every profile: an agent's changes to this policy, tower.conf, rule files and "
    "CLAUDE.local.md always ask."
)


def describe(repo_policy: RepoPolicy, slug: str | None, profiles: list[str]) -> list[str]:
    """`tower policy`'s report: the profile for the repo whose `owner/repo` is slug, why, each key, the problems."""
    where = slug or "this folder (no GitHub origin)"
    lines = [f"tower policy for {where}: {repo_policy.profile}", f"why: {repo_policy.why}"]
    lines.append(f"  {'push':<16} {repo_policy.push:<6} {PUSH_HELP[repo_policy.push]}")
    lines += [f"  {key:<16} {repo_policy.value(key):<6} {help_text}" for key, help_text in KEY_HELP.items()]
    lines.append(f"  {'wip_limit':<16} {repo_policy.wip_limit:<6} {repo_policy.wip_limit_from}")
    if repo_policy.problems:
        lines += ["problems:", *(f"  - {problem}" for problem in repo_policy.problems)]
    else:
        lines.append("problems: none")
    lines += [FLOOR_NOTE, f"Switch in your own terminal: tower policy use <profile> [--repo]   ({', '.join(profiles)})"]
    return lines


def session_line(repo_policy: RepoPolicy, slug: str | None) -> str:
    """One line for a session's context: what it may do without asking and what asks you first."""
    free: list[str] = []
    asking: list[str] = []
    if repo_policy.push == "force":
        free.append("plain git pushes")
        asking.append("force pushes and branch deletes")
    else:
        (free if repo_policy.push == "none" else asking).append("git pushes")
    for key, text in SHORT_HELP.items():
        (asking if repo_policy.asks(key) else free).append(text)
    where = f"this repo ({slug})" if slug else "this folder"
    return (
        f"tower policy for {where}: {repo_policy.profile} ({repo_policy.why}). "
        f"Without asking: {'; '.join(free) or 'nothing'}. Asks the user first: {'; '.join(asking) or 'nothing'}. "
        "Changing tower's policy, tower.conf or rule files always asks."
    )


def scan_line(repo_policy: RepoPolicy) -> str:
    """The hub scan's one-line summary of the repo's policy."""
    keys = " | ".join(f"{key}: {repo_policy.value(key)}" for key in KEYS)
    problems = f" | problems: {len(repo_policy.problems)} (see `tower policy`)" if repo_policy.problems else ""
    return f"Policy: {repo_policy.profile} ({repo_policy.why}) | {keys}{problems}"


def to_json(repo_policy: RepoPolicy) -> dict[str, object]:
    """The policy as `tower state --json` reports it (the WIP limit is reported beside it, as wip_limit)."""
    return {
        "profile": repo_policy.profile,
        "why": repo_policy.why,
        **{key: repo_policy.value(key) for key in KEYS},
        "problems": list(repo_policy.problems),
    }


# --- Migration from tower.conf ------------------------------------------------------------------------

FILE_HEADER = """\
# tower's approval policy for this machine: which of an agent's actions ask you first. See README "Policy profiles".
# Built-in profiles: work (everything asks), personal, sandbox (nothing asks). `tower policy` shows what applies here.
# Change it in your own terminal with `tower policy use <profile> [--repo]`: an agent can't change this file without
# your approval.
"""
REPOS_EXAMPLE = """
[repos]
# Per-repo overrides, by "owner/repo" or a glob on it, e.g.
# "acme/*" = "work"
"""


def legacy_modes(conf: dict[str, str]) -> tuple[str, str] | None:
    """tower.conf's old (push_approval, pr_approval), as the approval hook read them (stripped; unknown or missing
    meant every), or None if it has neither."""
    push, pr = conf.get("push_approval"), conf.get("pr_approval")
    if push is None and pr is None:
        return None
    push_mode = push.strip() if push is not None and push.strip() in LEGACY_PUSH else "every"
    pr_mode = pr.strip() if pr is not None and pr.strip() in LEGACY_PR else "every"
    return push_mode, pr_mode


def migration_profile() -> str | None:
    """With no policy file yet, the default profile migrating tower.conf would give (work when both old keys were
    every, else migrated); None when there is a policy file or nothing to migrate. Writes nothing."""
    if policy_path().exists():
        return None
    try:
        modes = legacy_modes(ledger.read_conf(ledger.conf_path()))
    except (ledger.LedgerError, UnicodeDecodeError):
        return None  # load() reports an unreadable tower.conf; setup.sh's own read of it fails first
    if modes is None:
        return None
    return STRICTEST if modes == ("every", "every") else "migrated"


def current_default() -> str:
    """This machine's default profile as things stand, without writing anything (for setup.sh's question): the
    policy file's default_profile, or what migrating tower.conf would make it, or work."""
    loaded = load(migrate_conf=False)
    if loaded.exists:
        return loaded.default_profile or STRICTEST
    return migration_profile() or STRICTEST


def available_profiles() -> list[str]:
    """Every profile setup.sh may make the default, without writing anything: the built-ins, the policy file's own,
    and `migrated` when tower.conf's old approvals are still to be migrated into one."""
    pending = {"migrated"} if migration_profile() == "migrated" else set()
    return sorted(set(load(migrate_conf=False).profiles) | pending)


def migration_text(conf: dict[str, str], today: date) -> str | None:
    """A policy file that does exactly what tower.conf's push_approval and pr_approval did, or None if it has
    neither. Before profiles, comments, reviews and issue writes always asked; an unknown value meant every."""
    modes = legacy_modes(conf)
    if modes is None:
        return None
    push_mode, pr_mode = modes
    origin = f"from {display_path(ledger.conf_path())}: push_approval={push_mode}, pr_approval={pr_mode}"
    lines = [FILE_HEADER.rstrip("\n"), f"# Migrated on {today.isoformat()} {origin}.", ""]
    if (push_mode, pr_mode) == ("every", "every"):  # that is the built-in work profile exactly
        lines.append('default_profile = "work"')
    else:
        pr_value = "auto" if pr_mode == "none" else "ask"
        lines += [
            'default_profile = "migrated"',
            "",
            "[profiles.migrated]",
            f'push = "{push_mode}"',
            f'merge_to_main = "{pr_value}"',
            f'merge_to_branch = "{pr_value}"',
            f'pr_writes = "{pr_value}"',
            'comments = "ask"',
            'issue_writes = "ask"',
            'cleanup = "ask"',
        ]
    return "\n".join(lines) + "\n" + REPOS_EXAMPLE


def migrate(path: Path, conf: dict[str, str], problems: list[str]) -> str | None:
    """Create the policy file from tower.conf's old keys, unless another process just did; returns the file's text,
    or None when there is nothing to migrate. If it can't be written, the text is still returned (and the reason
    added to problems), so the old keys keep working."""
    text = migration_text(conf, date.today())
    if text is None:
        return None
    try:
        write_file(path, text, if_absent=True)
    except FileExistsError:
        try:
            return path.read_text(encoding="utf-8")  # another hook won the race; read what it wrote
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"{display_path(path)} appeared but can't be read ({error_reason(error)})")
            return None
    except OSError as error:
        problems.append(
            f"couldn't write {display_path(path)} ({error_reason(error)}), so tower.conf's old keys are used for now"
        )
    return text


def write_file(path: Path, text: str, if_absent: bool) -> None:
    """Write path in one step, so no reader ever sees half a file: the text goes to a temporary file, which is then
    renamed over path, or with if_absent hard-linked into place, failing with FileExistsError if path exists. (Like
    card.write_atomically, which this module can't import: card imports state, which imports this.)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".policy-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        if if_absent:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# --- Changing it: tower policy use --------------------------------------------------------------------

HEADER_LINE = re.compile(r"\s*\[")
DEFAULT_PROFILE_LINE = re.compile(r"""\s*(?:default_profile|"default_profile"|'default_profile')\s*=""")
REPOS_HEADER = re.compile(r"""\s*\[\s*(?:repos|"repos"|'repos')\s*\]\s*(?:#.*)?""")


def use(profile: str, slug: str | None = None) -> str:
    """Make profile this machine's default (slug None) or the profile of the repo whose `owner/repo` is slug, by
    changing only that line of the policy file (created if missing, after migrating tower.conf). Returns what
    changed. Raises PolicyError, changing nothing, for an unknown profile or a file that can't be used, or if the
    edited file wouldn't give that profile."""
    current = load()
    if current.broken:
        raise PolicyError(f"{display_path(current.path)} can't be used ({current.broken}); fix it first")
    if profile not in current.profiles:
        names = ", ".join(sorted(current.profiles))
        raise PolicyError(f"{json.dumps(profile)} isn't a profile; the profiles are {names}")
    before = current.for_repo(slug)
    try:
        text = current.path.read_text(encoding="utf-8")
    except FileNotFoundError:  # never set up, or migration couldn't write it
        try:
            conf = ledger.read_conf(ledger.conf_path())
        except (ledger.LedgerError, UnicodeDecodeError) as error:
            raise PolicyError(f"can't read {display_path(ledger.conf_path())} to migrate it ({error})") from error
        text = migration_text(conf, date.today()) or FILE_HEADER + REPOS_EXAMPLE
    except (OSError, UnicodeDecodeError) as error:
        raise PolicyError(f"can't read {display_path(current.path)} ({error_reason(error)})") from error
    edited = set_repo(text, slug, profile) if slug else set_default(text, profile)
    try:
        after = from_document(current.path, parse_toml(edited), [], current.machine_wip).for_repo(slug)
    except TomlError as error:
        raise PolicyError(f"the edited file wouldn't parse ({error}), so nothing was changed") from error
    if after.profile != profile:
        raise PolicyError(f"the edited file would still give {after.profile} ({after.why}), so nothing was changed")
    try:
        write_file(current.path, edited, if_absent=False)
    except OSError as error:
        raise PolicyError(f"can't write {display_path(current.path)} ({error_reason(error)})") from error
    return f"{slug or 'default profile'}: {before.profile} -> {profile}"


def file_lines(text: str) -> list[str]:
    """text's lines, without the empty one after a final newline (put back by file_text())."""
    lines = text.split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines


def file_text(lines: list[str]) -> str:
    """lines as file text, ending in a newline."""
    return "\n".join(lines) + "\n"


def set_default(text: str, profile: str) -> str:
    """text with its top-level default_profile line set to profile, or one added after the opening comments."""
    lines = file_lines(text)
    first_table = next((i for i, line in enumerate(lines) if HEADER_LINE.match(line)), len(lines))
    new_line = f"default_profile = {json.dumps(profile)}"
    for index in range(first_table):
        if DEFAULT_PROFILE_LINE.match(lines[index]):
            lines[index] = new_line
            return file_text(lines)
    insert = 0
    while insert < first_table and (not lines[insert].strip() or lines[insert].lstrip().startswith("#")):
        insert += 1
    lines[insert:insert] = [new_line, ""] if insert < len(lines) and lines[insert].strip() else [new_line]
    return file_text(lines)


def set_repo(text: str, slug: str, profile: str) -> str:
    """text with slug's line in [repos] set to profile (any other line for it, in any letter case, removed), or one
    added at the end of [repos], which is created if missing."""
    lines = file_lines(text)
    new_line = f"{json.dumps(slug)} = {json.dumps(profile)}"
    header = next((i for i, line in enumerate(lines) if REPOS_HEADER.fullmatch(line)), None)
    if header is None:
        return file_text([*lines, *([""] if lines and lines[-1].strip() else []), "[repos]", new_line])
    end = next((i for i in range(header + 1, len(lines)) if HEADER_LINE.match(lines[i])), len(lines))
    found = [i for i in range(header + 1, end) if line_key(lines[i], i + 1) == slug.lower()]
    if found:
        lines[found[0]] = new_line
        for index in reversed(found[1:]):
            del lines[index]
        return file_text(lines)
    insert = end
    while insert > header + 1 and not lines[insert - 1].strip():
        insert -= 1
    lines.insert(insert, new_line)
    return file_text(lines)


def line_key(text: str, number: int) -> str | None:
    """The lower-cased key of a `key = value` line, or None for any other line."""
    line = LineReader(text, number)
    line.skip_space()
    try:
        if line.at_end():
            return None
        key = line.key()
    except TomlError:
        return None
    line.skip_space()
    return key.lower() if line.peek() == "=" else None


# --- The TOML subset ----------------------------------------------------------------------------------


class TomlError(ValueError):
    """The file isn't in the part of TOML parse_toml() reads (or isn't TOML at all)."""

    def __init__(self, line: int, message: str) -> None:
        """A problem on line (counted from 1)."""
        super().__init__(f"line {line}: {message}")


BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
INTEGER = re.compile(r"[+-]?(?:0|[1-9](?:_?[0-9])*)")
ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


def parse_toml(text: str) -> dict[str, object]:
    """Parse text in the part of TOML a policy uses: [table] and [a.b] headers, `key = value` lines with bare or
    quoted keys, basic and literal strings, whole numbers, true/false and # comments. Raises TomlError on anything
    else (arrays, inline tables, dotted keys, floats, dates, multi-line strings), on a key or table defined twice,
    and on what TOML itself forbids."""
    root: dict[str, object] = {}
    table = root
    explicit: set[tuple[str, ...]] = set()
    for number, raw in enumerate(text.split("\n"), start=1):
        line = LineReader(raw.removesuffix("\r"), number)
        line.skip_space()
        if line.at_end():
            continue
        if line.peek() == "[":
            table = line.table_header(root, explicit)
            continue
        key = line.key()
        line.skip_space()
        if line.peek() == ".":
            raise line.error("dotted keys aren't supported here; use a [table] header")
        line.expect("=")
        line.skip_space()
        value = line.value()
        line.skip_space()
        if not line.at_end():
            raise line.error("unexpected text after the value")
        if key in table:
            raise line.error(f"{key!r} is defined twice")
        table[key] = value
    return root


class LineReader:
    """Reads one line of a policy file left to right."""

    def __init__(self, text: str, number: int) -> None:
        """Start at the beginning of line number (counted from 1), whose text is text."""
        self.text = text
        self.number = number
        self.pos = 0

    def error(self, message: str) -> TomlError:
        """A TomlError for this line."""
        return TomlError(self.number, message)

    def peek(self) -> str:
        """The next character, or "" at the end of the line."""
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def skip_space(self) -> None:
        """Move past spaces and tabs."""
        while self.peek() in (" ", "\t") and self.peek():
            self.pos += 1

    def at_end(self) -> bool:
        """Whether only a comment (or nothing) is left; a comment may hold no control characters but tab."""
        if self.peek() == "#":
            if any(is_control(char) for char in self.text[self.pos + 1 :]):
                raise self.error("a control character in a comment")
            return True
        return not self.peek()

    def expect(self, char: str) -> None:
        """Move past char, or raise if it isn't next."""
        if self.peek() != char:
            raise self.error(f"expected {char!r}")
        self.pos += 1

    def table_header(self, root: dict[str, object], explicit: set[tuple[str, ...]]) -> dict[str, object]:
        """Read a [a.b] header and return its table, creating the tables on its path as needed."""
        self.pos += 1
        if self.peek() == "[":
            raise self.error("arrays of tables ([[...]]) aren't supported here")
        names: list[str] = []
        while True:
            self.skip_space()
            names.append(self.key())
            self.skip_space()
            if self.peek() != ".":
                break
            self.pos += 1
        self.expect("]")
        self.skip_space()
        if not self.at_end():
            raise self.error("unexpected text after the table header")
        path = tuple(names)
        if path in explicit:
            raise self.error(f"table [{'.'.join(names)}] is defined twice")
        explicit.add(path)
        table = root
        for name in names:
            child = table.setdefault(name, {})
            if not isinstance(child, dict):
                raise self.error(f"{name!r} is already a value, not a table")
            table = child
        return table

    def key(self) -> str:
        """Read a bare key, or a quoted one."""
        if self.peek() in ('"', "'"):
            return self.string()
        bare = BARE_KEY.match(self.text, self.pos)
        if bare is None:
            raise self.error("expected a key")
        self.pos = bare.end()
        return bare.group()

    def value(self) -> object:
        """Read a string, a whole number, or true/false."""
        if self.peek() in ('"', "'"):
            return self.string()
        for word, meaning in (("true", True), ("false", False)):
            if self.text.startswith(word, self.pos):
                self.pos += len(word)
                return meaning
        integer = INTEGER.match(self.text, self.pos)
        if integer is None:
            raise self.error("expected a string, a whole number, or true/false")
        self.pos = integer.end()
        return int(integer.group().replace("_", ""))

    def string(self) -> str:
        """Read a "basic" string (with escapes) or a 'literal' one (without), on one line."""
        quote = self.peek()
        if self.text.startswith(quote * 3, self.pos):
            raise self.error("multi-line strings aren't supported here")
        self.pos += 1
        parts: list[str] = []
        while True:
            char = self.peek()
            if not char:
                raise self.error("a string with no closing quote")
            self.pos += 1
            if char == quote:
                return "".join(parts)
            if is_control(char):
                raise self.error("a control character in a string")
            if char == "\\" and quote == '"':
                parts.append(self.escape())
            else:
                parts.append(char)

    def escape(self) -> str:
        """Read what follows a backslash in a basic string: one of TOML's escapes."""
        char = self.peek()
        self.pos += 1
        if char in ESCAPES:
            return ESCAPES[char]
        if char in ("u", "U"):
            digits = self.text[self.pos : self.pos + (4 if char == "u" else 8)]
            if len(digits) != (4 if char == "u" else 8) or not all(d in "0123456789abcdefABCDEF" for d in digits):
                raise self.error("a \\u or \\U escape without its hex digits")
            self.pos += len(digits)
            code = int(digits, 16)
            if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                raise self.error("a \\u or \\U escape that isn't a Unicode character")
            return chr(code)
        raise self.error(f"an unknown escape \\{char}")


def is_control(char: str) -> bool:
    """Whether char is a control character TOML forbids in strings and comments (all but tab)."""
    return (char < " " and char != "\t") or char == "\x7f"
