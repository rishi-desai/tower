import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren } from 'claude-code'

import type { GithubSection, Scan, StateSession, StateWorktree, TowerState } from '../types'

// tower-pane: tower's hub report as a live pane beside the chat, a status-line summary, a toast when something new
// needs the user, and in each worker a "0: back to the hub" band. Read-only by design: it only ever runs tower's own
// `tower state --json` (through the tower plugin's run-tower.py) and hub-scan.sh (for GitHub only), `claude plugin
// list --json`, `orca terminal list/switch` and `orca worktree ps` (which only change the tab Orca shows), and reads
// the ledger's backlog file. These commands don't go through the Bash tool, so tower's approval hook never sees them;
// nothing here may write anything.

const PANE = 'tower'
const LOCAL_EVERY_MS = 30_000 // cards, worktrees, live sessions: cheap, so often
// The pane's own background. Claude Code fills panes with a palette grey that Gruvbox Dark turns tan;
// this paints over it with Ghostty's background (~/.config/ghostty/config). '' keeps Claude Code's fill.
const PANE_BACKGROUND = '#0e0e0e'
const ISSUES_SHOWN = 10 // the GitHub list stays short: the newest ten, then "+ N more"
const HUB_SCAN_ISSUE_LIMIT = 30 // hub-scan.sh's `gh issue list --limit`: a full list may mean there are more
const scanAtom = atom({ plugin: 'tower-pane', key: 'scan' } as const, null)
const parkAtom = atom({ plugin: 'tower-pane', key: 'park' } as const, 0) // bumped to clear the park field
const githubAtom = atom({ plugin: 'tower-pane', key: 'github' } as const, null)
const backlogAtom = atom({ plugin: 'tower-pane', key: 'backlog' } as const, [] as string[])

// ---------- reading tower state ----------

/** The worktrees whose effective state is needs-you: the rows under "Needs you", counted by the status line. */
export function needsYou(state: TowerState): StateWorktree[] {
  return state.worktrees.filter(w => w.status === 'needs-you')
}

/** A worktree's sessions that are waiting on the user (a question or an approval). */
export function waitingIn(w: StateWorktree): StateSession[] {
  return w.sessions.filter(s => s.activity === 'waiting')
}

/** What a waiting session needs, as Claude Code says it ("approve Bash"); a question says "input needed". */
export function waitingNote(session: StateSession): string {
  return session.waiting_for || 'input needed'
}

/** One label per worktree that needs the user, for toasts: a new label is something new. */
export function needItems(state: TowerState): string[] {
  return needsYou(state).map(w => {
    const waiting = waitingIn(w)[0]
    if (waiting) return `${w.label}: session ${waiting.name} is waiting (${waitingNote(waiting)})`
    return w.card?.status === 'needs-you' ? `${w.label}: card needs you` : `${w.label}: needs you`
  })
}

/** The worktree holding a folder: the longest path that contains it (worktrees can sit inside the main checkout). */
export function worktreeAt(state: TowerState, cwd: string): StateWorktree | undefined {
  return [...state.worktrees]
    .sort((a, b) => b.path.length - a.path.length)
    .find(w => w.path !== '' && (cwd === w.path || cwd.startsWith(`${w.path}/`)))
}

// ---------- reading hub-scan.sh's GitHub section ----------

export type Issue = { number: string; title: string; isReady: boolean }
export type Github = { prs: string[]; issues: Issue[]; note: string }

/** Lines of one "== NAME ==" section of hub-scan.sh's report, without its header. */
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
function badgeOf(status: string, isStale: boolean): { label: string; color: string } {
  if (isStale) return { label: 'STALE', color: 'error' }
  switch (status) {
    case 'needs-you': return { label: 'NEEDS YOU', color: 'warning' }
    case 'waiting': return { label: 'WAITING', color: 'warning' } // a session waiting on you: a question or an approval
    case 'blocked': return { label: 'BLOCKED', color: 'error' }
    case 'running': return { label: 'RUNNING', color: 'suggestion' }
    case 'waiting-ci': return { label: 'WAITING CI', color: 'inactive' }
    case 'done': return { label: 'DONE', color: 'success' }
    case 'ready-to-clean': return { label: 'CLEAN UP', color: 'success' }
    case 'main': return { label: 'MAIN', color: 'inactive' }
    default: return { label: 'NO CARD', color: 'inactive' }
  }
}

/**
 * The card's file name for a worktree row. Must match tower's ledger-lib.sh (ledger_card_name):
 * "/" becomes "__"; a detached HEAD (labelled "(detached abc1234)") is "detached-abc1234".
 */
export function cardFile(label: string): string {
  const detached = label.match(/^\(detached (\w+)\)$/)
  return `${detached ? `detached-${detached[1]}` : label.replace(/\//g, '__')}.md`
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

/** Why an orca command failed: it prints a JSON error on stdout, other failures go to stderr. */
function orcaError(ran: { stdout: string; stderr: string; exitCode: number }): string {
  try {
    const message = (JSON.parse(ran.stdout) as { error?: { message?: string } }).error?.message
    if (message) return message
  } catch {
    // not JSON: fall through to stderr
  }
  return ran.stderr.trim().split('\n').pop() || `exit ${ran.exitCode}`
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
    const listed = await $.process.run(['orca', 'terminal', 'list', '--worktree', `path:${worktreePath}`, '--json'], { timeoutMs: 15_000 })
    if (listed.exitCode !== 0) {
      $.ui.toast(`Couldn't ask Orca: ${orcaError(listed).slice(0, 120)}`)
      return
    }
    const terminals = (JSON.parse(listed.stdout) as { result?: { terminals?: OrcaTerminal[] } }).result?.terminals ?? []
    const panes = await waitingPanes($, worktreePath).catch(() => null)
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
let towerRoot: string | null = null // the installed tower plugin's folder, from `claude plugin list --json`
let lastItems: string[] | null = null // null until the first scan: no toasts for what was already there
let sessionCwd = '' // this session's folder: which worktree it works in
let inOrca = false // this session runs in an Orca terminal (Orca sets ORCA_TERMINAL_HANDLE), so it can jump

/** Finds the installed tower plugin's folder. Null when tower isn't installed or enabled. */
async function locateTower($: EngineInterface): Promise<string | null> {
  const listed = await $.process.run(['claude', 'plugin', 'list', '--json'], { timeoutMs: 30_000 })
  if (listed.exitCode !== 0) return null
  const plugins = JSON.parse(listed.stdout) as Array<{ id: string; enabled: boolean; installPath: string }>
  return plugins.find(p => p.id === 'tower@tower' && p.enabled)?.installPath ?? null
}

/**
 * Refreshes, and never throws: $.process.run rejects when a command can't start or outlives its
 * timeout, and an escaped rejection would leave the pane frozen on an old scan with no reason shown.
 */
async function refresh($: EngineInterface, withGithub: boolean): Promise<void> {
  try {
    await scanOnce($)
  } catch (error) {
    const at = await $.clock.now()
    await update($, scanAtom, () => ({ ok: false, state: null, error: `scan didn't run: ${String(error).slice(0, 200)}`, at, items: [] }))
    $.ui.status(undefined)
  }
  if (withGithub) await loadGithub($).catch(error => $.ui.toast(`Couldn't load GitHub: ${String(error).slice(0, 120)}`))
}

/** Why `tower state --json` failed, from its stderr; an old tower without the core says so. */
function stateError(stderr: string, exitCode: number): string {
  const last = stderr.trim().split('\n').pop() || `exit ${exitCode}`
  return stderr.includes('run-tower.py') ? `${last} (the pane needs tower 0.9.0 or later: claude plugin update tower@tower)` : last
}

/** Runs `tower state --json`, stores it for the pane, updates the status line, toasts what's new. */
async function scanOnce($: EngineInterface): Promise<void> {
  const at = await $.clock.now()
  const failed = async (error: string) => {
    await update($, scanAtom, () => ({ ok: false, state: null, error, at, items: [] }))
    $.ui.status(undefined)
  }
  if (towerRoot === null) {
    towerRoot = await locateTower($)
    if (towerRoot === null) return failed('tower plugin not installed or disabled (claude plugin list)')
  }
  // The session's own folder is the process's working folder, so `tower state` reports this repo.
  const ran = await $.process.run(['python3', '-I', `${towerRoot}/scripts/run-tower.py`, 'state', '--json'], { timeoutMs: 30_000 })
  if (ran.exitCode !== 0) return failed(stateError(ran.stderr, ran.exitCode))
  let state: TowerState
  try {
    state = JSON.parse(ran.stdout) as TowerState
  } catch {
    return failed('tower state printed something that is not JSON')
  }
  if (!Array.isArray(state.worktrees)) return failed('tower state printed JSON without worktrees (its format may have changed)')

  const items = needItems(state)
  await update($, scanAtom, () => ({ ok: true, state, error: '', at, items }))
  await readBacklog($, state.ledger)

  const count = needsYou(state).length
  $.ui.status(`tower WIP ${state.slots}/${state.wip_limit}${count ? ` · ${count} need${count === 1 ? 's' : ''} you` : ''}`)
  if (lastItems !== null) {
    for (const item of items.filter(item => !lastItems!.includes(item))) {
      $.ui.toast(`tower: ${item} — /tower`)
    }
  }
  lastItems = items
}

/** Reads the ledger's parked ideas (_backlog.md); none when there's no ledger or no file yet. */
async function readBacklog($: EngineInterface, ledger: string | null): Promise<void> {
  const text = ledger === null ? '' : await $.fs.read(`${ledger}/_backlog.md`).catch(() => '')
  const lines = text.split('\n').filter(line => line.trim() !== '')
  const before = await read($, backlogAtom)
  if (JSON.stringify(before) !== JSON.stringify(lines)) await update($, backlogAtom, () => lines)
}

/** Runs hub-scan.sh in full for its GitHub section: the same PR and issue lines the hub reads. */
async function loadGithub($: EngineInterface): Promise<void> {
  if (towerRoot === null) towerRoot = await locateTower($)
  if (towerRoot === null) {
    $.ui.toast("Couldn't load GitHub: tower plugin not installed or disabled")
    return
  }
  const at = await $.clock.now()
  const ran = await $.process.run(['bash', `${towerRoot}/skills/hub/scripts/hub-scan.sh`], { timeoutMs: 90_000 })
  if (ran.exitCode !== 0) {
    $.ui.toast(`Couldn't load GitHub: ${(ran.stderr.trim().split('\n').pop() ?? `exit ${ran.exitCode}`).slice(0, 120)}`)
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
    const own = state && sessionCwd ? worktreeAt(state, sessionCwd) : undefined
    if (e.props.hasSurvey || !inOrca || !state || !own || own.is_main) return next(e)
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
    const stored = await read($, githubAtom)
    const parkCount = await read($, parkAtom)
    const backlog = await read($, backlogAtom)
    if (scan === null) return <Text>Scanning…</Text>
    if (!scan.ok || scan.state === null) {
      return (
        <Box flexDirection="column">
          <Text color="error" weight="bold">tower scan failed: {scan.error}</Text>
          <Button key="retry" label="Retry" hotkey="r" onPress={() => void refresh($, false)} />
        </Box>
      )
    }

    const state = scan.state
    const now = scan.at
    const github = stored ? parseGithub(stored.lines) : null

    // Each row is a fixed-width badge, then a column that wraps inside what's left of the pane: a
    // narrow pane gets more lines, each one starting under the name, never under the badge.
    const badge = (status: string, isStale = false) => {
      const { label, color } = badgeOf(status, isStale)
      return (
        <Box flexShrink={0}>
          <Text backgroundColor={color} color="inverseText" weight="bold">{` ${label}`.padEnd(BADGE_WIDTH)}</Text>
        </Box>
      )
    }
    const row = (key: string, status: string, isStale: boolean, top: RenderChildren, detail: string) => (
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
      <Button key={`card-${w.label}`} plain dimColor label="card" onPress={() => void openCard($, `${state.ledger}/${cardFile(w.label)}`)} />
    )
    const cardAge = (updated: string) => {
      const age = ageOf(updated, now)
      return age === '' ? 'card' : age === 'now' ? 'card just updated' : `card ${age} ago`
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
    const housekeeping = [
      ...state.worktrees.flatMap(w => {
        const n = w.sessions.filter(s => s.kind === 'interactive').length
        return n > 1 ? [`${w.label}: ${n} sessions share one worktree${w.is_main ? ' (one is the hub)' : ' (rule: one each)'}`] : []
      }),
      ...state.worktrees.filter(w => w.stale).map(w => `${w.label}: card not updated in over a day`),
      ...state.worktrees.filter(hasNoCard).map(w => `${w.label}: no card yet`),
      ...state.worktrees.filter(w => w.status === 'ready-to-clean').map(w => `${w.label}: ready to clean up (${w.reason})`),
      ...state.orphan_cards.map(o => `${o.name}: card left over from a removed worktree`),
    ]
    const issues = github?.issues ?? []
    const moreIssues = issues.length - Math.min(issues.length, ISSUES_SHOWN)
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
            return row(`need-${w.label}`, 'waiting', false, top, `${first.name}: ${waitingNote(first)}${more}`)
          }
          // Needs you with no session waiting here: the card says so (or sessions came from the event log).
          const detail = w.card ? (w.card.status === 'needs-you' ? cardAge(w.card.updated) : `${cardAge(w.card.updated)}, ${w.reason}`) : w.reason
          return row(`need-${w.label}`, 'needs-you', false, top, detail)
        })}
        {needs.length > 0 && <Text italic wrap="wrap">Click a name or press 1-9 to jump to its waiting terminal in Orca and answer there; press 0 there to come back.</Text>}
        {gap()}

        {title('Worktrees')}
        {others.map(w => {
          const names = w.sessions.map(s => s.name)
          const canNudge = (w.stale || hasNoCard(w)) && names.length > 0
          const top = [
            orcaName(w),
            ticket(w),
            w.card ? cardLink(w) : null,
            canNudge ? <Button key={`nudge-${w.label}`} plain dimColor label="nudge" onPress={() => void nudge($, names)} /> : null,
          ]
          // When the effective state isn't what the card says, say why (e.g. "...busy: you already answered").
          const overruled = w.card !== null && w.card.status !== w.status && w.reason !== ''
          const age = w.card ? (overruled ? `${cardAge(w.card.updated)}, ${w.reason}` : cardAge(w.card.updated)) : ''
          const detail = [age, sessionsOf(w)].filter(Boolean).join(' · ')
          return row(`wt-${w.label}`, w.status, w.stale, top, detail)
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
        {github !== null && <Text weight="bold" wrap="wrap">{`Open issues (${issues.length}${issues.length >= HUB_SCAN_ISSUE_LIMIT ? '+' : ''}): start a ready-for-agent one, discuss the rest`}</Text>}
        {issues.slice(0, ISSUES_SHOWN).map(issue => (
          <Box key={`issue-${issue.number}`} flexDirection="row" columnGap={1}>
            <Box flexShrink={0}>
              <Text> </Text>
              {issue.isReady
                ? <Button key={`start-${issue.number}`} plain label={`start #${issue.number}`} onPress={() => void askClaude($, `start #${issue.number}`)} />
                : <Button key={`discuss-${issue.number}`} plain dimColor label={`discuss #${issue.number}`} onPress={() => void askClaude($, `discuss #${issue.number}`)} />}
            </Box>
            <Text wrap="wrap">{issue.title}</Text>
          </Box>
        ))}
        {moreIssues > 0 && <Text>{`  + ${moreIssues}${issues.length >= HUB_SCAN_ISSUE_LIMIT ? ' or' : ''} more`}</Text>}
        {stored && <Text italic>{`as of ${clockTime(stored.at)}`}</Text>}

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
        {backlog.length > 0 && gap()}
        {backlog.length > 0 && title(`Parked ideas (${backlog.length})`)}
        {backlog.map(item => bullet(`idea-${item}`, ' ', item.replace(/^- /, '')))}
      </Box>
    )
  })
}
