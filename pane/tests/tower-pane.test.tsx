import { expect, mock, test } from 'claude-code/testing'
import type { TestBody } from 'claude-code/testing'
import type { On } from 'claude-code'

// A hub report shaped exactly like hub-scan.sh's output: one needs-you card, one session waiting.
const REPORT = `REPO: poot
LEDGER: /vault/Projects/poot/Ledger

== WORKTREES ==
- main | main checkout (no card expected) | /p
- rolly-polly | status: needs-you | ticket: #36 | updated: 2026-11-05T10:00 | /p/worktrees/rolly-polly
- copilot | status: running | ticket: none | updated: 2026-11-05T09:00 | /p/worktrees/tracker
WIP: 2 of 3 (running + needs-you)

== LIVE SESSIONS (agent view) ==
- main | 1 open: poot-68(int:idle)
- copilot | 1 open: copilot-1(int:waiting) waiting for: approve Bash

== ORPHAN CARDS (worktree gone) ==
(none)

== GITHUB ==
(skipped: --no-github)

== BACKLOG (parked ideas) ==
(empty)
`
const CALM = REPORT.replace('status: needs-you', 'status: running').replace('int:waiting', 'int:idle')

// Orca's terminals in rolly-polly: a plain shell that printed last, and the Claude session.
const ORCA_TERMINALS = [
  { handle: 'term_shell', title: 'Terminal 1', connected: true, lastOutputAt: 300 },
  { handle: 'term_claude_old', title: 'Old Claude', connected: true, lastOutputAt: 100, agentIdentity: 'claude' },
  { handle: 'term_claude', title: 'Terminal 2', connected: true, lastOutputAt: 200, agentIdentity: 'claude' },
]

const PANE = {
  plugin: 'tower-pane',
  component: 'Pane',
  requestId: 'tower',
  props: { title: 'tower', isFocused: false, bodyColumns: 100, placement: 'dock', scroll: { offset: 0, bodyRows: 60 }, view: {} },
} as const

type World = { report: string; statuses: (string | undefined)[]; toasts: string[]; argvs: string[][]; files: Record<string, string>; clock: ReturnType<typeof mock.clock> }

/** The engine beneath the mod: canned commands, captured status line and toasts. */
function world(on: On, report: string): World {
  const w: World = { report, statuses: [], toasts: [], argvs: [], files: {}, clock: mock.clock(on, { now: 0 }) }
  mock.env(on, { HOME: '/home' })
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', (_, e) => {
    w.statuses.push(e.text)
    return { value: undefined }
  })
  on('ui.toast', (_, e) => {
    w.toasts.push(e.text)
    return { value: undefined }
  })
  on('process.run', (_, e) => {
    w.argvs.push([...e.argv])
    const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
    if (e.argv[0] === 'claude') return ok(JSON.stringify([{ id: 'tower@tower', enabled: true, installPath: '/tower' }]))
    if (e.argv[0] === 'orca' && e.argv[2] === 'list') return ok(JSON.stringify({ ok: true, result: { terminals: ORCA_TERMINALS } }))
    if (e.argv[0] === 'orca' || e.argv[0] === 'open') return ok('{"ok":true}')
    if (e.argv[0] === 'rm') {
      delete w.files[e.argv[2] ?? '']
      return ok('')
    }
    if (e.argv.includes('--no-github')) return ok(w.report)
    return ok(w.report.replace('(skipped: --no-github)', '-- my open PRs --\n- #57 Alert copilot [feat/37-copilot] review:none'))
  })
  on('fs.write', (_, e) => {
    w.files[e.path] = e.text
    return { value: undefined }
  })
  on('fs.read', (_, e) => (e.path in w.files ? { value: w.files[e.path] ?? '' } : { deny: 'ENOENT' }))
  on('fs.list', (_, e) => ({
    value: Object.keys(w.files).filter(f => f.startsWith(`${e.path}/`)).map(f => ({ name: f.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: 0, isLink: false })),
  }))
  return w
}

/** Starts the mod as a session in `cwd` with id `sessionId` would, and lets its background start finish. */
async function startSession($: Parameters<TestBody>[0], on: On, w: World, sessionId: string, cwd: string) {
  on('session.id', () => ({ value: sessionId }))
  on('session.cwd', () => ({ value: cwd }))
  on('command.register', () => ({ value: { command: 'tower' } }))
  on('session.start', () => ({ cwd }))
  await $.session.start({ cwd, surface: 'terminal' })
  await w.clock.settle() // session.start's work runs unawaited
}

test('the pane lists what needs you, and the status line counts it', async ($, on) => {
  const w = world(on, REPORT)
  await $.command.run({ command: 'tower' })

  expect(w.argvs.some(argv => argv[0] === 'bash' && argv[1] === '/tower/skills/hub/scripts/hub-scan.sh' && argv[2] === '--no-github')).toBe(true)
  expect(w.statuses.at(-1)).toBe('tower WIP 2/3 · 2 need you')

  // A card and its own waiting session are one row, not two.
  w.report = REPORT.replace('copilot-1(int:waiting)', 'copilot-1(int:idle)').replace('- copilot | 1 open', '- rolly-polly | 1 open')
  w.report = w.report.replace('copilot-1(int:idle) waiting for: approve Bash', 'rolly-1(int:waiting) waiting for: input needed')
  await $.command.run({ command: 'tower' })
  expect(w.statuses.at(-1)).toBe('tower WIP 2/3 · 1 needs you')
  w.report = REPORT
  await $.command.run({ command: 'tower' }) // back to the two-row report for the pane checks

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'orca-rolly-polly' })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^card just updated$/ })).toBeTruthy() // rolly-polly's detail line: no waiting session of its own
  expect(await ui.find({ type: 'Text', text: /^card just updated · waiting session copilot-1$/ })).toBeTruthy() // copilot's worktree row
  expect((await ui.find({ type: 'Text', text: /copilot-1: approve Bash/ }))?.text).toContain('copilot')
  expect(await ui.find({ type: 'Text', text: /NEEDS YOU/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /\/p\/worktrees/ })).toBeUndefined() // no paths in the pane
  expect(await ui.find({ type: 'Text', text: /Press Refresh GitHub/ })).toBeTruthy()
  await ui.unmount()
})

test('a needs-you card whose session is working again is shown as running, not as needing you', async ($, on) => {
  // rolly-polly's card still says needs-you, but its session is busy: hub-scan.sh's section 1 already says running.
  const resumed = REPORT.replace(
    '- rolly-polly | status: needs-you |',
    '- rolly-polly | status: running | card: needs-you |',
  ).replace('updated: 2026-11-05T10:00 |', 'updated: 2026-11-05T10:00 | reason: the card says needs-you but session rolly-ad is busy: you already answered |')
    .replace('- copilot | 1 open: copilot-1(int:waiting) waiting for: approve Bash', '- rolly-polly | 1 open: rolly-ad(int:busy)')
  const w = world(on, resumed)
  await $.command.run({ command: 'tower' })
  expect(w.statuses.at(-1)).toBe('tower WIP 2/3')
  expect(w.toasts).toHaveLength(0)

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /Nothing needs you right now/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /you already answered · busy session rolly-ad/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /NEEDS YOU/ })).toBeUndefined()
  await ui.unmount()
})

test('a report from tower before 0.9.0 still shows a resumed needs-you card as running', async ($, on) => {
  // REPORT's rows have no card field, as hub-scan.sh printed them before tower state decided the status.
  const w = world(on, REPORT.replace('- copilot | 1 open: copilot-1(int:waiting) waiting for: approve Bash', '- rolly-polly | 1 open: rolly-ad(int:busy)'))
  await $.command.run({ command: 'tower' })
  expect(w.statuses.at(-1)).toBe('tower WIP 2/3')
})

// hub-scan.sh's real output for the fixture in tests/test_state.py, which checks it byte for byte: three workers the
// hub and the pane used to disagree on (busy with no card; busy with a stale needs-you card; waiting with a running
// card). The hub reports from its GROUPS section; this test shows the pane draws the same status for each.
const HUB_AND_PANE = `REPO: repo
LEDGER: /vault/repo/Ledger

== WORKTREES ==
- main | status: main | main checkout (no card expected) | reason: the main checkout | /p
- feat-1 | status: running | card: none | ticket: none | updated: ? | reason: no card yet, but session w1 is busy | /p/worktrees/feat-1
- feat-7 | status: running | card: needs-you | ticket: #7 | updated: 2026-11-06T09:00 | reason: the card says needs-you but session w7 is busy: you already answered | /p/worktrees/feat-7
- feat-8 | status: needs-you | card: running | ticket: #8 | updated: 2026-11-06T09:00 | reason: session w8 is waiting on you (approve Bash); the card says running | /p/worktrees/feat-8
WIP: 3 of 3 (execution slots: running + needs-you, main excluded) — AT LIMIT
UNSHIPPED: feat-1 (1 commit), feat-7 (1 commit), feat-8 (1 commit) not on main
PR approval: every

== LIVE SESSIONS (agent view) ==
- main | 1 open: hub-1(int:idle)
- feat-1 | 1 open: w1(int:busy)
- feat-7 | 1 open: w7(int:busy)
- feat-8 | 1 open: w8(int:waiting) waiting for: approve Bash

== ORPHAN CARDS (worktree gone) ==
(none)

== GROUPS (report in this order) ==
NEEDS YOU
- feat-8 | ticket: #8 | session w8 is waiting on you (approve Bash); the card says running
BLOCKED
(none)
RUNNING
- feat-1 | ticket: none | no card yet, but session w1 is busy
- feat-7 | ticket: #7 | the card says needs-you but session w7 is busy: you already answered
WAITING ON CI OR REVIEW
(none)
DONE
(none)
HOUSEKEEPING
- feat-1 | no card

== GITHUB ==
(skipped: --no-github)

== BACKLOG (parked ideas) ==
(empty)
`

test('the pane shows the hub\'s status for every worktree of the shared fixture', async ($, on) => {
  const w = world(on, HUB_AND_PANE)
  await $.command.run({ command: 'tower' })
  expect(w.statuses.at(-1)).toBe('tower WIP 3/3 · 1 needs you') // the hub: WIP 3 of 3, one row under NEEDS YOU

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // NEEDS YOU: feat-8 only. RUNNING: feat-1 and feat-7, as in the hub's GROUPS.
  expect((await ui.findAll({ type: 'Text', text: /^ NEEDS YOU\s*$/ })).length).toBe(1)
  expect((await ui.findAll({ type: 'Text', text: /^ RUNNING\s*$/ })).length).toBe(2)
  expect(await ui.find({ type: 'Text', text: /^card just updated · w8 waiting: approve Bash$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^card just updated, the card says needs-you but session w7 is busy: you already answered · busy session w7$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^busy session w1$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /feat-1: no card yet/ })).toBeTruthy()
  await ui.unmount()
})

test('a toast only for what is new since the last scan', async ($, on) => {
  const w = world(on, CALM)
  await $.command.run({ command: 'tower' }) // baseline: nothing needs you
  w.report = REPORT
  await $.command.run({ command: 'tower' })
  const before = w.toasts.length
  await $.command.run({ command: 'tower' }) // same items again: no new toasts

  expect(w.toasts.some(t => t.includes('rolly-polly: card needs you'))).toBe(true)
  expect(w.toasts.some(t => t.includes('session copilot-1 is waiting'))).toBe(true)
  expect(w.toasts.length).toBe(before)
})

test('Refresh GitHub runs the full scan and the pane keeps its GitHub section', async ($, on) => {
  const w = world(on, REPORT)
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'github' })
  expect(w.argvs.some(argv => argv[0] === 'bash' && !argv.includes('--no-github'))).toBe(true)
  expect(await ui.find({ type: 'Text', text: /^ #57$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^Alert copilot$/ })).toBeTruthy()

  // The next quick refresh skips GitHub; the pane shows the stored section, stamped with its time.
  await ui.press({ key: 'refresh' })
  expect(w.argvs.at(-1)).toContain('--no-github')
  expect(await ui.find({ type: 'Text', text: /^Alert copilot$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^as of / })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /review:none|\[feat\/37-copilot\]/ })).toBeUndefined() // PR rows: number + title only
  await ui.unmount()
})

test('a scan that cannot run shows its reason instead of freezing the pane', async ($, on) => {
  mock.clock(on, { now: 0 })
  const statuses: (string | undefined)[] = []
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', (_, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
  on('process.run', (_, e) => {
    if (e.argv[0] === 'claude') {
      return { value: { exitCode: 0, stdout: JSON.stringify([{ id: 'tower@tower', enabled: true, installPath: '/tower' }]), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    }
    return { deny: 'spawn bash ENOENT' } // the command could not start: $.process.run rejects
  })

  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /tower scan failed: scan didn't run/ }))?.text).toContain('ENOENT')
  expect(statuses.at(-1)).toBeUndefined()
  await ui.unmount()
})

// ---------- interactions ----------

type Acts = { prompts: string[]; copied: string[]; sent: Array<{ to: unknown; text: string }>; toasts: string[] }

/** The engine beneath the pane's actions: captures prompts, clipboard, messages and toasts. */
function actions(on: On): Acts {
  const acts: Acts = { prompts: [], copied: [], sent: [], toasts: [] }
  on('prompt.submit', (_, e) => {
    acts.prompts.push(e.text)
    return { text: e.text }
  })
  on('ui.copy', (_, e) => {
    acts.copied.push(e.text)
    return { value: { isCopied: true } }
  })
  on('session.send', (_, e) => {
    acts.sent.push({ to: e.to, text: e.text })
    return { isDelivered: true as const }
  })
  return acts
}

const NO_CARD = REPORT.replace(
  '- copilot | status: running | ticket: none | updated: 2026-11-05T09:00 | /p/worktrees/tracker',
  '- copilot | status: NO CARD | /p/worktrees/tracker',
)

test("clicking a name switches Orca to that worktree's Claude terminal; card opens Obsidian", async ($, on) => {
  actions(on)
  const w = world(on, REPORT)
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  await ui.press({ key: 'orca-rolly-polly' })
  const orca = w.argvs.filter(argv => argv[0] === 'orca' || argv[0] === 'open')
  expect(orca[0]).toEqual(['orca', 'terminal', 'list', '--worktree', 'path:/p/worktrees/rolly-polly', '--json'])
  expect(orca[1]).toEqual(['orca', 'terminal', 'switch', '--terminal', 'term_claude', '--json']) // newest Claude one, not the shell
  expect(orca[2]).toEqual(['open', '-a', 'Orca'])
  expect(w.toasts.at(-1)).toBe('Orca: rolly-polly · Terminal 2')

  await ui.press({ key: 'card-rolly-polly' })
  expect(w.argvs.at(-1)).toEqual(['open', `obsidian://open?path=${encodeURIComponent('/vault/Projects/poot/Ledger/rolly-polly.md')}`])
  await ui.unmount()
})

test("What's next?, Start #N and Park send requests to this session's Claude", async ($, on) => {
  const acts = actions(on)
  world(on, REPORT)
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  await ui.press({ key: 'next' })
  await ui.press({ key: 'github' }) // loads the ready-for-agent list
  await ui.input({ key: 'park-0', text: '  worm theme for the dashboard ' })

  expect(acts.prompts[0]).toBe("Using the tower hub skill: what's next?")
  expect(acts.prompts[1]).toBe('Using the tower hub skill: park: worm theme for the dashboard')
  expect(await ui.find({ type: 'Input', key: 'park-1' })).toBeTruthy() // a fresh, empty field
  await ui.unmount()
})

test('a ready-for-agent issue starts with one click', async ($, on) => {
  const acts = actions(on)
  const w = world(on, REPORT)
  w.report = REPORT
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // A full scan whose GitHub section has one ready-for-agent issue.
  w.report = REPORT.replace('(skipped: --no-github)', '-- open issues (all — your repo) --\n- #39 Copilot: analyst level calls [ready-for-agent]\n- #44 poot theme [ready-for-human]')
  await ui.press({ key: 'github' })

  await ui.press({ key: 'start-39' })
  expect(acts.prompts.at(-1)).toBe('Using the tower hub skill: start #39')
  expect(await ui.find({ type: 'Text', text: /\+ 1 other open issues/ })).toBeTruthy()
  await ui.unmount()
})

test('nudge asks the sessions of a cardless worktree to write their card', async ($, on) => {
  const acts = actions(on)
  world(on, NO_CARD)
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  await ui.press({ key: 'nudge-copilot' })
  expect(acts.sent).toHaveLength(1)
  expect(acts.sent[0]?.to).toBe('copilot-1')
  expect(acts.sent[0]?.text).toContain('status-card skill')
  await ui.unmount()
})

// ---------- pop-up questions relayed to the pane ----------

const QUESTION_FILE = '/home/.claude/tower/questions/worker-1.json'
const ASKED = [{ question: 'Which mascot?', header: 'Mascot', multiSelect: false, options: [{ label: 'Worm', description: 'wiggles' }, { label: 'Ball', description: 'rolls' }] }]

test("a worker's pop-up is relayed to a file, and an answer from the pane settles it", async ($, on) => {
  const w = world(on, REPORT)
  on('tool.call', { tool: 'AskUserQuestion' }, () => new Promise(() => {})) // the pop-up stays open: nobody answers there
  on('session.receive', (_, e) => ({ text: e.text })) // a message nobody took: queued for the model
  await startSession($, on, w, 'worker-1', '/p/worktrees/rolly-polly')

  const asked = $.tool.call({ tool: 'AskUserQuestion', questions: ASKED })
  await w.clock.settle()
  const relayed = JSON.parse(w.files[QUESTION_FILE] ?? '{}') as { id: string; cwd: string; questions: unknown }
  expect(relayed.cwd).toBe('/p/worktrees/rolly-polly')
  expect(relayed.questions).toEqual(ASKED)

  // A message with the wrong id is left for the model; the right one is taken.
  const wrong = await $.session.receive({ origin: { kind: 'peer', plugin: 'tower-pane' }, text: `tower-answer ${'0'.repeat(36)} %7B%7D` })
  expect('consumed' in wrong).toBe(false)
  const answer = encodeURIComponent(JSON.stringify({ 'Which mascot?': 'Worm' }))
  const taken = await $.session.receive({ origin: { kind: 'peer', plugin: 'tower-pane' }, text: `tower-answer ${relayed.id} ${answer}` })
  expect(taken).toEqual({ consumed: 'an answer from the tower pane' })

  const result = await asked
  expect(result.result).toEqual({ questions: ASKED, answers: { 'Which mascot?': 'Worm' } })
  expect(QUESTION_FILE in w.files).toBe(false) // removed once answered
})

test('answered in the pop-up first, the relay steps aside and removes its file', async ($, on) => {
  const w = world(on, REPORT)
  on('tool.call', { tool: 'AskUserQuestion' }, () => ({ result: { questions: ASKED, answers: { 'Which mascot?': 'Ball' } } }))
  await startSession($, on, w, 'worker-1', '/p/worktrees/rolly-polly')

  const result = await $.tool.call({ tool: 'AskUserQuestion', questions: ASKED })
  expect(result.result).toEqual({ questions: ASKED, answers: { 'Which mascot?': 'Ball' } })
  expect(QUESTION_FILE in w.files).toBe(false)
})

test("the hub pane shows another session's question and sends the clicked option back", async ($, on) => {
  const acts = actions(on)
  const w = world(on, REPORT)
  w.files[QUESTION_FILE] = JSON.stringify({ id: 'q1', sessionId: 'worker-1', cwd: '/p/worktrees/rolly-polly/web', askedAt: 0, questions: ASKED })
  w.files['/home/.claude/tower/questions/elsewhere.json'] = JSON.stringify({ id: 'q2', sessionId: 'other', cwd: '/other/repo', askedAt: 0, questions: ASKED })
  await startSession($, on, w, 'hub-1', '/p')
  await w.clock.advance(3_000) // the question poll
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  expect(await ui.find({ type: 'Text', text: /QUESTION/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^Which mascot\?$/ })).toBeTruthy()
  expect((await ui.findAll({ type: 'Text', text: /^Which mascot\?$/ })).length).toBe(1) // the other repo's question isn't here

  await ui.press({ key: 'answer-q1-Which mascot?-Worm' }) // { sessionId } reaches session.send spelled as the id
  expect(acts.sent.at(-1)).toEqual({ to: 'worker-1', text: `tower-answer q1 ${encodeURIComponent(JSON.stringify({ 'Which mascot?': 'Worm' }))}` })
  expect(await ui.find({ type: 'Text', text: /^Which mascot\?$/ })).toBeUndefined() // gone once delivered
  await ui.unmount()
})
