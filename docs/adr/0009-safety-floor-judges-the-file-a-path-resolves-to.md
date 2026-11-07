# The safety floor judges the file a path resolves to, not the text that names it

The safety floor used to ask whenever a Bash command or an edited path merely contained `tower.conf` or
`policy.toml`, so commit messages, heredocs and test fixtures in temp folders stopped unattended workers, whose hook
prompts are the only ones they get (#75). It now resolves each path ending in one of those names, the way the shell
and the program would: relative to the session's folder and every folder the line cds into, through symlinks, and
under its own name into any folder another argument names (`git -C DIR`, a copy's destination). It asks when the
result is tower's file: a `tower.conf` in any `.claude` folder, anything under a `.claude/tower` folder, or what the
hook's own `TOWER_CONF` and `TOWER_DIR` point at. Where the path can't be resolved, it still asks: a variable,
command output or glob in the path, a relative path after a `cd` to a variable, any path beside an argument from a
variable, and any mention in python/node code, which can build a path at run time.

## Considered options

- **Keep matching text, with an exception list** (skip `-m` values, heredoc bodies): each program spells its
  message option differently (`git commit -m`, `-F -`, `gh pr create --body`), and a heredoc can feed a shell, so
  every exception would be a hole or need its own parser.
- **Resolve only the exact path `~/.claude/tower.conf`**: misses another home folder's `.claude` (the tests' temp
  HOME), which the text match caught; any `.claude` folder's tower.conf still counts.
- **Fail open on what can't be resolved**: the floor exists because a prompt injection must not loosen the rules,
  so a path from a variable keeps asking even though it's usually harmless.

## Consequences

- A commit message that spells out the full path (`~/.claude/tower.conf`) still asks: it resolves to the real file,
  and a word's role (message or path) isn't known without per-program parsing.
- The hook looks paths up on disk, each paid for from the work budget, so a command naming thousands of them runs
  out of budget and asks rather than running past the hook's timeout.
- A symlink an earlier command of the same line creates isn't there yet when the hook resolves the path; text
  matching never stopped that route either (a glob or a copy without the name got through), and Claude Code's own
  permission rules (a write-deny on `~/.claude/tower`) are the backstop for it.
