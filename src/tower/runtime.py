"""runtime.py: `tower env`, a runtime slot per worktree and the variables a project derives from it.

Worktrees share one machine: every worker's dev server wants port 3000, and every one points at the same database. A
project that cares declares its resources once, outside the repo, in `<tower dir>/projects/<Project>.toml` (the tower
dir is ~/.claude/tower, or $TOWER_DIR; <Project> is the project name the cards use):

    port_base = 3000                  # PORT = port_base + slot
    db_name = "poot_{slot}"           # DB_NAME
    seed = 'cp -R {main}/data data'   # how a new worktree gets what only the main checkout has; printed, never run

    [ports]                           # more ports, each base + slot
    API_PORT = 8000

    [env]                             # any other variables
    API_URL = "http://localhost:{port}/api"

Each worktree gets a runtime slot: 0 for the main checkout, and for every other worktree the smallest number no other
live worktree of the project holds, kept in `<tower dir>/slots/<Project>.json` so it never changes while the worktree
exists (a dev server keeps its port across sessions). A worktree whose folder is gone gives its slot back.
`tower env` prints the variables as `export` lines; the SessionStart hook (plugin/scripts/session-env.py) writes them
into each session's environment, so the worker's commands run on its own port without being told.

Placeholders, in any string: {slot}, {port} (needs port_base), {main} and {worktree} (paths). In seed, the paths are
shell-quoted for you; elsewhere they're inserted as they are and the whole value is quoted on export. {{ and }} are
literal braces.

The file is a small subset of TOML, read by hand because the hooks run under macOS's Python 3.9, which has no tomllib:
`key = value` lines with a "basic" or 'literal' string or a whole number, `# comments`, and the [ports] and [env] tables.
Anything else is an error naming the line, so a typo never silently drops a setting.

Concurrency: several sessions can start at once, each asking for its slot. The slots file is only read and written
while holding an exclusive lock on `<Project>.lock` beside it (a file never renamed or deleted), and replaced
atomically, so two workers never get the same slot and a crash never leaves half a file.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tower import events, ledger
from tower.gitrepo import NotARepo, git, list_worktrees, repo_name

MAX_PORT = 65535
LINE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*")
TABLE = re.compile(r"\s*\[\s*([^\]]*?)\s*\]\s*(?:#.*)?")
VALUE = re.compile(r"""("(?:[^"\\]|\\.)*"|'[^']*'|[+-]?\d+)\s*(?:#.*)?""")
TOP_LEVEL = {"port_base": int, "db_name": str, "seed": str}
TABLES = ("ports", "env")
RESERVED = ("TOWER_SLOT", "PORT", "DB_NAME")  # set from the top-level settings, never from a table


class RuntimeConfigError(Exception):
    """The project's runtime config can't be used; the message names the file and the line or setting."""


@dataclass(frozen=True)
class ProjectConfig:
    """A project's runtime config. ports and env keep the file's order."""

    port_base: int | None = None
    db_name: str | None = None
    seed: str | None = None
    ports: dict[str, int] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WorktreeEnv:
    """One worktree's runtime: its slot, the variables to export (in order), and the seed command (None for the
    main checkout, or when the config has none)."""

    slot: int
    variables: list[tuple[str, str]]
    seed: str | None


def config_dir() -> Path:
    """Where projects' runtime configs live: <tower dir>/projects."""
    return events.default_dir() / "projects"


def worktree_env(directory: Path) -> tuple[WorktreeEnv | None, Path]:
    """The runtime of the worktree holding directory, and the config file it came from; None (and the path it
    looked at) when the project has no runtime config, in which case no slot is taken. Raises NotARepo outside a git
    repo, LedgerError when tower.conf can't be read, RuntimeConfigError for a bad config or slots file."""
    top = git(directory, "rev-parse", "--show-toplevel")
    if top is None:
        raise NotARepo(f"not a git repo: {directory}")
    repo = repo_name(directory)
    project = ledger.conf_get(ledger.read_conf(ledger.conf_path()), f"project.{repo}", repo)
    path = config_dir() / f"{project}.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, path
    except OSError as error:
        raise RuntimeConfigError(f"{path}: can't read it ({error.strerror or type(error).__name__})") from error
    try:
        config = parse_config(text)
        main = list_worktrees(directory)[0].path
        worktree = os.path.realpath(top)
        slot = 0 if worktree == os.path.realpath(main) else allocate_slot(project, worktree)
        return derive(config, slot, main, top), path
    except RuntimeConfigError as error:
        raise RuntimeConfigError(f"{path}: {error}") from error


def parse_config(text: str) -> ProjectConfig:
    """The config from the file's text. Raises RuntimeConfigError naming the line for anything it doesn't read."""
    top: dict[str, object] = {}
    tables: dict[str, dict[str, object]] = {name: {} for name in TABLES}
    current: dict[str, object] = top
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        table = TABLE.fullmatch(line)
        if table:
            if table.group(1) not in TABLES:
                raise RuntimeConfigError(f"line {number}: unknown table [{table.group(1)}] (only [ports] and [env])")
            current = tables[table.group(1)]
            continue
        pair = LINE.fullmatch(line)
        if pair is None:
            raise RuntimeConfigError(f"line {number}: isn't key = value (keys are letters, digits and _)")
        key, value = pair.group(1), parse_value(pair.group(2), pair.group(1), number)
        if key in current:
            raise RuntimeConfigError(f"line {number}: {key} is set twice")
        if current is top:
            if key not in TOP_LEVEL:
                raise RuntimeConfigError(
                    f"line {number}: unknown setting {key} (use port_base, db_name, seed, or a [ports] or [env] table)"
                )
            if not isinstance(value, TOP_LEVEL[key]):
                kind = "a whole number" if TOP_LEVEL[key] is int else "a quoted string"
                raise RuntimeConfigError(f"line {number}: {key} must be {kind}")
            if key == "seed" and "\n" in str(value):  # it's printed as a comment: a newline would end the comment
                raise RuntimeConfigError(f"line {number}: seed must be one line (join commands with &&)")
        elif current is tables["ports"] and not isinstance(value, int):
            raise RuntimeConfigError(f"line {number}: {key} in [ports] must be a whole number (the base port)")
        current[key] = value
    taken = set(RESERVED)
    for name in [*tables["ports"], *tables["env"]]:
        if name in taken:
            raise RuntimeConfigError(f"{name} is set twice (TOWER_SLOT, PORT and DB_NAME come from the settings)")
        taken.add(name)
    return ProjectConfig(
        port_base=top.get("port_base"),  # type: ignore[arg-type]  # checked against TOP_LEVEL above
        db_name=top.get("db_name"),  # type: ignore[arg-type]
        seed=top.get("seed"),  # type: ignore[arg-type]
        ports={k: int(str(v)) for k, v in tables["ports"].items()},
        env={k: str(v) for k, v in tables["env"].items()},
    )


def parse_value(text: str, key: str, number: int) -> str | int:
    """One value: a "basic" string (with escapes), a 'literal' string (as written) or a whole number."""
    match = VALUE.fullmatch(text)
    if match is None:
        raise RuntimeConfigError(f"line {number}: {key}'s value isn't a quoted string or a whole number")
    value = match.group(1)
    if value.startswith("'"):
        return value[1:-1]
    if value.startswith('"'):
        try:
            decoded = json.loads(value)  # TOML's basic-string escapes are JSON's, bar the rare \UXXXXXXXX
        except ValueError as error:
            raise RuntimeConfigError(f"line {number}: {key}'s string has an escape tower can't read") from error
        return str(decoded)
    return int(value)


def derive(config: ProjectConfig, slot: int, main: str, worktree: str) -> WorktreeEnv:
    """The variables and seed command for a worktree in slot. Raises RuntimeConfigError for a bad placeholder or a
    port past 65535."""
    port = None if config.port_base is None else config.port_base + slot
    values: dict[str, object] = {"slot": slot, "main": main, "worktree": worktree}
    if port is not None:
        values["port"] = port
    extra_ports = [(name, base + slot) for name, base in config.ports.items()]
    for name, number in ([("PORT", port)] if port is not None else []) + extra_ports:
        if not 0 < number <= MAX_PORT:
            raise RuntimeConfigError(f"{name} would be {number} in slot {slot}, outside 1-{MAX_PORT}")
    variables = [("TOWER_SLOT", str(slot))]
    if port is not None:
        variables.append(("PORT", str(port)))
    if config.db_name is not None:
        variables.append(("DB_NAME", fill(config.db_name, "db_name", values)))
    variables += [(name, str(number)) for name, number in extra_ports]
    variables += [(name, fill(value, name, values)) for name, value in config.env.items()]
    seed = None
    if config.seed is not None and slot != 0:
        quoted = {**values, "main": shlex.quote(main), "worktree": shlex.quote(worktree)}
        seed = fill(config.seed, "seed", quoted)
    return WorktreeEnv(slot=slot, variables=variables, seed=seed)


def fill(template: str, setting: str, values: dict[str, object]) -> str:
    """template with its placeholders filled from values. Raises RuntimeConfigError for an unknown one."""
    try:
        return template.format_map(values)
    except KeyError as error:
        name = error.args[0]
        if name == "port":
            raise RuntimeConfigError(f"{setting}: {{port}} needs port_base") from error
        raise RuntimeConfigError(
            f"{setting}: unknown placeholder {{{name}}} (use {{slot}}, {{port}}, {{main}} or {{worktree}})"
        ) from error
    except (ValueError, IndexError, AttributeError) as error:
        raise RuntimeConfigError(f"{setting}: can't fill in {template!r} ({error}); write {{{{ for a brace") from error


def allocate_slot(project: str, worktree: str) -> int:
    """worktree's slot in project (1 or more), taking the smallest free one the first time. Holds the project's lock
    while reading and writing the slots file. Raises RuntimeConfigError when the file is damaged (guessing would hand
    out slots already in use) or can't be written."""
    directory = events.default_dir() / "slots"
    path = directory / f"{project}.json"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / f"{project}.lock", "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # released when the file closes
            held = read_slots(path)
            live = {p: s for p, s in held.items() if os.path.isdir(p)}  # a removed worktree's slot is free again
            slot = live.get(worktree)
            if slot is None:
                taken = set(live.values())
                slot = next(n for n in range(1, len(live) + 2) if n not in taken)
                live[worktree] = slot
            if live != held:
                write_atomically(path, json.dumps(live, indent=2, sort_keys=True) + "\n")
            return slot
    except OSError as error:
        raise RuntimeConfigError(f"can't keep slots in {path} ({error.strerror or type(error).__name__})") from error


def read_slots(path: Path) -> dict[str, int]:
    """The slots file as {worktree path: slot}; empty when it doesn't exist yet."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        held = json.loads(text)
    except ValueError:
        held = None
    if not isinstance(held, dict) or not all(isinstance(s, int) and s > 0 for s in held.values()):
        raise RuntimeConfigError(f"the slots file {path} is damaged; fix or delete it (slots are handed out again)")
    return {str(p): int(s) for p, s in held.items()}


def write_atomically(path: Path, text: str) -> None:
    """Replace path with text in one step (a temporary file beside it, then a rename)."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def render_exports(env: WorktreeEnv) -> str:
    """The runtime as `tower env` prints it: `export NAME=value` lines a shell can eval, then the seed as a comment."""
    lines = [f"export {name}={shlex.quote(value)}" for name, value in env.variables]
    if env.seed is not None:
        lines.append(f"# seed (once, for what only the main checkout has): {env.seed}")
    return "\n".join(lines) + "\n"
