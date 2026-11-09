/** A worktree's effective state, as src/tower/state.py's STATUSES name them. */
export type Status = 'needs-you' | 'running' | 'waiting-ci' | 'blocked' | 'done' | 'ready-to-clean' | 'no-card' | 'card-unknown' | 'main'

/** One Claude session open in a worktree, as `tower state --json` lists it. waiting means waiting on you. */
export type StateSession = { name: string; kind: string; activity: 'busy' | 'waiting' | 'idle'; waiting_for: string }

/** One worktree of `tower state --json`: its effective state (status), why (reason), and the facts behind it. */
export type StateWorktree = {
  label: string
  branch: string | null
  path: string
  /** The card's file name in the Ledger, as the core names it: open this, don't rebuild it from label. */
  card_file: string
  is_main: boolean
  status: Status
  reason: string
  holds_slot: boolean
  /** What the card itself says; null without one. */
  card: { status: string; ticket: string; updated: string; pr: string } | null
  /** A running card untouched for over 24h. */
  stale: boolean
  /** The card couldn't be checked (no Ledger found, or its file unreadable): card is null, but not because there's none. */
  card_unknown: boolean
  sessions: StateSession[]
  unshipped_commits: number | null
  dirty: boolean | null
  /** head_oid: the branch's last commit in the PR; null when gh didn't say. */
  pr: { number: number; state: string; url: string; head_oid: string | null } | null
}

/**
 * What `tower state --json` prints (src/tower/state.py's to_json): the one truth the hub, the pane and the WIP count
 * share. The core only adds keys, never renames or removes them.
 */
export type TowerState = {
  repo: string
  main_checkout: string
  ledger: string | null
  base: string | null
  wip_limit: number
  slots: number
  at_limit: boolean
  sessions_source: string
  notes: string[]
  worktrees: StateWorktree[]
  unshipped: Array<{ label: string; commits: number }>
  orphan_cards: Array<{ name: string; status: string; path: string }>
  /** Pairs of worktrees (by label) that changed the same files; the hub lists each under Housekeeping too. */
  overlaps: Array<{ worktrees: [string, string]; files: string[] }>
  /** The cleanup the hub suggests (its Housekeeping group): a worktree or orphan card's label and what to do. */
  housekeeping: Array<{ label: string; note: string }>
  /** The repo's approval policy (src/tower/policy.py); missing from a core older than tower 0.13. */
  policy?: StatePolicy | null
  /** The stacks git-spice tracks, in merge order (src/tower/stacks.py); missing from a core older than tower 0.16. */
  stacks?: StateStack[]
  /**
   * Where each issue in progress runs, by its ticket: "#93" -> "running in feat-93-x" (src/tower/state.py's
   * issues_in_progress, which `tower scan` marks its issue rows from too); missing from a core older than tower 0.16.
   */
  in_progress?: Record<string, string>
}

/**
 * One stack git-spice tracks: the branch its bottom is built on, the policy key its merges are judged by, the
 * worktrees holding its branches (one, for a stack worktree), and its branches in merge order, bottom first.
 */
export type StateStack = {
  base: string
  merge_key: 'merge_to_main' | 'merge_to_branch'
  /** Which branch merges next and whether that waits for the user, as the hub's STACKS header says it. */
  next_note: string
  worktrees: string[]
  branches: StateStackBranch[]
}

/**
 * One branch of a stack. depth: 0 for the bottom. worktree: the path holding it checked out. pr_state: GitHub's,
 * null when PRs weren't read. restack: why it needs a restack, '' when it doesn't. next: the one that may merge now.
 */
export type StateStackBranch = {
  name: string
  /** The ticket guess from its name ("#41", or "none"). */
  ticket: string
  parent: string
  depth: number
  worktree: string | null
  pr: number | null
  pr_state: string | null
  merged: boolean
  restack: string
  next: boolean
}

/** Which of an agent's actions ask you first in this repo: its profile, why it applies, and each key's value. */
export type StatePolicy = {
  profile: string
  why: string
  push: 'every' | 'force' | 'none'
  merge_to_main: 'ask' | 'auto'
  merge_to_branch: 'ask' | 'auto'
  comments: 'ask' | 'auto'
  issue_writes: 'ask' | 'auto'
  pr_writes: 'ask' | 'auto'
  cleanup: 'ask' | 'auto'
  problems: string[]
}

/** One run of `tower state --json`, as the pane draws it. */
export type Scan = {
  /** The command exited 0 and printed JSON. */
  ok: boolean
  /** What it printed; null when it failed. */
  state: TowerState | null
  /** Why it failed (stderr's last line, or a reason of ours). */
  error: string
  /** When it ran, from $.clock.now(). */
  at: number
}

/** The GitHub section from the last full `tower scan` run (only Refresh GitHub runs it). */
export type GithubSection = { lines: string[]; at: number }

declare module 'claude-code' {
  interface PluginState {
    tower: {
      scan: Scan | null
      github: GithubSection | null
      park: number
      /** The ledger's _backlog.md lines, read with each scan; error says why it couldn't be read ('' when it could). */
      backlog: { lines: string[]; error: string }
    }
  }
}
