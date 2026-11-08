import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren } from 'claude-code'

import type { GithubSection, Scan, StateSession, StateStack, StateWorktree, Status, TowerState } from '../types'

// The pane mod: tower's hub report as a live pane beside the chat, a status-line summary, a toast when something new
// needs the user, and in each worker a "0: back to the hub" band. Read-only by design: it only ever runs tower's own
// `tower state --json` and `tower scan` (for GitHub only), both through this plugin's run-tower.py, `orca terminal
// list/switch` and `orca worktree ps` (which only change the tab Orca shows), and reads
// the ledger's backlog file. These commands don't go through the Bash tool, so tower's approval hook never sees them;
// nothing here may write anything.

const PANE = 'tower'
const LOCAL_EVERY_MS = 30_000 // cards, worktrees, live sessions: cheap, so often
// The pane's own background. Claude Code fills panes with a palette grey that Gruvbox Dark turns tan;
// this paints over it with Ghostty's background (~/.config/ghostty/config). '' keeps Claude Code's fill.
const PANE_BACKGROUND = '#0e0e0e'
const ISSUES_SHOWN = 10 // the GitHub list stays short: the newest ten, then "+ N more"
const SCAN_ISSUE_LIMIT = 30 // `tower scan`'s `gh issue list --limit`: a full list may mean there are more
const scanAtom = atom({ plugin: 'tower', key: 'scan' } as const, null)
const parkAtom = atom({ plugin: 'tower', key: 'park' } as const, 0) // bumped to clear the park field
const githubAtom = atom({ plugin: 'tower', key: 'github' } as const, null)
const backlogAtom = atom({ plugin: 'tower', key: 'backlog' } as const, { lines: [] as string[], error: '' })

// ---------- reading tower state ----------

/** The worktrees whose effective state is needs-you: the rows under "Needs you", counted by the status line. */
export function needsYou(state: TowerState): StateWorktree[] {
  return state.worktrees.filter(w => w.status === 'needs-you')
}

/** A worktree's sessions that are waiting on the user (a question or an approval). */
export function waitingIn(w: StateWorktree): StateSession[] {
  return w.sessions.filter(s => s.activity === 'waiting')
}

/**
 * What a waiting session needs, as Claude Code says it: "input needed" for a question, "approve Bash" for an approval.
 * Sessions read from the event log (and some background ones) don't say, so the note stays neutral.
 */
export function waitingNote(session: StateSession): string {
  return session.waiting_for || 'waiting on you'
}

/**
 * One label per thing that needs the user, for toasts: each waiting session, or the worktree when none waits (its
 * card asks). A label that wasn't there last scan is something new; a label never carries text that can change
 * while the same wait goes on.
 */
export function toastLabels(state: TowerState): string[] {
  return needsYou(state).flatMap(w => {
    const waiting = waitingIn(w)
    if (waiting.length) return waiting.map(s => `${w.label}: session ${s.name} is waiting`)
    return [w.card?.status === 'needs-you' ? `${w.label}: card needs you` : `${w.label}: needs you`]
  })
}

/** The worktree holding a folder: the longest path that contains it (worktrees can sit inside the main checkout). */
export function worktreeAt(state: TowerState, cwd: string): StateWorktree | undefined {
  return [...state.worktrees]
    .sort((a, b) => b.path.length - a.path.length)
    .find(w => w.path !== '' && (cwd === w.path || cwd.startsWith(`${w.path}/`)))
}

/**
 * A stack as the pane draws it: a header (its base, the worktree holding it by label, never a path, and the core's
 * word on who merges next) and one row per branch in merge order: its mark, the merge-order number indented by depth ("  2."), and its
 * text ("feat-b #12 · needs restack: ...").
 */
export function stackLines(state: TowerState, stack: StateStack): { header: string; rows: Array<{ mark: string; text: string }> } {
  const labels = stack.worktrees.map(path => state.worktrees.find(w => w.path === path)?.label).filter(Boolean)
  const header = [`on ${stack.base}`, ...labels.map(label => `in ${label}`), stack.next_note].join(' · ')
  const rows = stack.branches.map((b, i) => {
    const flags = [b.merged ? 'merged' : '', b.next ? 'next to merge' : '', b.restack ? `needs restack: ${b.restack}` : '']
    const pr = b.pr === null ? 'no PR' : `#${b.pr}${b.pr_state ? ` ${b.pr_state.toLowerCase()}` : ''}`
    return { mark: `${'  '.repeat(b.depth)}${i + 1}.`, text: [`${b.name} ${pr}`, ...flags.filter(Boolean)].join(' · ') }
  })
  return { header, rows }
}

// ---------- reading `tower scan`'s GitHub section ----------

export type Issue = { number: string; title: string; isReady: boolean }
export type Github = { prs: string[]; issues: Issue[]; note: string }

/** Lines of one "== NAME ==" section of `tower scan`'s report, without its header. */
function sectionLines(text: string, name: string): string[] {
  const lines = text.split('\n')
  const start = lines.findIndex(line => line.startsWith(`== ${name}`))
  if (start < 0) return []
  const rest = lines.slice(start + 1)
  const end = rest.findIndex(line => line.startsWith('== '))
  return (end < 0 ? rest : rest.slice(0, end)).filter(line => line.trim() !== '')
}

/** PRs and every open issue (labels at the end of each row: "- #39 title [ready-for-agent] [bug]"). */
export function parseGithub(lines: string[]): Github {
  const github: Github = { prs: [], issues: [], note: '' }
  let group: 'prs' | 'issues' | '' = ''
  for (const line of lines) {
    if (line.startsWith('-- my open PRs')) group = 'prs'
    else if (line.startsWith('-- open issues')) group = 'issues'
    else if (line.startsWith('- #') && group === 'prs') github.prs.push(line.slice(2))
    else if (line.startsWith('- #') && group === 'issues') {
      const m = line.match(/^- #(\d+) (.*?)((?: \[[^\]]+\])*)$/)
      if (m) github.issues.push({ number: m[1] ?? '', title: m[2] ?? '', isReady: (m[3] ?? '').includes('[ready-for-agent]') })
    } else if (line.startsWith('(') && line !== '(none)') github.note = line
  }
  return github
}

/** Whether an issue is in progress: the core's in_progress (`tower state --json`) names its ticket. */
export function isInProgress(issue: Issue, inProgress: Record<string, string>): boolean {
  return Boolean(inProgress[`#${issue.number}`]) // every key starts with #, so none is an Object property
}

/**
 * The issue rows the pane draws: every ready-for-agent issue not in progress (each must stay startable), then the
 * newest others not in progress, then the newest in progress, until there are ISSUES_SHOWN rows. Within each group,
 * the newest first, as `tower scan` lists them.
 */
export function issuesToShow(issues: Issue[], inProgress: Record<string, string>): Issue[] {
  const open = issues.filter(i => !isInProgress(i, inProgress))
  const ready = open.filter(i => i.isReady)
  const rest = [...open.filter(i => !i.isReady), ...issues.filter(i => isInProgress(i, inProgress))]
  return [...ready, ...rest.slice(0, Math.max(0, ISSUES_SHOWN - ready.length))]
}

/** "#45 title [branch] review:APPROVED DRAFT" -> "#45 title · approved · draft"; review:none is dropped. */
export function prLine(raw: string): string {
  const m = raw.match(/^(#\d+) (.*?) \[[^\]]*\] review:(\S+)( DRAFT)?$/)
  if (!m) return raw
  const review = ({ APPROVED: 'approved', CHANGES_REQUESTED: 'changes requested', REVIEW_REQUIRED: 'review needed' } as Record<string, string>)[m[3] ?? ''] ?? ''
  return [`${m[1]} ${m[2]}`, review, m[4] ? 'draft' : ''].filter(Boolean).join(' · ')
}

// ---------- small formatting helpers ----------

function clockTime(ms: number): string {
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** "12m", "3h", "2d" since a card's `updated` (local time, as the card writes it). */
export function ageOf(updated: string, now: number): string {
  const then = Date.parse(updated)
  if (Number.isNaN(then)) return ''
  const minutes = Math.max(0, Math.round((now - then) / 60_000))
  if (minutes < 1) return 'now'
  if (minutes < 60) return `${minutes}m`
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)}h`
  return `${Math.round(minutes / 1440)}d`
}

const BADGE_WIDTH = 11
/** Badge text and colour per status: dark text on a coloured block reads on any pane background. */
function badgeOf(status: Status | 'session-waiting', isStale: boolean): { label: string; color: string } {
  if (isStale) return { label: 'STALE', color: 'error' }
  switch (status) {
    case 'needs-you': return { label: 'NEEDS YOU', color: 'warning' }
    case 'session-waiting': return { label: 'WAITING', color: 'warning' } // a needs-you row whose session waits on you
    case 'blocked': return { label: 'BLOCKED', color: 'error' }
    case 'running': return { label: 'RUNNING', color: 'suggestion' }
    case 'waiting-ci': return { label: 'WAITING CI', color: 'inactive' }
    case 'done': return { label: 'DONE', color: 'success' }
    case 'ready-to-clean': return { label: 'CLEAN UP', color: 'success' }
    case 'main': return { label: 'MAIN', color: 'inactive' }
    default: return { label: 'NO CARD', color: 'inactive' }
  }
}

// ---------- actions (pane buttons) ----------
// Anything that changes something is sent to this session's Claude as a prompt, so tower's approval
// prompts still apply. The pane itself only switches Orca's tab, opens Obsidian, and messages sessions.

const ASK_PREFIX = 'Using the tower hub skill: '

/** Sends a request to this session's Claude, as if typed. */
async function askClaude($: EngineInterface, request: string): Promise<void> {
  try {
    await $.prompt.submit({ text: `${ASK_PREFIX}${request}` })
    $.ui.toast(`Sent to Claude: ${request}`)
  } catch (error) {
    $.ui.toast(`Couldn't send to Claude: ${String(error).slice(0, 120)}`)
  }
}

/** Opens a card note in Obsidian through its obsidian:// link (launches the app; writes nothing). */
async function openCard($: EngineInterface, path: string): Promise<void> {
  try {
    const ran = await $.process.run(['open', `obsidian://open?path=${encodeURIComponent(path)}`], { timeoutMs: 10_000 })
    $.ui.toast(ran.exitCode === 0 ? 'Opened the card in Obsidian' : `Couldn't open Obsidian: ${ran.stderr.trim().slice(0, 120)}`)
  } catch (error) {
    $.ui.toast(`Couldn't open Obsidian: ${String(error).slice(0, 120)}`)
  }
}

/** One terminal of `orca terminal list --json`. Its pane is `${tabId}:${leafId}`, the key `orca worktree ps` uses. */
export type OrcaTerminal = { handle: string; title: string; connected: boolean; lastOutputAt: number; agentIdentity?: string; tabId?: string; leafId?: string }
/** One agent of `orca worktree ps --json`. Orca's hooks set state: working, waiting or blocked (on you), done. */
type OrcaAgent = { paneKey?: string; state?: string; updatedAt?: number }

/** A failed command's reason: the last line of its stderr, else its exit code. */
function lastLine(stderr: string, exitCode: number): string {
  return stderr.trim().split('\n').pop() || `exit ${exitCode}`
}

/** Why an orca command failed: it prints a JSON error on stdout, other failures go to stderr. */
function orcaError(ran: { stdout: string; stderr: string; exitCode: number }): string {
  try {
    const message = (JSON.parse(ran.stdout) as { error?: { message?: string } }).error?.message
    if (message) return message
  } catch {
    // not JSON: fall through to stderr
  }
  return lastLine(ran.stderr, ran.exitCode)
}

/**
 * The terminal to land on in a worktree: the one whose agent Orca says is waiting on the user (the newest such, if
 * several), else the Claude terminal that printed last, else any terminal that printed last.
 */
export function pickTerminal(terminals: OrcaTerminal[], waitingPanes: string[]): { pick: OrcaTerminal | undefined; isWaiting: boolean } {
  const live = terminals.filter(t => t.connected)
  for (const pane of waitingPanes) {
    const waiting = live.find(t => `${t.tabId}:${t.leafId}` === pane)
    if (waiting) return { pick: waiting, isWaiting: true }
  }
  const claude = live.filter(t => t.agentIdentity === 'claude')
  return { pick: [...(claude.length ? claude : live)].sort((a, b) => b.lastOutputAt - a.lastOutputAt)[0], isWaiting: false }
}

/** The panes in a worktree whose agent Orca marks as waiting on the user, newest first; null when Orca can't say. */
async function waitingPanes($: EngineInterface, worktreePath: string): Promise<string[] | null> {
  const ran = await $.process.run(['orca', 'worktree', 'ps', '--limit', '500', '--json'], { timeoutMs: 15_000 })
  if (ran.exitCode !== 0) return null
  try {
    const worktrees = (JSON.parse(ran.stdout) as { result?: { worktrees?: Array<{ path?: string; agents?: OrcaAgent[] }> } }).result?.worktrees ?? []
    const agents = worktrees.find(w => w.path === worktreePath)?.agents ?? []
    return agents
      .filter(a => (a.state === 'waiting' || a.state === 'blocked') && a.paneKey)
      .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
      .map(a => a.paneKey ?? '')
  } catch {
    return null
  }
}

/**
 * Brings a worktree's terminal forward in Orca, then Orca itself: the terminal waiting on the user when Orca knows
 * one (see pickTerminal). `name` is only for the toast.
 */
async function openInOrca($: EngineInterface, name: string, worktreePath: string): Promise<void> {
  try {
    // Both Orca questions at once: a click shouldn't wait for two round trips in a row.
    const [listed, panes] = await Promise.all([
      $.process.run(['orca', 'terminal', 'list', '--worktree', `path:${worktreePath}`, '--json'], { timeoutMs: 15_000 }),
      waitingPanes($, worktreePath).catch(() => null),
    ])
    if (listed.exitCode !== 0) {
      $.ui.toast(`Couldn't ask Orca: ${orcaError(listed).slice(0, 120)}`)
      return
    }
    const terminals = (JSON.parse(listed.stdout) as { result?: { terminals?: OrcaTerminal[] } }).result?.terminals ?? []
    const { pick, isWaiting } = pickTerminal(terminals, panes ?? [])
    if (!pick) {
      $.ui.toast(`No open Orca terminal in ${name}`)
      return
    }
    const switched = await $.process.run(['orca', 'terminal', 'switch', '--terminal', pick.handle, '--json'], { timeoutMs: 15_000 })
    if (switched.exitCode !== 0) {
      $.ui.toast(`Couldn't switch Orca: ${orcaError(switched).slice(0, 120)}`)
      return
    }
    await $.process.run(['open', '-a', 'Orca'], { timeoutMs: 10_000 }) // the tab is switched; this only raises the window
    // Without Orca's agent states the pick is a guess: say so rather than land quietly on the wrong terminal.
    const how = isWaiting ? ' (waiting on you)' : panes === null ? " (Orca didn't say which terminal waits: the newest Claude one)" : ''
    $.ui.toast(`Orca: ${name} · ${pick.title}${how}`)
  } catch (error) {
    $.ui.toast(`Couldn't reach Orca: ${String(error).slice(0, 120)}`)
  }
}

const NUDGE = 'From the tower pane (the user pressed Nudge): please update your tower status card now with the status-card skill: where you are, what is left, and whether you need anything from the user. Nothing else is needed.'

/** Asks each session in a worktree to update its card. A message only; it changes nothing on GitHub. */
async function nudge($: EngineInterface, names: string[]): Promise<void> {
  const sent: string[] = []
  for (const name of names) {
    try {
      // A message that isn't delivered resolves isDelivered: false (with a reason); it doesn't throw.
      const result = await $.session.send({ to: name, text: NUDGE })
      if (result.isDelivered) sent.push(name)
      else $.ui.toast(`Couldn't nudge ${name}: ${result.reason ?? 'not delivered'}`)
    } catch (error) {
      $.ui.toast(`Couldn't nudge ${name}: ${String(error).slice(0, 100)}`)
    }
  }
  if (sent.length) $.ui.toast(`Nudged ${sent.join(', ')}`)
}

// ---------- scanning ----------

// Module state: starts over when the mod reloads (session.start sets it again).
let lastItems: string[] | null = null // null until the first scan: no toasts for what was already there
let sessionCwd = '' // this session's folder: which worktree it works in
let inOrca = false // this session runs in an Orca terminal (Orca sets ORCA_TERMINAL_HANDLE), so it can jump

/**
 * Refreshes, and never throws: $.process.run rejects when a command can't start or outlives its
 * timeout, and an escaped rejection would leave the pane frozen on an old scan with no reason shown.
 */
async function refresh($: EngineInterface, withGithub: boolean): Promise<void> {
  const local = scanOnce($).catch(async error => {
    const at = await $.clock.now()
    await update($, scanAtom, () => ({ ok: false, state: null, error: `scan didn't run: ${String(error).slice(0, 200)}`, at }))
    $.ui.status(undefined)
  })
  // `tower scan` is slow (it runs every section, GitHub's included): run it beside the local scan, not after it.
  const github = withGithub ? loadGithub($).catch(error => $.ui.toast(`Couldn't load GitHub: ${String(error).slice(0, 120)}`)) : null
  await Promise.all([local, github])
}

/** Runs `tower state --json`, stores it for the pane, updates the status line, toasts what's new. */
async function scanOnce($: EngineInterface): Promise<void> {
  const at = await $.clock.now()
  const failed = async (error: string) => {
    await update($, scanAtom, () => ({ ok: false, state: null, error, at }))
    $.ui.status(undefined)
  }
  // The pane ships in the tower plugin, so the core is beside it: $.plugin.root is the plugin's folder, the same
  // copy whose skills and hooks this session loaded (an installed one, or a --plugin-dir checkout).
  // The session's own folder is the process's working folder, so `tower state` reports this repo.
  const ran = await $.process.run(['python3', '-I', `${$.plugin.root}/scripts/run-tower.py`, 'state', '--json'], { timeoutMs: 30_000 })
  if (ran.exitCode !== 0) return failed(lastLine(ran.stderr, ran.exitCode))
  let state: TowerState
  try {
    state = JSON.parse(ran.stdout) as TowerState
  } catch {
    return failed('tower state printed something that is not JSON')
  }
  if (!Array.isArray(state.worktrees)) return failed('tower state printed JSON without worktrees (its format may have changed)')
  if (!Array.isArray(state.housekeeping)) return failed('tower state printed no housekeeping (its format may have changed)')

  const items = toastLabels(state)
  await update($, scanAtom, () => ({ ok: true, state, error: '', at }))
  await readBacklog($, state.ledger)

  const count = needsYou(state).length
  // `tower · work · 2/4`: the repo's approval profile (an older core reports none), then the WIP count.
  const profile = state.policy ? ` · ${state.policy.profile}` : ''
  $.ui.status(`tower${profile} · ${state.slots}/${state.wip_limit}${count ? ` · ${count} need${count === 1 ? 's' : ''} you` : ''}`)
  if (lastItems !== null) {
    for (const item of items.filter(item => !lastItems!.includes(item))) {
      $.ui.toast(`tower: ${item} — /tower`)
    }
  }
  lastItems = items
}

/** Reads the ledger's parked ideas (_backlog.md): none without a ledger or the file; any other failure is kept. */
async function readBacklog($: EngineInterface, ledger: string | null): Promise<void> {
  let backlog = { lines: [] as string[], error: '' }
  if (ledger !== null) {
    try {
      backlog.lines = (await $.fs.read(`${ledger}/_backlog.md`)).split('\n').filter(line => line.trim() !== '')
    } catch (error) {
      // No backlog file yet is normal (nothing parked); anything else is shown, not swallowed.
      if (!String(error).includes('ENOENT')) backlog = { lines: [], error: String(error).slice(0, 160) }
    }
  }
  const before = await read($, backlogAtom)
  if (JSON.stringify(before) !== JSON.stringify(backlog)) await update($, backlogAtom, () => backlog)
}

/** Runs `tower scan` in full for its GitHub section: the same PR and issue lines the hub reads. */
async function loadGithub($: EngineInterface): Promise<void> {
  const at = await $.clock.now()
  const ran = await $.process.run(['python3', '-I', `${$.plugin.root}/scripts/run-tower.py`, 'scan'], { timeoutMs: 90_000 })
  if (ran.exitCode !== 0) {
    $.ui.toast(`Couldn't load GitHub: ${lastLine(ran.stderr, ran.exitCode).slice(0, 120)}`)
    return
  }
  const section: GithubSection = { lines: sectionLines(ran.stdout, 'GITHUB'), at }
  await update($, githubAtom, () => section)
}

// ---------- hooks ----------

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'tower', description: "Open tower's live hub pane for this repo" })
    // Only inside a git repo; started in the background so session start isn't delayed.
    void (async () => {
      // Not a git repo, or the session's folder is gone: stay quiet (no status line, no timer).
      const inRepo = await $.process.run(['git', 'rev-parse', '--is-inside-work-tree'], { timeoutMs: 10_000 }).catch(() => null)
      if (inRepo === null || inRepo.exitCode !== 0) return
      sessionCwd = await $.session.cwd()
      inOrca = Boolean(await $.env.get('ORCA_TERMINAL_HANDLE').catch(() => undefined))
      await refresh($, false)
      $.clock.every(LOCAL_EVERY_MS, () => void refresh($, false))
    })()
    return next(e)
  })

  on('command.run', { command: 'tower' }, async $ => {
    await refresh($, false)
    await $.ui.open({ id: PANE, title: 'tower' })
    return { text: 'tower pane opened. It refreshes every 30 s; GitHub only when you press Refresh GitHub.' }
  })

  // In a worker's session (not the main checkout, where the hub runs), inside Orca: one key or click back to the hub.
  // A bare 0 in an empty prompt presses a band button; the band steps aside while a question's survey holds it.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const state = (await read($, scanAtom))?.state
    const ownWorktree = state && sessionCwd ? worktreeAt(state, sessionCwd) : undefined
    if (e.props.hasSurvey || !inOrca || !state || !ownWorktree || ownWorktree.is_main) return next(e)
    const { Box, Button } = $.ui.resolve(e)
    return (
      <Box>
        <Button key="hub" plain hotkey="0" label="back to the tower hub" onPress={() => void openInOrca($, 'the hub', state.main_checkout)} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Input, Text } = $.ui.resolve(e)
    const scan: Scan | null = await read($, scanAtom)
    const storedGithub = await read($, githubAtom)
    const parkCount = await read($, parkAtom)
    const backlog = await read($, backlogAtom)
    // $.state outlives a reload: a scan stored by the pane before 0.7.0 has no `state` until the next scan replaces it.
    if (scan === null || (scan.ok && !scan.state)) return <Text>Scanning…</Text>
    if (!scan.ok || !scan.state) {
      return (
        <Box flexDirection="column">
          <Text color="error" weight="bold">tower scan failed: {scan.error}</Text>
          <Button key="retry" label="Retry" hotkey="r" onPress={() => void refresh($, false)} />
        </Box>
      )
    }

    const state = scan.state
    const now = scan.at
    const github = storedGithub ? parseGithub(storedGithub.lines) : null

    // Each row is a fixed-width badge, then a column that wraps inside what's left of the pane: a
    // narrow pane gets more lines, each one starting under the name, never under the badge.
    const badge = (status: Status | 'session-waiting', isStale = false) => {
      const { label, color } = badgeOf(status, isStale)
      return (
        <Box flexShrink={0}>
          <Text backgroundColor={color} color="inverseText" weight="bold">{` ${label}`.padEnd(BADGE_WIDTH)}</Text>
        </Box>
      )
    }
    const row = (key: string, status: Status | 'session-waiting', isStale: boolean, top: RenderChildren, detail: string) => (
      <Box key={key} flexDirection="row" columnGap={1}>
        {badge(status, isStale)}
        <Box flexDirection="column" flexGrow={1} flexShrink={1}>
          <Box flexDirection="row" flexWrap="wrap" columnGap={2}>{top}</Box>
          {detail !== '' && <Text wrap="wrap">{detail}</Text>}
        </Box>
      </Box>
    )
    const bullet = (key: string, mark: string, text: string) => (
      <Box key={key} flexDirection="row" columnGap={1}>
        <Box flexShrink={0}><Text>{mark}</Text></Box>
        <Text wrap="wrap">{text}</Text>
      </Box>
    )
    const title = (text: string) => <Text weight="bold" underline>{text}</Text>
    const gap = () => <Text> </Text>
    const orcaName = (w: StateWorktree, hotkey?: string) => (
      <Button key={`orca-${w.label}`} plain hotkey={hotkey} label={w.label} onPress={() => void openInOrca($, w.label, w.path)} />
    )
    const cardLink = (w: StateWorktree) => state.ledger === null ? null : (
      <Button key={`card-${w.label}`} plain dimColor label="card" onPress={() => void openCard($, `${state.ledger}/${w.card_file}`)} />
    )
    const cardAge = (updated: string) => {
      const age = ageOf(updated, now)
      return age === '' ? 'card' : age === 'now' ? 'card just updated' : `card ${age} ago`
    }
    // The card's age; when the effective state isn't what the card says, why (e.g. "...busy: you already answered").
    const cardDetail = (w: StateWorktree) => {
      const age = cardAge(w.card?.updated ?? '')
      return w.card !== null && w.card.status !== w.status && w.reason !== '' ? `${age}, ${w.reason}` : age
    }
    const ticket = (w: StateWorktree) => (w.card && w.card.ticket !== 'none' ? <Text>{w.card.ticket}</Text> : null)
    const hasNoCard = (w: StateWorktree) => !w.is_main && w.card === null
    const sessionsOf = (w: StateWorktree) => {
      if (w.sessions.length === 0) return 'no session'
      if (w.sessions.length === 1) return `${w.sessions[0]?.activity ?? ''} session ${w.sessions[0]?.name ?? ''}`
      return `${w.sessions.length} sessions`
    }

    const needs = needsYou(state)
    const others = state.worktrees.filter(w => w.status !== 'needs-you')
    const housekeeping = state.housekeeping.map(item => `${item.label}: ${item.note}`) // the hub's own list
    const stacks = state.stacks ?? [] // an older core reports none
    const issues = github?.issues ?? []
    // An issue in progress gets no button: its number, then where it runs after its title ("… · running in feat-93-x").
    const inProgress = state.in_progress ?? {} // an older core reports none
    const shownIssues = issuesToShow(issues, inProgress)
    const moreIssues = issues.length - shownIssues.length
    const runningIssues = issues.filter(i => isInProgress(i, inProgress)).length
    let hotkey = 0 // 1-9 on the Needs you rows
    const nextHotkey = () => (hotkey < 9 ? String(++hotkey) : undefined)

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        width="100%"
        minHeight={e.props.scroll.bodyRows} // fill the whole pane, not just the rows with text
        backgroundColor={PANE_BACKGROUND || undefined}
      >
        <Box flexDirection="row" flexWrap="wrap" columnGap={3}>
          <Text weight="bold">{state.repo}</Text>
          {state.policy && <Text>{`policy ${state.policy.profile}`}</Text>}
          <Text>{`WIP ${state.slots}/${state.wip_limit}`}</Text>
          {state.at_limit && <Text backgroundColor="error" color="inverseText" weight="bold"> AT LIMIT </Text>}
          <Text>{`updated ${clockTime(now)}`}</Text>
        </Box>
        {state.notes.map(note => <Text key={`note-${note}`} italic dimColor wrap="wrap">{`note: ${note}`}</Text>)}
        <Box flexDirection="row" flexWrap="wrap" columnGap={1}>
          <Button key="refresh" label="Refresh" hotkey="r" onPress={() => void refresh($, false)} />
          <Button key="github" label="Refresh GitHub" hotkey="g" onPress={() => void refresh($, true)} />
          <Button key="next" label="What's next?" hotkey="n" onPress={() => void askClaude($, "what's next?")} />
        </Box>
        {gap()}

        {title('Needs you')}
        {needs.length === 0 && <Text>Nothing needs you right now.</Text>}
        {needs.map(w => {
          const waiting = waitingIn(w)
          const top = [orcaName(w, nextHotkey()), ticket(w), w.card ? cardLink(w) : null]
          const first = waiting[0]
          if (first) {
            // Answer it in its own session: the pop-up there has every option (free text, "chat about this").
            const more = waiting.length > 1 ? ` (+${waiting.length - 1} more waiting)` : ''
            return row(`need-${w.label}`, 'session-waiting', false, top, `${first.name}: ${waitingNote(first)}${more}`)
          }
          // Needs you with no session waiting here: the card says so (or sessions came from the event log).
          const detail = w.card ? cardDetail(w) : w.reason
          return row(`need-${w.label}`, 'needs-you', false, top, detail)
        })}
        {needs.length > 0 && <Text italic wrap="wrap">Click a name or press 1-9 to jump to its waiting terminal in Orca and answer there; press 0 there to come back.</Text>}
        {gap()}

        {title('Worktrees')}
        {others.map(w => {
          const names = w.sessions.map(s => s.name)
          // Event-log sessions are named by their id's start, which a message can't reach: only live names are nudged.
          const canNudge = (w.stale || hasNoCard(w)) && names.length > 0 && state.sessions_source === 'live'
          const top = [
            orcaName(w),
            ticket(w),
            w.card ? cardLink(w) : null,
            canNudge ? <Button key={`nudge-${w.label}`} plain dimColor label="nudge" onPress={() => void nudge($, names)} /> : null,
          ]
          const detail = [w.card ? cardDetail(w) : '', sessionsOf(w)].filter(Boolean).join(' · ')
          return row(`wt-${w.label}`, w.status, w.stale, top, detail)
        })}

        {stacks.length > 0 && gap()}
        {stacks.length > 0 && title('Stacks (merge order, bottom first)')}
        {stacks.map((stack, i) => {
          const { header, rows } = stackLines(state, stack)
          return (
            <Box key={`stack-${i}-${header}`} flexDirection="column">
              <Text wrap="wrap">{header}</Text>
              {rows.map(row => bullet(`stack-${row.text}`, row.mark, row.text))}
            </Box>
          )
        })}

        {housekeeping.length > 0 && gap()}
        {housekeeping.length > 0 && title('Housekeeping')}
        {housekeeping.map(line => bullet(`hk-${line}`, '•', line))}
        {gap()}

        {title('GitHub')}
        {github === null && <Text wrap="wrap">Press Refresh GitHub (g) to load PRs and issues.</Text>}
        {github !== null && github.note && <Text color="error" wrap="wrap">{github.note}</Text>}
        {github !== null && <Text weight="bold">{`PRs (${github.prs.length})`}</Text>}
        {github !== null && github.prs.map(pr => {
          const [number = '', ...words] = prLine(pr).split(' ')
          return bullet(`pr-${number}`, ` ${number}`, words.join(' '))
        })}
        {github !== null && <Text weight="bold" wrap="wrap">{`Open issues (${issues.length - runningIssues}${issues.length >= SCAN_ISSUE_LIMIT ? '+' : ''}${runningIssues ? `, plus ${runningIssues} in progress` : ''}): start a ready-for-agent one, discuss the rest`}</Text>}
        {shownIssues.map(issue => (
          <Box key={`issue-${issue.number}`} flexDirection="row" columnGap={1}>
            <Box flexShrink={0}>
              <Text> </Text>
              {isInProgress(issue, inProgress) ? <Text>{`#${issue.number}`}</Text> : issue.isReady
                ? <Button key={`start-${issue.number}`} plain label={`start #${issue.number}`} onPress={() => void askClaude($, `start #${issue.number}`)} />
                : <Button key={`discuss-${issue.number}`} plain dimColor label={`discuss #${issue.number}`} onPress={() => void askClaude($, `discuss #${issue.number}`)} />}
            </Box>
            <Text wrap="wrap">{[issue.title, inProgress[`#${issue.number}`]].filter(Boolean).join(' · ')}</Text>
          </Box>
        ))}
        {moreIssues > 0 && <Text>{`  + ${moreIssues}${issues.length >= SCAN_ISSUE_LIMIT ? ' or' : ''} more`}</Text>}
        {storedGithub && <Text italic>{`as of ${clockTime(storedGithub.at)}`}</Text>}

        {gap()}
        <Input
          key={`park-${parkCount}`}
          label="Park an idea"
          placeholder="type it and press Enter"
          submitLabel="park"
          onSubmit={idea => {
            if (idea.trim() === '') return
            void askClaude($, `park: ${idea.trim()}`)
            void update($, parkAtom, n => n + 1) // a new key draws a fresh, empty field
          }}
        />
        {(backlog.lines.length > 0 || backlog.error !== '') && gap()}
        {backlog.lines.length > 0 && title(`Parked ideas (${backlog.lines.length})`)}
        {backlog.error !== '' && <Text color="error" wrap="wrap">{`Couldn't read the parked ideas: ${backlog.error}`}</Text>}
        {backlog.lines.map(item => bullet(`idea-${item}`, ' ', item.replace(/^- /, '')))}
      </Box>
    )
  })
}
