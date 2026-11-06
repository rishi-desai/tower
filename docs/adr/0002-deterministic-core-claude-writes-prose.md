# A deterministic core owns tower's state; Claude writes only the prose

Until now tower's state came from agents following skill instructions: a worker decides what its card says, and the
hub works out status from those cards in prose and bash. Cards are overwritten, so there is no history, and two
readers (the hub and the pane) can reach different answers from the same facts. So tower's facts and the rules over
them move into a small Python core, `src/tower/`: same inputs, same output, unit-tested. Hooks record what happened
as events in one append-only log (`~/.claude/tower/events.jsonl`, all repos), and later the core computes each
worktree's effective state, renders cards' frontmatter and checks policy from those facts. Claude keeps writing the
parts only a model can: what was done, why it matters, the decision it needs from you.

Each event is one JSON line: `ts` (UTC), `type`, `session_id`, `repo`, `worktree`, `branch`, then `data`, the
type-specific payload, kept under its own key so a payload field can never collide with a common one. Readers in the
core depend on this shape: add fields, don't rename or remove them.

The core is standard library only, because the plugin's hooks import it straight from the plugin through the
`plugin/lib/tower` symlink, with nothing installed. For the same reason the modules a hook imports stay importable
on Python 3.9, the `python3` a stock Mac gives a hook. Since T06 (#24) the whole core does, because hub-scan.sh runs
the `tower` CLI from the plugin the same way (`plugin/scripts/run-tower.py`); only the installed dev command needs 3.11+.

## Considered options

- **Keep state in prose and instructions, and tighten the skills**: no code to maintain, but no tests, no history,
  and every reader re-derives status its own way, which is how the hub and the pane came to disagree.
- **Hooks call an installed `tower` CLI**: the core could use 3.11+ freely, but every machine would need the CLI on
  the hooks' `PATH`, and a missing install silently drops events. Importing from the plugin works wherever the
  plugin does.
- **A database (SQLite) instead of a JSON Lines file**: queries would be easier, but a file under a lock already
  takes appends from many hook processes safely, needs no schema migrations, and stays readable with `tail` and `jq`.
  The core can build an index from the log later if reads get slow.
