/** One Claude session open in a worktree, as `tower state --json` lists it. activity: busy, waiting (on you) or idle. */
export type StateSession = { name: string; kind: string; activity: string; waiting_for: string }

/** One worktree of `tower state --json`: its effective state (status), why (reason), and the facts behind it. */
export type StateWorktree = {
  label: string
  branch: string | null
  path: string
  is_main: boolean
  /** needs-you, running, waiting-ci, blocked, done, ready-to-clean, no-card or main. */
  status: string
  reason: string
  holds_slot: boolean
  /** What the card itself says; null without one. */
  card: { status: string; ticket: string; updated: string; pr: string } | null
  /** A running card untouched for over 24h. */
  stale: boolean
  sessions: StateSession[]
  unshipped_commits: number | null
  dirty: boolean | null
  pr: { number: number; state: string; url: string } | null
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
  /** One label per worktree that needs the user, for toasts: only new labels toast. */
  items: string[]
}

/** The GitHub section from the last full hub-scan.sh run (only Refresh GitHub runs it). */
export type GithubSection = { lines: string[]; at: number }

declare module 'claude-code' {
  interface PluginState {
    'tower-pane': {
      scan: Scan | null
      github: GithubSection | null
      park: number
      /** The ledger's _backlog.md lines, read with each scan. */
      backlog: string[]
    }
  }
}
