/** One run of tower's hub-scan.sh, as the pane draws it. */
export type Scan = {
  /** The script exited 0. */
  ok: boolean
  /** Its full stdout: the hub report. */
  text: string
  /** Why it failed (stderr's last line, or a reason of ours). */
  error: string
  /** When it ran, from $.clock.now(). */
  at: number
  /** "2 of 3" from the WIP line, or "" when absent. */
  wip: string
  /** One label per thing that needs the user: a needs-you card or a waiting session. */
  items: string[]
}

/** The GitHub section from the last full scan (the 30 s refresh skips GitHub). */
export type GithubSection = { lines: string[]; at: number }

/**
 * A pop-up question a worker session relayed for the pane (one file per session in
 * ~/.claude/tower/questions). The worker accepts an answer only when it carries this `id`.
 */
export type RelayedQuestion = {
  id: string
  sessionId: string
  /** The worker's folder: the pane matches it to a worktree of this repo. */
  cwd: string
  askedAt: number
  questions: Array<{ question: string; header: string; multiSelect: boolean; options: Array<{ label: string; description?: string }> }>
}

declare module 'claude-code' {
  interface PluginState {
    'tower-pane': {
      scan: Scan | null
      github: GithubSection | null
      park: number
      questions: RelayedQuestion[]
      /** The options clicked so far, per relayed question id: question text -> label. */
      picks: Record<string, Record<string, string>>
    }
  }
}
