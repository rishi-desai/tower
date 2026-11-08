# Stacks: one worktree per stack, read from git-spice's tracking ref, and a card that follows its worktree

Stacked tickets (#32) are worked with git-spice. The #30 spike (docs/research/git-spice-with-worktrees.md) found that
git-spice restacks, submits and syncs a stack cleanly when the whole stack is in one worktree, and that one worktree
per branch turns every restack and merge into manual, bottom-up steps across worktrees. So a stack gets **one stack
worktree and one worker**, which moves between the stack's branches with `git-spice up`/`down` and creates each
ticket's branch with `git-spice branch create` on top of the one before. "One ticket = one branch = one worktree"
becomes "one stack = one worktree" for stacked tickets: a stack is sequential anyway.

Two choices follow from that:

- **tower reads git-spice's tracking ref (`refs/spice/data`) with `git cat-file`, and never runs git-spice.** Every
  `tower state` (the pane runs one every 30 seconds in each session) needs each branch's parent. `git-spice log
  --json` is git-spice's documented output, but it isn't read-only: on a repo that never used git-spice it
  initializes one (writes the ref), it untracks branches deleted outside it, and its restack check rewrites a
  branch's recorded base commit. A scan must never write to a repo, least of all to a ref the worker's own git-spice
  is updating at that moment. The ref is a tree of JSON blobs (`repo` with the trunk, `branches/<name>` with
  `base.name` and the PR as `change.github.pr.number`, an optional `version`); tower reads only version 1 and says so
  for any other. "Needs restack" is git-spice's own test, done with git: the parent's tip isn't an ancestor of the
  branch. A merged parent is one whose PR GitHub says merged (`--pr`), or, without GitHub, one git-spice submitted
  whose tip is already on the stack's base (`tower merge` merges a stacked PR with a merge commit).
- **A card stays with its worktree, not its branch.** A card is found by its worktree's branch name. A stack
  worktree's branch changes every time its worker creates or moves to another branch, so its card, its kickoff and
  its decisions log (worker memory, ADR 0008) would be lost each time. So the worktree's own git folder
  (`.git/worktrees/<name>/tower-card`) records its card's name when the card is written, and every reader looks there
  before the branch name. Git removes that folder with the worktree, so a later worktree at the same path starts
  without it.

## Considered options

- **Run `git-spice log --json`**: a documented format, PR state with `--cr-status`, but writes (above), needs git-spice
  on every machine running a scan, and a network call for PR state.
- **One worktree per branch** (the spike's other layout): two workers could work two layers at once, but a restack
  skips every branch checked out elsewhere and everything above it, and a sync after a merge leaves the stack in a
  state `git-spice branch submit` refuses.
- **Record the worktree's path in the card** and, when no card has the branch's name, take the card naming this
  worktree: every lookup reads the whole Ledger, a card that can't be read (an iCloud file not downloaded) is missed
  in silence, and telling an orphan from an earlier worktree at the same path takes a guess from file times, which
  Linux doesn't keep.
- **Name a stack worktree's card after the stack's bottom branch**: the bottom changes when it merges and `git-spice
  repo sync` deletes it, so the card would be lost at every merge. The worktree's folder name is stable, but it
  differs from the branch name the hub's starter card already uses.

## Consequences

- If git-spice changes its tracking format, `tower state` says it couldn't read it, and stacks aren't shown; nothing
  else breaks. A test against the real git-spice (pinned in mise.toml) catches the change.
- A parent force-pushed from somewhere else shows as needing a restack only once its local branch is updated (a
  fetch and reset, or a sync), and a merged parent shows without `--pr` only once the merge is fetched. In a stack
  worktree the worker is the one pushing, so neither is the usual case.
- A branch that is checked out in another worktree while its card follows a stack worktree finds that card by its
  name. The one-worktree-per-stack rule keeps this from happening.
- tower writes one small file into a worktree's git folder, next to git's own (`HEAD`, `index`), never into the
  working tree, so it shows in no `git status` or PR.
- One rule says whether a stack worktree is finished (`stacks.open_besides`, `Stack.unmerged_besides`): no other branch
  of its stack is unmerged (a PR closed on GitHub doesn't count). `tower state` uses it for ready-to-clean, `card
  write` to keep the card running when its branch merged, and `tower clean` to refuse; clean and card write fail
  closed when git-spice's tracking can't be read. Without GitHub, a branch counts as merged when its tip, or
  GitHub's copy of it, is on the stack's base, so a squash merge shows only with `--pr`.
- The stack kickoff syncs from the top branch and never after the last merge: a sync deletes the branch the worktree
  is on once it merged, and can't check out the trunk (the main checkout has it), so the worktree would be left on
  no branch. For the same reason a worktree on a detached HEAD (mid-restack) isn't tied to its stack in the report.
- A stack that forks (two branches on one parent) splits into separate branches on the base once that parent merges
  and is synced; git-spice sees it the same way. The worktree then answers only for the branch it has checked out.
- A tracked branch deleted before git-spice noticed (a merged parent removed by hand) drops out of its stack; the
  branch above it sits on what the deleted one was built on and needs a restack (`git-spice repo sync`).
