"""rules.py: `tower rules`, which links this project's personal rules file into the current worktree.

A repo may already have a team CLAUDE.md. Personal rules must load next to it without ever being committed. Claude
Code loads a git-ignored CLAUDE.local.md beside CLAUDE.md, but that file exists only in the worktree where it was
made, so each worktree's CLAUDE.local.md holds one import line pointing at a single shared file Claude Code protects:

    <worktree>/CLAUDE.local.md   ->   @~/.claude/tower/rules/<Project>.md

The rules stay on this machine (work rules never leave the work Mac). The rules file itself is created by the agent's
Write tool (or by you), so creating it goes through the same approval prompt as every later edit.
"""

from __future__ import annotations

from pathlib import Path

from tower import ledger
from tower.gitrepo import git, repo_name

RULES_HOME = Path(".claude") / "tower" / "rules"  # under your home folder; created by setup.sh


class RulesError(Exception):
    """The rules can't be linked safely; the message says why and how to fix it. Nothing was written."""


def link_rules(worktree_dir: Path) -> tuple[Path, list[str]]:
    """Add the import line to the worktree's CLAUDE.local.md (never overwriting it) and return the rules file's path,
    plus warnings for stderr. Raises RulesError (and LedgerError, NotARepo) before writing anything."""
    conf = ledger.read_conf(ledger.conf_path())
    repo = repo_name(worktree_dir)
    project = ledger.project_dir(repo, conf).name  # the same project name the cards use
    rules_dir = Path.home() / RULES_HOME
    rules_file = rules_dir / f"{project}.md"
    import_line = f"@~/{(RULES_HOME / project).as_posix()}.md"

    if any(c.isspace() for c in project):
        raise RulesError(
            f"project name '{project}' has a space, which can break the @import.\n"
            f"  Add 'project.{repo}=<NameWithoutSpaces>' to {ledger.conf_path()}."
        )
    if not rules_dir.is_dir():
        raise RulesError(f"{rules_dir} is missing — run setup.sh on this machine")
    toplevel = git(worktree_dir, "rev-parse", "--show-toplevel")
    if toplevel is None:
        raise RulesError(f"can't find the worktree's top folder from {worktree_dir}")
    local_md = Path(toplevel) / "CLAUDE.local.md"
    # A personal file git would pick up could be committed: checked before writing (check-ignore works on paths that
    # don't exist yet). setup.sh adds CLAUDE.local.md to your global git ignore; this catches a machine where it didn't.
    if git(Path(toplevel), "check-ignore", "-q", str(local_md)) is None:
        raise RulesError(
            f"{local_md} would NOT be git-ignored, so it could be committed. Nothing written.\n"
            '  Fix: echo CLAUDE.local.md >> "$(git config --global core.excludesFile || echo ~/.config/git/ignore)"'
        )

    try:
        existing = local_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    if import_line not in existing.splitlines():
        separator = "" if existing == "" or existing.endswith("\n") else "\n"  # don't glue it to a last unended line
        with open(local_md, "a", encoding="utf-8") as handle:
            handle.write(f"{separator}{import_line}\n")

    warnings = []
    if not rules_file.exists():  # a missing file is just an empty rule set
        warnings.append(f"{rules_file} doesn't exist yet; create it to add the first rule.")
    return rules_file, warnings
