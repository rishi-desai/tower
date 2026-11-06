import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren, ToolCallResult } from 'claude-code'

import type { GithubSection, RelayedQuestion, Scan } from '../types'

// tower-pane: tower's hub report as a live pane beside the chat, a status-line summary, and a toast
// when something new needs the user. Read-only by design: it only ever runs tower's own
// hub-scan.sh, `claude plugin list --json`, and `orca terminal list/switch` (which only changes the
// tab Orca shows). These commands don't go through the Bash tool, so tower's approval hook never sees
// them; nothing here may write anything, except the relayed-question files (see "pop-up questions").

const PANE = 'tower'
const LOCAL_EVERY_MS = 30_000 // cards, worktrees, live sessions: cheap, so often
// The pane's own background. Claude Code fills panes with a palette grey that Gruvbox Dark turns tan;
// this paints over it with Ghostty's background (~/.config/ghostty/config). '' keeps Claude Code's fill.
const PANE_BACKGROUND = '#0e0e0e'
const scanAtom = atom({ plugin: 'tower-pane', key: 'scan' } as const, null)
const parkAtom = atom({ plugin: 'tower-pane', key: 'park' } as const, 0) // bumped to clear the park field
const githubAtom = atom({ plugin: 'tower-pane', key: 'github' } as const, null)
const questionsAtom = atom({ plugin: 'tower-pane', key: 'questions' } as const, [] as RelayedQuestion[])
const picksAtom = atom({ plugin: 'tower-pane', key: 'picks' } as const, {} as Record<string, Record<string, string>>)
const QUESTION_POLL_MS = 3_000 // a question waits on the user, so the pane looks often (one directory listing)

// ---------- reading hub-scan.sh's report ----------

/** Lines of one "== NAME ==" section of the report, without its header. */
function sectionLines(text: string, name: string): string[] {
  const lines = text.split('\n')
  const start = lines.findIndex(line => line.startsWith(`== ${name}`))
  if (start < 0) return []
  const rest = lines.slice(start + 1)
  const end = rest.findIndex(line => line.startsWith('== '))
  return (end < 0 ? rest : rest.slice(0, end)).filter(line => line.trim() !== '')
}

/** The branch at the start of a report row: "- feat/42-x | ..." -> "feat/42-x". */
function rowBranch(line: string): string {
  return line.replace(/^- /, '').split(' | ')[0] ?? ''
}

/** Everything that needs the user, one label each for toasts: the same rows as the pane's Needs you. */
export function needsYou(text: string): string[] {
  const { needCards, waiting } = needRows(parseReport(text))
  const hasWaiting = (branch: string) => waiting.some(w => w.branch === branch)
  return [
    ...needCards.filter(w => w.card === 'needs-you').map(w => `${w.branch}: card needs you`),
    // Needs you for a reason LIVE SESSIONS can't show (e.g. sessions read from the event log): say so once.
    ...needCards.filter(w => w.card !== 'needs-you' && !hasWaiting(w.branch)).map(w => `${w.branch}: needs you`),
    ...waiting.map(w => `${w.branch}: session ${w.session.name} is waiting`),
  ]
}

/** "2 of 3" from "WIP: 2 of 3 (running + needs-you)". */
export function wipOf(text: string): string {
  const match = text.match(/^WIP: (\d+ of \d+)/m)
  return match?.[1] ?? ''
}

/**
 * One WORKTREES row. status is the worktree's effective state, decided once by tower's core (`tower state`) for the
 * hub, the pane and the WIP count alike; card is what the card itself says ('none' without one, '' on the main
 * checkout's row); reason says why status is what it is.
 */
export type Worktree = { branch: string; path: string; isMain: boolean; status: string; card: string; reason: string; ticket: string; updated: string; isStale: boolean }
export type Session = { name: string; state: string; waitingFor: string }
export type Github = { prs: string[]; readyIssues: string[]; otherIssues: number; note: string }
export type Report = {
  repo: string
  ledger: string
  wip: string
  isAtLimit: boolean
  worktrees: Worktree[]
  sessions: Record<string, Session[]>
  shared: Record<string, number>
  orphans: string[]
  backlog: string[]
}

/** The report as rows: one worktree per row, sessions grouped by worktree. */
export function parseReport(text: string): Report {
  const legacy = new Set<string>() // rows from hub-scan.sh before tower 0.9.0, whose status is the card's own
  const rows = sectionLines(text, 'WORKTREES')
    .filter(line => line.startsWith('- '))
    .map((line, index): Worktree => {
      const parts = line.slice(2).split(' | ')
      const field = (name: string) => parts.find(p => p.startsWith(`${name}: `))?.slice(name.length + 2) ?? ''
      const isMain = index === 0
      let status = (field('status') || (parts.some(p => p.startsWith('main checkout')) ? 'main' : '')).toLowerCase()
      if (status === 'no card') status = 'no-card' // a report from hub-scan.sh before tower 0.9.0
      // Rows from before tower 0.9.0 have no card field: there, status was the card's own.
      if (!parts.some(p => p.startsWith('card: '))) legacy.add(parts[0] ?? '')
      const card = legacy.has(parts[0] ?? '') ? (isMain ? '' : status === 'no-card' ? 'none' : status) : field('card')
      const path = [...parts].reverse().find(p => p.startsWith('/')) ?? '' // the row's last field; never drawn, only handed to Orca
      return { branch: parts[0] ?? '', path, isMain, status, card, reason: field('reason'), ticket: field('ticket'), updated: field('updated'), isStale: parts.some(p => p.startsWith('STALE')) }
    })
  const sessions: Record<string, Session[]> = {}
  const shared: Record<string, number> = {}
  for (const line of sectionLines(text, 'LIVE SESSIONS')) {
    if (!line.startsWith('- ')) continue
    const branch = rowBranch(line)
    sessions[branch] = [...line.matchAll(/([\w.-]+)\(\w+:(\w+)\)(?: waiting for: ([^,<]+))?/g)].map(m => ({
      name: m[1] ?? '', state: m[2] ?? '', waitingFor: (m[3] ?? '').trim(),
    }))
    const many = line.match(/<-- (\d+) interactive/)
    if (many?.[1]) shared[branch] = Number(many[1])
  }
  // A legacy needs-you row whose session is working again, and none waiting: the user already answered. Newer
  // reports arrive with this decided (tower state); remove once no tower older than 0.9.0 is installed.
  const worktrees = rows.map(w => {
    const list = sessions[w.branch] ?? []
    const isResumed = legacy.has(w.branch) && w.status === 'needs-you' && list.some(s => s.state === 'busy')
      && !list.some(s => s.state === 'waiting' || s.state === 'blocked')
    return isResumed ? { ...w, status: 'running', reason: 'the card says needs-you but its session is busy: you already answered' } : w
  })
  return {
    repo: text.match(/^REPO: (.+)$/m)?.[1] ?? '',
    ledger: text.match(/^LEDGER: (.+)$/m)?.[1] ?? '',
    wip: wipOf(text),
    isAtLimit: /^WIP: .*AT LIMIT/m.test(text),
    worktrees,
    sessions,
    shared,
    orphans: sectionLines(text, 'ORPHAN CARDS').filter(l => l.startsWith('- ')).map(rowBranch),
    backlog: sectionLines(text, 'BACKLOG').filter(l => l !== '(empty)'),
  }
}

/** PRs, ready-for-agent issues and a count of the rest, from the GitHub section's lines. */
export function parseGithub(lines: string[]): Github {
  const github: Github = { prs: [], readyIssues: [], otherIssues: 0, note: '' }
  let group: 'prs' | 'issues' | '' = ''
  for (const line of lines) {
    if (line.startsWith('-- my open PRs')) group = 'prs'
    else if (line.startsWith('-- open issues')) group = 'issues'
    else if (line.startsWith('- #') && group === 'prs') github.prs.push(line.slice(2))
    else if (line.startsWith('- #') && group === 'issues') {
      if (line.includes('[ready-for-agent]')) github.readyIssues.push(line.slice(2).replace(/ \[[^\]]+\]/g, ''))
      else github.otherIssues += 1
    } else if (line.startsWith('(') && line !== '(none)') github.note = line
  }
  return github
}

/**
 * The rows under "Needs you": each worktree whose effective state is needs-you (with its waiting
 * session, if any), then each waiting session whose worktree isn't one of them. The pane, the status
 * line and the toasts all count these. The pane doesn't weigh cards against sessions itself: section 1's
 * status already has (a needs-you card whose session is busy again arrives as running).
 */
export function needRows(report: Report) {
  const needCards = report.worktrees.filter(w => w.status === 'needs-you')
  const waiting = Object.entries(report.sessions).flatMap(([branch, list]) =>
    list.filter(s => s.state === 'waiting' || s.state === 'blocked').map(s => ({ branch, session: s })))
  const loneWaiting = waiting.filter(w => !needCards.some(c => c.branch === w.branch))
  return { needCards, waiting, loneWaiting, count: needCards.length + loneWaiting.length }
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
    case 'question': return { label: 'QUESTION', color: 'warning' }
    case 'waiting': return { label: 'WAITING', color: 'warning' }
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
 * The card's file name for a worktree row. Must match tower's core (card_name in src/tower/ledger.py):
 * "/" becomes "__"; a detached HEAD (drawn "(detached abc1234)") is "detached-abc1234".
 */
export function cardFile(branch: string): string {
  const detached = branch.match(/^\(detached (\w+)\)$/)
  return `${detached ? `detached-${detached[1]}` : branch.replace(/\//g, '__')}.md`
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

type OrcaTerminal = { handle: string; title: string; connected: boolean; lastOutputAt: number; agentIdentity?: string }

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
 * Brings a worktree's terminal tab forward in Orca, then Orca itself. Of several terminals it picks
 * the Claude one that printed last (a session waiting on you printed its question last), else any.
 */
async function openInOrca($: EngineInterface, branch: string, worktreePath: string): Promise<void> {
  try {
    const listed = await $.process.run(['orca', 'terminal', 'list', '--worktree', `path:${worktreePath}`, '--json'], { timeoutMs: 15_000 })
    if (listed.exitCode !== 0) {
      $.ui.toast(`Couldn't ask Orca: ${orcaError(listed).slice(0, 120)}`)
      return
    }
    const live = ((JSON.parse(listed.stdout) as { result?: { terminals?: OrcaTerminal[] } }).result?.terminals ?? []).filter(t => t.connected)
    const claude = live.filter(t => t.agentIdentity === 'claude')
    const pick = [...(claude.length ? claude : live)].sort((a, b) => b.lastOutputAt - a.lastOutputAt)[0]
    if (!pick) {
      $.ui.toast(`No open Orca terminal in ${branch}`)
      return
    }
    const switched = await $.process.run(['orca', 'terminal', 'switch', '--terminal', pick.handle, '--json'], { timeoutMs: 15_000 })
    if (switched.exitCode !== 0) {
      $.ui.toast(`Couldn't switch Orca: ${orcaError(switched).slice(0, 120)}`)
      return
    }
    await $.process.run(['open', '-a', 'Orca'], { timeoutMs: 10_000 }) // the tab is switched; this only raises the window
    $.ui.toast(`Orca: ${branch} · ${pick.title}`)
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

// ---------- pop-up questions (prototype) ----------
// A worker session running this mod writes each AskUserQuestion pop-up to a file; a hub pane shows it
// with buttons and sends the click back with $.session.send. The first answer wins: the pop-up in the
// worker, or the pane. Approvals never go this way; they stay in their own session.

const ANSWER_TAG = 'tower-answer'
let questionsDir: string | null = null // ~/.claude/tower/questions, once HOME is known
let ownSessionId = ''
let pendingAnswer: { id: string; resolve: (answers: Record<string, string>) => void } | null = null

/** Finds ~/.claude/tower/questions from HOME. */
async function locateQuestionsDir($: EngineInterface): Promise<void> {
  const home = await $.env.get('HOME')
  if (home) questionsDir = `${home}/.claude/tower/questions`
}

/** Reads every relayed question but this session's own; redraws only when the set changed. */
async function pollQuestions($: EngineInterface): Promise<void> {
  if (questionsDir === null) return
  const entries = await $.fs.list(questionsDir).catch(() => []) // no directory yet: no questions
  const found: RelayedQuestion[] = []
  for (const entry of entries) {
    if (entry.kind !== 'file' || !entry.name.endsWith('.json')) continue
    try {
      const question = JSON.parse(await $.fs.read(`${questionsDir}/${entry.name}`)) as RelayedQuestion
      if (question.sessionId !== ownSessionId) found.push(question)
    } catch {
      // half-written or just removed: the next poll sees it whole or gone
    }
  }
  found.sort((a, b) => a.askedAt - b.askedAt)
  const before = await read($, questionsAtom)
  if (JSON.stringify(before) !== JSON.stringify(found)) await update($, questionsAtom, () => found)
}

/** Sends the picked answers to the worker that asked. It accepts them only with the question's id. */
async function sendAnswers($: EngineInterface, question: RelayedQuestion, answers: Record<string, string>): Promise<void> {
  const text = `${ANSWER_TAG} ${question.id} ${encodeURIComponent(JSON.stringify(answers))}`
  const result = await $.session.send({ to: { sessionId: question.sessionId }, text })
  $.ui.toast(result.isDelivered ? `Answered: ${Object.values(answers).join(', ')}` : `Couldn't answer: ${result.reason}`)
  if (result.isDelivered) await update($, questionsAtom, list => list.filter(q => q.id !== question.id))
}

/** Records one clicked option; sends once every question of the pop-up has one. */
async function pickOption($: EngineInterface, question: RelayedQuestion, questionText: string, label: string): Promise<void> {
  const picks = { ...((await read($, picksAtom))[question.id] ?? {}), [questionText]: label }
  await update($, picksAtom, all => ({ ...all, [question.id]: picks }))
  if (question.questions.every(q => picks[q.question] !== undefined)) await sendAnswers($, question, picks)
}

type AskedQuestions = RelayedQuestion['questions']
type AskResult = ToolCallResult<'AskUserQuestion'>

/**
 * Worker side: writes this session's pop-up to its question file, opens the pop-up as usual, and
 * returns whichever answer comes first, the pop-up's or the pane's. The file is removed either way.
 */
async function relayQuestion($: EngineInterface, questions: AskedQuestions, openPopup: () => Promise<AskResult>): Promise<AskResult> {
  if (questionsDir === null || ownSessionId === '') return openPopup()
  const file = `${questionsDir}/${ownSessionId}.json`
  const relayed: RelayedQuestion = {
    id: crypto.randomUUID(),
    sessionId: ownSessionId,
    cwd: await $.session.cwd(),
    askedAt: await $.clock.now(),
    questions: questions.map(q => ({ question: q.question, header: q.header, multiSelect: q.multiSelect, options: q.options.map(o => ({ label: o.label, description: o.description })) })),
  }
  try {
    await $.fs.write(file, JSON.stringify(relayed))
  } catch (error) {
    $.ui.toast(`tower: couldn't relay this question to the pane (${String(error).slice(0, 80)}); answer it here`)
    return openPopup()
  }
  const fromPane = new Promise<Record<string, string>>(resolve => {
    pendingAnswer = { id: relayed.id, resolve }
  })
  type First = { local: AskResult } | { answers: Record<string, string> }
  try {
    const first: First = await Promise.race([
      openPopup().then((local): First => ({ local })),
      fromPane.then((answers): First => ({ answers })),
    ])
    if ('local' in first) return first.local
    $.ui.toast(`Answered from the tower pane: ${Object.values(first.answers).join(', ')}`)
    return { result: { questions, answers: first.answers } }
  } finally {
    pendingAnswer = null
    await $.process.run(['rm', '-f', file], { timeoutMs: 5_000 }).catch(() => null)
  }
}

// ---------- scanning ----------

// Module state: starts over when the mod reloads.
let scanArgv: string[] | null = null // ['bash', '<tower>/skills/hub/scripts/hub-scan.sh']
let lastItems: string[] | null = null // null until the first scan: no toasts for what was already there

/** Finds the installed tower plugin's hub-scan.sh. Null when tower isn't installed or enabled. */
async function locateScan($: EngineInterface): Promise<string | null> {
  const listed = await $.process.run(['claude', 'plugin', 'list', '--json'], { timeoutMs: 30_000 })
  if (listed.exitCode !== 0) return null
  const plugins = JSON.parse(listed.stdout) as Array<{ id: string; enabled: boolean; installPath: string }>
  const tower = plugins.find(p => p.id === 'tower@tower' && p.enabled)
  return tower ? `${tower.installPath}/skills/hub/scripts/hub-scan.sh` : null
}

/**
 * Refreshes, and never throws: $.process.run rejects when a command can't start or outlives its
 * timeout, and an escaped rejection would leave the pane frozen on an old scan with no reason shown.
 */
async function refresh($: EngineInterface, withGithub: boolean): Promise<void> {
  try {
    await scanOnce($, withGithub)
  } catch (error) {
    const at = await $.clock.now()
    const reason = `scan didn't run: ${String(error).slice(0, 200)}`
    await update($, scanAtom, () => ({ ok: false, text: '', error: reason, at, wip: '', items: [] }))
    $.ui.status(undefined)
  }
}

/** Runs the scan, stores it for the pane, updates the status line, toasts what's new. */
async function scanOnce($: EngineInterface, withGithub: boolean): Promise<void> {
  const at = await $.clock.now()
  if (scanArgv === null) {
    const path = await locateScan($)
    if (path === null) {
      await update($, scanAtom, () => ({ ok: false, text: '', error: 'tower plugin not installed or disabled (claude plugin list)', at, wip: '', items: [] }))
      return
    }
    scanArgv = ['bash', path]
  }
  const argv = withGithub ? scanArgv : [...scanArgv, '--no-github']
  const ran = await $.process.run(argv, { timeoutMs: withGithub ? 90_000 : 30_000 })
  const error = ran.exitCode === 0 ? '' : (ran.stderr.trim().split('\n').pop() ?? `exit ${ran.exitCode}`)
  const scan: Scan = { ok: ran.exitCode === 0, text: ran.stdout, error, at, wip: wipOf(ran.stdout), items: needsYou(ran.stdout) }
  await update($, scanAtom, () => scan)

  if (withGithub && scan.ok) {
    const section: GithubSection = { lines: sectionLines(ran.stdout, 'GITHUB'), at }
    await update($, githubAtom, () => section)
  }

  if (!scan.ok) {
    $.ui.status(undefined)
    return
  }
  const count = needRows(parseReport(scan.text)).count
  $.ui.status(`tower WIP ${scan.wip.replace(' of ', '/')}${count ? ` · ${count} need${count === 1 ? 's' : ''} you` : ''}`)
  if (lastItems !== null) {
    for (const item of scan.items.filter(item => !lastItems!.includes(item))) {
      $.ui.toast(`tower: ${item} — /tower`)
    }
  }
  lastItems = scan.items
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
      ownSessionId = await $.session.id()
      await locateQuestionsDir($).catch(() => undefined) // no HOME: questions stay in the pop-up only
      await refresh($, false)
      $.clock.every(LOCAL_EVERY_MS, () => void refresh($, false))
      $.clock.every(QUESTION_POLL_MS, () => void pollQuestions($).catch(() => undefined))
    })()
    return next(e)
  })

  // Worker side: relay this session's pop-up to the pane, and take whichever answer comes first.
  on('tool.call', { tool: 'AskUserQuestion' }, ($, e, next) => relayQuestion($, e.questions, () => next(e)))

  // Worker side: a pane's answer arrives as a message; take it before the model would read it.
  on('session.receive', async ($, e, next) => {
    const match = e.text.match(new RegExp(`${ANSWER_TAG} ([0-9a-f-]{36}) (\\S+)`))
    if (!match || pendingAnswer === null || match[1] !== pendingAnswer.id) return next(e)
    try {
      pendingAnswer.resolve(JSON.parse(decodeURIComponent(match[2] ?? '')) as Record<string, string>)
    } catch {
      return next(e) // not ours after all: let the model read it
    }
    return { consumed: 'an answer from the tower pane' }
  })

  on('command.run', { command: 'tower' }, async $ => {
    await refresh($, false)
    await $.ui.open({ id: PANE, title: 'tower' })
    return { text: 'tower pane opened. It refreshes every 30 s; GitHub only when you press Refresh GitHub.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Input, Text } = $.ui.resolve(e)
    const scan = await read($, scanAtom)
    const stored = await read($, githubAtom)
    const parkCount = await read($, parkAtom)
    const relayed = await read($, questionsAtom)
    const picks = await read($, picksAtom)
    if (scan === null) return <Text>Scanning…</Text>
    if (!scan.ok) {
      return (
        <Box flexDirection="column">
          <Text color="error" weight="bold">tower scan failed: {scan.error}</Text>
          <Button key="retry" label="Retry" hotkey="r" onPress={() => void refresh($, false)} />
        </Box>
      )
    }

    const report = parseReport(scan.text)
    const now = scan.at
    const fresh = sectionLines(scan.text, 'GITHUB')
    const isFresh = fresh.length > 0 && fresh[0] !== '(skipped: --no-github)'
    const githubLines = isFresh ? fresh : stored?.lines ?? null
    const github = githubLines ? parseGithub(githubLines) : null
    const githubAt = isFresh ? scan.at : stored?.at

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
    const row = (key: string, status: string, isStale: boolean, top: RenderChildren, detail: string, extra: RenderChildren = null) => (
      <Box key={key} flexDirection="row" columnGap={1}>
        {badge(status, isStale)}
        <Box flexDirection="column" flexGrow={1} flexShrink={1}>
          <Box flexDirection="row" flexWrap="wrap" columnGap={2}>{top}</Box>
          {detail !== '' && <Text wrap="wrap">{detail}</Text>}
          {extra}
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
    const cardPath = (branch: string) => `${report.ledger}/${cardFile(branch)}`
    const pathOf = (branch: string) => report.worktrees.find(w => w.branch === branch)?.path ?? ''
    const orcaName = (key: string, branch: string, hotkey?: string) => (
      <Button key={key} plain hotkey={hotkey} label={branch} onPress={() => void openInOrca($, branch, pathOf(branch))} />
    )
    const cardLink = (branch: string) => (
      <Button key={`card-${branch}`} plain dimColor label="card" onPress={() => void openCard($, cardPath(branch))} />
    )
    const cardAge = (updated: string) => {
      const age = ageOf(updated, now)
      return age === '' ? 'card' : age === 'now' ? 'card just updated' : `card ${age} ago`
    }
    const ticketOf = (w: { ticket: string }) => (w.ticket && w.ticket !== 'none' ? w.ticket : '')
    const hasCard = (w: Worktree) => !w.isMain && w.card !== 'none' && w.card !== ''
    const hasNoCard = (w: Worktree) => !w.isMain && w.card === 'none'

    const { needCards, waiting, loneWaiting } = needRows(report)
    const others = report.worktrees.filter(w => !needCards.includes(w))
    const namesIn = (branch: string) => (report.sessions[branch] ?? []).map(s => s.name)
    const sessionsOf = (branch: string) => {
      const list = report.sessions[branch] ?? []
      if (list.length === 0) return 'no session'
      if (list.length === 1) return `${list[0]?.state ?? ''} session ${list[0]?.name ?? ''}`
      return `${list.length} sessions`
    }
    const housekeeping = [
      ...Object.entries(report.shared).map(([branch, n]) => `${branch}: ${n} sessions share one worktree (rule: one each)`),
      ...report.worktrees.filter(w => w.isStale).map(w => `${w.branch}: card not updated in over a day`),
      ...report.worktrees.filter(hasNoCard).map(w => `${w.branch}: no card yet`),
      ...report.worktrees.filter(w => w.status === 'ready-to-clean').map(w => `${w.branch}: ready to clean up (${w.reason})`),
      ...report.orphans.map(o => `${o}: card left over from a removed worktree`),
    ]
    const worktreeAt = (cwd: string) =>
      [...report.worktrees].sort((a, b) => b.path.length - a.path.length).find(w => w.path !== '' && (cwd === w.path || cwd.startsWith(`${w.path}/`)))
    const questions = relayed.flatMap(q => {
      const at = worktreeAt(q.cwd)
      return at ? [{ q, branch: at.branch }] : [] // another repo's question: its own hub shows it
    })
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
          <Text weight="bold">{report.repo}</Text>
          <Text>{`WIP ${report.wip.replace(' of ', '/')}`}</Text>
          {report.isAtLimit && <Text backgroundColor="error" color="inverseText" weight="bold"> AT LIMIT </Text>}
          <Text>{`updated ${clockTime(now)}`}</Text>
        </Box>
        <Box flexDirection="row" flexWrap="wrap" columnGap={1}>
          <Button key="refresh" label="Refresh" hotkey="r" onPress={() => void refresh($, false)} />
          <Button key="github" label="Refresh GitHub" hotkey="g" onPress={() => void refresh($, true)} />
          <Button key="next" label="What's next?" hotkey="n" onPress={() => void askClaude($, "what's next?")} />
        </Box>
        {gap()}

        {title('Needs you')}
        {needCards.length + loneWaiting.length + questions.length === 0 && <Text>Nothing needs you right now.</Text>}
        {questions.map(({ q, branch }) => row(
          `q-${q.id}`, 'question', false, [orcaName(`orca-q-${q.id}`, branch)], '',
          q.questions.map(item => (
            <Box key={`qq-${q.id}-${item.question}`} flexDirection="column">
              <Text wrap="wrap" weight="bold">{item.question}</Text>
              {item.multiSelect
                ? <Text wrap="wrap" italic>Several answers allowed: answer this one in the session (click the name).</Text>
                : item.options.map(option => (
                  <Box key={`opt-${q.id}-${item.question}-${option.label}`} flexDirection="row" columnGap={1}>
                    <Box flexShrink={0}>
                      <Text>{picks[q.id]?.[item.question] === option.label ? '●' : '○'}</Text>
                    </Box>
                    <Box flexShrink={0}>
                      <Button key={`answer-${q.id}-${item.question}-${option.label}`} plain label={option.label} onPress={() => void pickOption($, q, item.question, option.label)} />
                    </Box>
                    {option.description ? <Text wrap="wrap" dimColor>{option.description}</Text> : null}
                  </Box>
                ))}
            </Box>
          )),
        ))}
        {needCards.map(card => {
          const session = waiting.find(w => w.branch === card.branch)?.session
          const detail = `${hasCard(card) ? cardAge(card.updated) : card.isMain ? 'main checkout' : 'no card'}${session ? ` · ${session.name} waiting${session.waitingFor ? `: ${session.waitingFor}` : ''}` : ''}`
          const top = [orcaName(`orca-${card.branch}`, card.branch, nextHotkey()), ticketOf(card) ? <Text>{ticketOf(card)}</Text> : null, hasCard(card) ? cardLink(card.branch) : null]
          return row(`need-${card.branch}`, 'needs-you', false, top, detail)
        })}
        {loneWaiting.map(w => row(
          `wait-${w.session.name}`, 'waiting', false,
          [orcaName(`orca-${w.session.name}`, w.branch, nextHotkey())],
          `${w.session.name}${w.session.waitingFor ? `: ${w.session.waitingFor}` : ' is waiting'}`,
        ))}
        {needCards.length + loneWaiting.length + questions.length > 0 && <Text italic wrap="wrap">Click a name or press 1-9 to jump to it in Orca. Questions can be answered here; approvals only in their session.</Text>}
        {gap()}

        {title('Worktrees')}
        {others.map(w => {
          const canNudge = (w.isStale || hasNoCard(w)) && namesIn(w.branch).length > 0
          const top = [
            orcaName(`orca-${w.branch}`, w.branch),
            ticketOf(w) ? <Text>{ticketOf(w)}</Text> : null,
            hasCard(w) ? cardLink(w.branch) : null,
            canNudge ? <Button key={`nudge-${w.branch}`} plain dimColor label="nudge" onPress={() => void nudge($, namesIn(w.branch))} /> : null,
          ]
          // When the effective state isn't what the card says, say why (e.g. "...busy: you already answered").
          const overruled = hasCard(w) && w.card !== w.status && w.reason !== ''
          const age = hasCard(w) ? (overruled ? `${cardAge(w.updated)}, ${w.reason}` : cardAge(w.updated)) : ''
          const detail = [age, sessionsOf(w.branch)].filter(Boolean).join(' · ')
          return row(`wt-${w.branch}`, w.status, w.isStale, top, detail)
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
        {github !== null && <Text weight="bold" wrap="wrap">{`Ready for an agent (${github.readyIssues.length}): click a number to start it`}</Text>}
        {github !== null && github.readyIssues.map(issue => {
          const number = issue.match(/^#(\d+)/)?.[1] ?? ''
          return (
            <Box key={`issue-${number}`} flexDirection="row" columnGap={1}>
              <Box flexShrink={0}><Text> </Text><Button key={`start-${number}`} plain label={`#${number}`} onPress={() => void askClaude($, `start #${number}`)} /></Box>
              <Text wrap="wrap">{issue.replace(/^#\d+ /, '')}</Text>
            </Box>
          )
        })}
        {github !== null && github.otherIssues > 0 && <Text>{`  + ${github.otherIssues} other open issues`}</Text>}
        {github !== null && githubAt !== undefined && <Text italic>{`as of ${clockTime(githubAt)}`}</Text>}

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
        {report.backlog.length > 0 && gap()}
        {report.backlog.length > 0 && title(`Parked ideas (${report.backlog.length})`)}
        {report.backlog.map(item => bullet(`idea-${item}`, ' ', item.replace(/^- /, '')))}
      </Box>
    )
  })
}
