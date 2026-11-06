import { expect, mock, test } from 'claude-code/testing'
import type { TestBody } from 'claude-code/testing'
import type { On } from 'claude-code'

import type { StateWorktree, TowerState } from '../types'
import { issuesToShow, parseGithub } from '../hooks/register'
import { SHARED } from './fixtures/tower-state'

// SHARED is `tower state --json`'s real output for the fixture in tests/test_state.py, which checks it: feat-3's card
// says needs-you; feat-8 waits on an approval and feat-9 on a question; feat-1 and feat-7 are busy; feat-5 is idle
// with no card. Variants below change it the way the core would, never by hand-written JSON.

/** SHARED with each worktree passed through change. */
function variant(change: (w: StateWorktree) => StateWorktree, base: TowerState = SHARED): TowerState {
  return { ...base, worktrees: base.worktrees.map(change) }
}

/** Nothing needs you: every needs-you worktree running, every session busy instead of waiting. */
const CALM = variant(w => w.status !== 'needs-you' ? w : {
  ...w,
  status: 'running',
  reason: 'busy',
  sessions: w.sessions.map(s => ({ ...s, activity: 'busy', waiting_for: '' })),
})

// hub-scan.sh's GitHub section (only Refresh GitHub runs it): one PR, twelve open issues, one ready for an agent.
const ISSUES = ['- #61 Copilot: analyst [level] calls [ready-for-agent] [enhancement]', ...Array.from({ length: 11 }, (_, i) => `- #${60 - i} idea ${60 - i}`)]
const githubReport = (issues: string[]) => `REPO: repo

== GITHUB ==
repo: me/repo | you: me
-- my open PRs --
- #57 Alert copilot [feat/37-copilot] review:none
-- open issues (all — your repo) --
${issues.join('\n') || '(none)'}

== BACKLOG (parked ideas) ==
(empty)
`

// Orca's terminals in feat-8: a plain shell that printed last, the Claude session waiting on you, and a second
// Claude session that printed more recently. Orca's agent states say which pane waits.
const ORCA_TERMINALS = [
  { handle: 'term_shell', title: 'Terminal 1', connected: true, lastOutputAt: 300, tabId: 't1', leafId: 'l1' },
  { handle: 'term_waiting', title: 'Asks you', connected: true, lastOutputAt: 100, agentIdentity: 'claude', tabId: 't2', leafId: 'l2' },
  { handle: 'term_claude', title: 'Terminal 3', connected: true, lastOutputAt: 200, agentIdentity: 'claude', tabId: 't3', leafId: 'l3' },
]
const ORCA_PS = {
  ok: true,
  result: {
    worktrees: [
      { path: '/p/worktrees/feat-8', agents: [{ paneKey: 't3:l3', state: 'working', updatedAt: 5 }, { paneKey: 't2:l2', state: 'waiting', updatedAt: 4 }] },
    ],
  },
}

const PANE = {
  plugin: 'tower-pane',
  component: 'Pane',
  requestId: 'tower',
  props: { title: 'tower', isFocused: false, bodyColumns: 100, placement: 'dock', scroll: { offset: 0, bodyRows: 60 }, view: {} },
} as const

const BAND = {
  plugin: 'tower-pane',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const

type World = {
  state: TowerState
  issues: string[]
  stateRun: { exitCode: number; stderr: string } | null
  orcaPs: unknown
  statuses: (string | undefined)[]
  toasts: string[]
  argvs: string[][]
  files: Record<string, string>
  writes: string[]
  readError?: string
  clock: ReturnType<typeof mock.clock>
}

/** The engine beneath the mod: canned commands, captured status line, toasts and file writes. */
function world(on: On, state: TowerState, env: Record<string, string> = {}): World {
  const w: World = { state, issues: ISSUES, stateRun: null, orcaPs: ORCA_PS, statuses: [], toasts: [], argvs: [], files: {}, writes: [], clock: mock.clock(on, { now: 0 }) }
  mock.env(on, env)
  on('ui.open', () => ({ value: { isPlaced: true } }))
  // The engine's own band above the prompt, drawn when no plugin draws one.
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine band</Text>
  })
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
    const ran = (exitCode: number, stdout: string, stderr = '') => ({ value: { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false } })
    const ok = (stdout: string) => ran(0, stdout)
    if (e.argv[0] === 'claude') return ok(JSON.stringify([{ id: 'tower@tower', enabled: true, installPath: '/tower' }]))
    if (e.argv[0] === 'git') return ok('true\n')
    if (e.argv[0] === 'python3') return w.stateRun ? ran(w.stateRun.exitCode, '', w.stateRun.stderr) : ok(JSON.stringify(w.state))
    if (e.argv[0] === 'bash') return ok(githubReport(w.issues))
    if (e.argv[0] === 'orca' && e.argv[2] === 'list') return ok(JSON.stringify({ ok: true, result: { terminals: ORCA_TERMINALS } }))
    if (e.argv[0] === 'orca' && e.argv[1] === 'worktree') return w.orcaPs === null ? ran(1, '', 'orca: not running') : ok(JSON.stringify(w.orcaPs))
    if (e.argv[0] === 'orca' || e.argv[0] === 'open') return ok('{"ok":true}')
    return ran(127, '', `unexpected command ${e.argv.join(' ')}`)
  })
  on('fs.read', (_, e) => (e.path in w.files ? { value: w.files[e.path] ?? '' } : { deny: w.readError ?? 'ENOENT: no such file' }))
  on('fs.write', (_, e) => {
    w.writes.push(e.path)
    return { value: undefined }
  })
  return w
}

/** Starts the mod as a session in `cwd` would, and lets its background start (the first scan) finish. */
async function startSession($: Parameters<TestBody>[0], on: On, w: World, cwd: string) {
  on('session.id', () => ({ value: 'session-1' }))
  on('session.cwd', () => ({ value: cwd }))
  on('command.register', () => ({ value: { command: 'tower' } }))
  on('session.start', () => ({ cwd }))
  await $.session.start({ cwd, surface: 'terminal' })
  await w.clock.settle() // session.start's work runs unawaited
}

test('the pane draws tower state: what needs you, the WIP count, and every worktree', async ($, on) => {
  const w = world(on, SHARED)
  w.files['/vault/repo/Ledger/_backlog.md'] = '- [ ] 2026-11-05 — worm theme for the dashboard\n'
  await $.command.run({ command: 'tower' })

  expect(w.argvs.some(argv => argv.join(' ') === 'python3 -I /tower/scripts/run-tower.py state --json')).toBe(true)
  expect(w.argvs.some(argv => argv[0] === 'bash')).toBe(false) // hub-scan.sh runs only for GitHub
  expect(w.statuses.at(-1)).toBe('tower WIP 5/3 · 3 need you')

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /AT LIMIT/ })).toBeTruthy()
  // Needs you: feat-8 and feat-9 wait in their sessions (WAITING with what they need), feat-3's card asks (NEEDS YOU).
  expect((await ui.findAll({ type: 'Text', text: /^ WAITING\s*$/ })).length).toBe(2)
  expect((await ui.findAll({ type: 'Text', text: /^ NEEDS YOU\s*$/ })).length).toBe(1)
  expect(await ui.find({ type: 'Text', text: /^w8: approve Bash$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^w9: input needed$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^card just updated$/ })).toBeTruthy() // feat-3
  // 1-9 press the Needs you rows' names in order (the kit presses by key; the engine maps the hotkey to the same press).
  expect((await ui.find({ type: 'Button', key: 'orca-feat-3' }))?.props.hotkey).toBe('1')
  expect((await ui.find({ type: 'Button', key: 'orca-feat-8' }))?.props.hotkey).toBe('2')
  expect((await ui.find({ type: 'Button', key: 'orca-feat-9' }))?.props.hotkey).toBe('3')
  expect((await ui.find({ type: 'Button', key: 'orca-feat-1' }))?.props.hotkey).toBeUndefined() // Worktrees rows have none
  // Worktrees: the core's status, and its reason where it overrules the card.
  expect((await ui.findAll({ type: 'Text', text: /^ RUNNING\s*$/ })).length).toBe(2)
  expect(await ui.find({ type: 'Text', text: /^card just updated, the card says needs-you but session w7 is busy: you already answered · busy session w7$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^busy session w1$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^idle session w5$/ })).toBeTruthy()
  // Housekeeping, and the parked ideas from the ledger.
  // Housekeeping is the core's own list (the hub's Housekeeping group), drawn as it comes.
  expect(await ui.find({ type: 'Text', text: /^feat-5: no card$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^feat__2-gone: orphan card \(worktree gone\), status: done$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /worm theme for the dashboard/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /\/p\/worktrees/ })).toBeUndefined() // no paths in the pane
  expect(await ui.find({ type: 'Text', text: /Press Refresh GitHub/ })).toBeTruthy()
  await ui.unmount()
})

test('a toast only for what is new since the last scan', async ($, on) => {
  const w = world(on, CALM)
  await $.command.run({ command: 'tower' }) // baseline: nothing needs you
  expect(w.statuses.at(-1)).toBe('tower WIP 5/3')
  w.state = SHARED
  await $.command.run({ command: 'tower' })
  const before = w.toasts.length
  await $.command.run({ command: 'tower' }) // same items again: no new toasts

  expect(w.toasts).toContain('tower: feat-3: card needs you — /tower')
  expect(w.toasts).toContain('tower: feat-8: session w8 is waiting — /tower')
  expect(w.toasts).toContain('tower: feat-9: session w9 is waiting — /tower')
  expect(w.toasts.length).toBe(before)
})

test('a scan that fails shows its reason instead of freezing the pane', async ($, on) => {
  const w = world(on, SHARED)
  w.stateRun = { exitCode: 1, stderr: "python3: can't open file '/tower/scripts/run-tower.py': No such file\n" }
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /tower scan failed: / }))?.text).toContain('the pane needs tower 0.9.1 or later')
  expect(w.statuses.at(-1)).toBeUndefined()
  await ui.unmount()
})

test("a scan that can't start shows why", async ($, on) => {
  mock.clock(on, { now: 0 })
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', () => ({ value: undefined }))
  on('process.run', (_, e) => {
    if (e.argv[0] === 'claude') {
      return { value: { exitCode: 0, stdout: JSON.stringify([{ id: 'tower@tower', enabled: true, installPath: '/tower' }]), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    }
    return { deny: 'spawn python3 ENOENT' } // the command could not start: $.process.run rejects
  })
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /tower scan failed: scan didn't run/ }))?.text).toContain('ENOENT')
  await ui.unmount()
})

test("clicking a name lands on the terminal Orca says is waiting, not the newest one", async ($, on) => {
  const w = world(on, SHARED)
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  await ui.press({ key: 'orca-feat-8' })
  const orca = w.argvs.filter(argv => argv[0] === 'orca' || argv[0] === 'open')
  expect(orca[0]).toEqual(['orca', 'terminal', 'list', '--worktree', 'path:/p/worktrees/feat-8', '--json'])
  expect(orca[1]).toEqual(['orca', 'worktree', 'ps', '--limit', '500', '--json'])
  expect(orca[2]).toEqual(['orca', 'terminal', 'switch', '--terminal', 'term_waiting', '--json'])
  expect(orca[3]).toEqual(['open', '-a', 'Orca'])
  expect(w.toasts.at(-1)).toBe('Orca: feat-8 · Asks you (waiting on you)')

  // Orca can't say which agent waits: the newest Claude terminal, and the toast says it was a guess.
  w.orcaPs = null
  await ui.press({ key: 'orca-feat-8' })
  expect(w.argvs.filter(argv => argv[2] === 'switch').at(-1)).toEqual(['orca', 'terminal', 'switch', '--terminal', 'term_claude', '--json'])
  expect(w.toasts.at(-1)).toBe("Orca: feat-8 · Terminal 3 (Orca didn't say which terminal waits: the newest Claude one)")

  await ui.press({ key: 'card-feat-8' })
  expect(w.argvs.at(-1)).toEqual(['open', `obsidian://open?path=${encodeURIComponent('/vault/repo/Ledger/feat-8.md')}`])
  await ui.unmount()
})

test('in a worker inside Orca, 0 goes back to the hub; the hub and sessions outside Orca get no band', async ($, on) => {
  const w = world(on, SHARED, { ORCA_TERMINAL_HANDLE: 'term_waiting' })
  await startSession($, on, w, '/p/worktrees/feat-8/src')
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const back = await band.find({ type: 'Button', key: 'hub' })
  expect(back?.props.hotkey).toBe('0')
  await band.press({ key: 'hub' })
  expect(w.argvs.find(argv => argv[0] === 'orca')).toEqual(['orca', 'terminal', 'list', '--worktree', 'path:/p', '--json'])
  await band.unmount()

  // While a question's survey holds the band, it steps aside.
  const survey = await $.ui.mount({ ...BAND, props: { ...BAND.props, hasSurvey: true }, surface: 'terminal' })
  expect(await survey.find({ type: 'Button', key: 'hub' })).toBeUndefined()
  await survey.unmount()
})

test('the hub session gets no back-to-hub band', async ($, on) => {
  const w = world(on, SHARED, { ORCA_TERMINAL_HANDLE: 'term_hub' })
  await startSession($, on, w, '/p')
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', key: 'hub' })).toBeUndefined()
  await band.unmount()
})

test('a worker outside Orca gets no back-to-hub band', async ($, on) => {
  const w = world(on, SHARED)
  await startSession($, on, w, '/p/worktrees/feat-8')
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', key: 'hub' })).toBeUndefined()
  await band.unmount()
})

test("a worker's pop-up question stays in its own session: nothing is relayed", async ($, on) => {
  const w = world(on, SHARED)
  const asked = [{ question: 'Which mascot?', header: 'Mascot', multiSelect: false, options: [{ label: 'Worm', description: 'wiggles' }, { label: 'Ball', description: 'rolls' }] }]
  on('tool.call', { tool: 'AskUserQuestion' }, () => ({ result: { questions: asked, answers: { 'Which mascot?': 'Worm' } } }))
  await startSession($, on, w, '/p/worktrees/feat-9')

  const result = await $.tool.call({ tool: 'AskUserQuestion', questions: asked })
  expect(result.result).toEqual({ questions: asked, answers: { 'Which mascot?': 'Worm' } })
  expect(w.writes).toEqual([]) // no question file, and no answer buttons in any pane
})

test('every open issue is listed, ten at most: ready ones start, the rest are discussed', async ($, on) => {
  const acts = actions(on)
  const w = world(on, SHARED)
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'github' })
  expect(w.argvs.some(argv => argv.join(' ') === 'bash /tower/skills/hub/scripts/hub-scan.sh')).toBe(true)

  expect(await ui.find({ type: 'Text', text: /^ #57$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^Alert copilot$/ })).toBeTruthy() // PR rows: number + title only
  expect(await ui.find({ type: 'Text', text: /^Open issues \(12\)/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^Copilot: analyst \[level\] calls$/ })).toBeTruthy() // a bracket in a title stays
  expect(await ui.find({ type: 'Button', key: 'discuss-61' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'start-60' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'discuss-52' })).toBeTruthy() // the tenth
  expect(await ui.find({ type: 'Button', key: 'discuss-51' })).toBeUndefined() // the eleventh: counted below
  expect(await ui.find({ type: 'Text', text: /^ {2}\+ 2 more$/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^as of / })).toBeTruthy()

  await ui.press({ key: 'start-61' })
  await ui.press({ key: 'discuss-60' })
  expect(acts.prompts).toEqual(['Using the tower hub skill: start #61', 'Using the tower hub skill: discuss #60'])

  // The 30-second refresh skips GitHub; the pane keeps the stored section.
  await ui.press({ key: 'refresh' })
  expect(w.argvs.filter(argv => argv[0] === 'bash').length).toBe(1)
  expect(await ui.find({ type: 'Button', key: 'start-61' })).toBeTruthy()
  await ui.unmount()
})

// ---------- interactions ----------

type Acts = { prompts: string[]; sent: Array<{ to: unknown; text: string }> }

/** The engine beneath the pane's actions: captures prompts and messages. */
function actions(on: On): Acts {
  const acts: Acts = { prompts: [], sent: [] }
  on('prompt.submit', (_, e) => {
    acts.prompts.push(e.text)
    return { text: e.text }
  })
  on('session.send', (_, e) => {
    acts.sent.push({ to: e.to, text: e.text })
    return { isDelivered: true as const }
  })
  return acts
}

test('with no open issues the GitHub section has no issue rows and no "+ more"', async ($, on) => {
  const w = world(on, SHARED)
  w.issues = []
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'github' })
  expect(await ui.find({ type: 'Text', text: /^Open issues \(0\)/ })).toBeTruthy()
  expect(await ui.find({ type: 'Button', key: 'discuss-60' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /more$/ })).toBeUndefined()
  await ui.unmount()
})

test('parseGithub reads PRs and every issue, ready or not', () => {
  const github = parseGithub(['-- my open PRs --', '- #57 Alert [feat/x] review:none', '-- open issues (all — your repo) --', ...ISSUES.slice(0, 3), '(gh failed: rate limited)'])
  expect(github.prs).toEqual(['#57 Alert [feat/x] review:none'])
  expect(github.issues).toEqual([
    { number: '61', title: 'Copilot: analyst [level] calls', isReady: true },
    { number: '60', title: 'idea 60', isReady: false },
    { number: '59', title: 'idea 59', isReady: false },
  ])
  expect(github.note).toBe('(gh failed: rate limited)')
  expect(parseGithub(['-- open issues assigned to you --', '(none)']).issues).toEqual([])
})

test('a ready-for-agent issue older than the newest ten still gets its start button', () => {
  const issues = parseGithub(['-- open issues (all — your repo) --', ...ISSUES.slice(1), '- #12 old but ready [ready-for-agent]']).issues
  const shown = issuesToShow(issues)
  expect(shown.map(i => i.number)).toEqual(['12', '60', '59', '58', '57', '56', '55', '54', '53', '52'])
})

test('a tower too old for the pane says to update it, and a real failure keeps its own reason', async ($, on) => {
  const w = world(on, { ...SHARED, housekeeping: undefined } as unknown as TowerState) // tower 0.9.0's JSON
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /tower scan failed: / }))?.text).toContain('claude plugin update tower@tower')
  await ui.unmount()

  w.stateRun = { exitCode: 1, stderr: 'Traceback:\n  File "/tower/scripts/run-tower.py", line 24\nKeyError: boom\n' }
  await $.command.run({ command: 'tower' })
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await again.find({ type: 'Text', text: /tower scan failed: / }))?.text).toBe('tower scan failed: KeyError: boom')
  await again.unmount()
})

test('without live sessions (event log only) nothing offers a nudge it could not deliver', async ($, on) => {
  world(on, { ...SHARED, sessions_source: 'events' })
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'nudge-feat-5' })).toBeUndefined()
  await ui.unmount()
})

test('a backlog that cannot be read says why instead of showing nothing', async ($, on) => {
  const w = world(on, SHARED)
  w.readError = 'EACCES: permission denied'
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /^Couldn't read the parked ideas: / }))?.text).toContain('EACCES')
  await ui.unmount()
})

test("What's next? and Park send requests to this session's Claude", async ($, on) => {
  const acts = actions(on)
  world(on, SHARED)
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  await ui.press({ key: 'next' })
  await ui.input({ key: 'park-0', text: '  worm theme for the dashboard ' })

  expect(acts.prompts).toEqual(["Using the tower hub skill: what's next?", 'Using the tower hub skill: park: worm theme for the dashboard'])
  expect(await ui.find({ type: 'Input', key: 'park-1' })).toBeTruthy() // a fresh, empty field
  await ui.unmount()
})

test('nudge asks the sessions of a cardless worktree to write their card', async ($, on) => {
  const acts = actions(on)
  world(on, SHARED)
  await $.command.run({ command: 'tower' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  expect(await ui.find({ type: 'Button', key: 'nudge-feat-7' })).toBeUndefined() // it has a card
  await ui.press({ key: 'nudge-feat-5' })
  expect(acts.sent).toHaveLength(1)
  expect(acts.sent[0]?.to).toBe('w5')
  expect(acts.sent[0]?.text).toContain('status-card skill')
  await ui.unmount()
})
