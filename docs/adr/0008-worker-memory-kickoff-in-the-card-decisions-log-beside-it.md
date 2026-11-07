# Worker memory: the kickoff is a fixed card section, the decisions log a file beside the cards

A worker that compacts (or a fresh session on its worktree) loses the hub's kickoff and every decision it never wrote
down, and can open a PR without the reviews the kickoff asked for (#93). Three parts fix that, re-read by a SessionStart
hook (`worker-memory.py`) on `startup`, `clear` and `compact`:

- **The kickoff is a `## Kickoff` section of the card**, set only by the hub with `tower card kickoff` right after the
  starter card. `tower card write`, the command workers use, refuses a `## Kickoff` section and carries the card's
  kickoff over unchanged, the same way it fills status and PR from facts. `card kickoff` refuses to change a different
  kickoff unless given `--replace`, which the hub passes only for an orphan card from earlier work on the same branch
  name; `--replace` also archives that work's decisions log.
- **The decisions log is its own file, `<Ledger>/_decisions/<card name>`**, one timestamped line per decision or
  discovery, added with `tower decisions add` through an append-only write. It is not a card section: the card stays
  current state, short enough for the hub to read, and overwritten each time, while the log only grows. `tower clean`
  archives it with the card, so a reused branch name starts with an empty log.
- **The hook prints the kickoff, the current card and the log**, under Claude Code's 10,000-character cap for
  SessionStart output (above it, Claude sees only a 2,000-character preview and a file path). The kickoff and card come
  whole; the log keeps its newest lines that fit and names the file for the rest. It skips `resume` and `fork` (the
  transcript still holds everything) and the main checkout (the hub's, where tower never starts a worker). A card or log
  older than the worktree itself is earlier work on a reused branch name, not yet replaced by the hub, so it isn't
  given (macOS keeps the worktree's creation time; elsewhere this check is skipped).

There is no PreCompact hook. Claude Code runs only scripts there (`command`, `http`, `mcp_tool`, never `prompt` or
`agent`), so it can't make the worker update its card or log before compaction; its stdout only steers the summary
(docs/research/precompact-hook.md). The card may be a little behind at compaction; the kickoff is always exact.

## Considered options

- **Decisions as a card section the writer only appends to**: one file, archived with the card for free, but the card
  would grow without limit, stop being current state, and every append would rewrite the whole card under a lock.
- **The log beside the card as `<card name>.decisions.md`**: every reader that lists the Ledger's `*.md` as cards (the
  orphan scan, the cards-only projects of `tower state --all`) would have to learn to skip it. A `_decisions` folder is
  skipped already, like `_archive`.
- **The log in the worktree (`.git/worktrees/<name>/`)**: no vault needed, but cleanup deletes it with the worktree,
  and the user can't read it in Obsidian.

## Consequences

- Workers can still edit the card or log files directly; the guarantee is the core's interface, the same as for the
  card's frontmatter. The approval hook doesn't guard either file.
- A worker that writes its own card before the hub's starter card still gets the kickoff: `card kickoff` adds it to
  whichever card is there.
- Card writes take no lock, as before: if the worker's `card write` reads its card just before the hub's `card kickoff`
  lands and writes just after, the kickoff is lost. The window is milliseconds right after the start, and `card
  kickoff` run again restores it (the same kickoff changes nothing otherwise). A card that exists but can't be read
  loses its kickoff on the next `card write`, with a warning, the same as its ticket.
- A card far past its ~30 lines is cut in the hook's output (the kickoff never is), and the log keeps its newest lines;
  both name the file to read for the rest.
