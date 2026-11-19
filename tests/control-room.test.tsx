import { expect, test } from 'claude-code/testing'

import {
  ago, alwaysAskWords, attention, cardMeta, describeCall, eventTone, isInternalTool, meter, percentTone,
  usageItems, modelLabel, mtok, parseAgentUsage, pickLimits,
  gateDraft, openerFor, mcpLabel, mergeMcp, refreshUsage, resetMcp, resetProfile, shipAlerts, shipLine, shortenPaths, tokenParts, tokenSplit, toolLabel, usd,
} from '../hooks/register'
import { globToRegExp, inAutoMode, judge, relativePath, roleFor, splitCommand } from '../hooks/permissions'
import type { Profile } from '../hooks/permissions'
import { hintSection, minCalls, nudgeText } from '../hooks/delegation'
import type { Thresholds } from '../hooks/delegation'
import { agentBudget, level, levelText, modelDiffers } from '../hooks/budget'
import {
  address, briefKey, draftQuestion, liveSessions, matchPending, modeClash, newCode, parentMark, projectName, readMark, readParent, replyTarget,
  sessionTree, spawnVerdict, staleKeys, stripMarks, stripReminders, summaryLine, treeUsd, withQuestion,
} from '../hooks/sessions'

const AGENT_USAGE = JSON.stringify({
  session: 's1',
  agents: { main: { tokens: 2_671_611, usd: 1.9364 }, a1: { tokens: 95_534, usd: 0.1083, input: 34, output: 1_500, cache_read: 80_000, cache_write: 14_000, new: 15_534,
    cache_read_pct: 83.7 } },
  total: { tokens: 2_767_145, usd: 2.0447, input: 1_000, output: 40_000, cache_read: 2_600_000, cache_write: 126_145,
    new: 167_145, cache_read_pct: 94 },
  budget: { id: 'est-1', title: 'T', budget_tokens: 47_189_622, used_tokens: 1_954_806, p50_tokens: 6e6, p90_tokens: 3.9e7 },
})

function usageEngine(on: Parameters<Parameters<typeof test>[1]>[1], exitCode = 0, contextPercent = 74) {
  on('session.id', () => ({ value: 's1' }))
  on('session.usage', () => ({
    value: {
      startedAt: 0, context: { window: 1_000_000, percent: contextPercent }, cost: { usd: 2.1 }, changed: ['cost'],
      rateLimits: [
        { kind: 'five_hour', percentUsed: 38, resetsAt: '2023-11-14T23:00:00Z', source: 'api' },
        { kind: 'seven_day', percentUsed: 67, resetsAt: '2023-11-16T02:00:00Z', source: 'api' },
      ],
    },
  }))
  on('process.run', () => ({
    value: { exitCode, stdout: exitCode ? '' : AGENT_USAGE, stderr: exitCode ? 'boom' : '', isStdoutTruncated: false,
      isStderrTruncated: false },
  }))
}

const PANE = {
  plugin: 'token-tools',
  component: 'Pane',
  requestId: 'control-room',
  props: {
    title: 'Phòng điều hành',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

const stores: Map<string, unknown>[] = []

function engine(on: Parameters<Parameters<typeof test>[1]>[1], failBash = false,
  onTool: (e: Record<string, unknown>) => void = () => {}) {
  let clock = 1_700_000_000_000
  on('clock.now', () => ({ value: (clock += 1_500) })) // every reading 1.5 s later
  on('clock.every', () => ({ value: undefined }))
  // An in-memory stand-in for the shared store; tests reach it through `stores` (one per test run, the last).
  const store = new Map<string, unknown>()
  stores.push(store)
  on('store.set', (_, e) => { store.set(e.key, JSON.parse(JSON.stringify(e.value))); return { value: undefined } })
  on('store.get', (_, e) => ({ value: store.get(e.key) }))
  on('store.keys', () => ({ value: [...store.keys()] }))
  on('store.delete', (_, e) => { store.delete(e.key); return { value: undefined } })
  on('command.register', () => ({ value: { command: 'control-room' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  let spawned = 0
  on('agent.spawn', (_, e) => ({ model: e.model ?? 'claude-sonnet-5-5', agentId: `a${++spawned}` }))
  on('agent.list', () => ({ value: [] }))
  on('tool.call', (_, e) => {
    onTool(e as unknown as Record<string, unknown>)
    if ((e as { title?: string }).title === 'blocked') return { result: 'Session not started: Blocked by auto mode' }
    return failBash && e.tool === 'Bash' ? { result: {}, isError: true } : { result: {} }
  })
}

test('describeCall and ago keep lines short and readable', () => {
  expect(describeCall('Edit', { file_path: 'src/A.java' })).toBe('Edit src/A.java')
  expect(describeCall('Bash', { command: 'mvn   -q\n compile' })).toBe('Bash mvn -q compile')
  expect(describeCall('Glob', {})).toBe('Glob')
  expect(describeCall('Bash', { command: 'x'.repeat(100) }).length).toBeLessThan(75)
  expect(ago(45_000)).toBe('45s')
  expect(ago(30 * 60_000)).toBe('30p')
  expect(ago(125 * 60_000)).toBe('2g5p')
})

test('labels: internal tools hidden, MCP names and models shortened', () => {
  expect(isInternalTool('mcp__ccd_turn__wrap_up')).toBe(true)
  expect(isInternalTool('mcp__code-graph__cg_impact')).toBe(false)
  expect(toolLabel('mcp__code-graph__cg_impact')).toBe('cg_impact')
  expect(toolLabel('Bash')).toBe('Bash')
  expect(modelLabel('claude-haiku-5-5')).toBe('haiku 5.5')
  expect(modelLabel('claude-opus-4-8-20260101')).toBe('opus 4.8')
  expect(modelLabel('claude-fable-5')).toBe('fable 5')
  expect(modelLabel('gpt-x')).toBe('gpt-x')
})

test('paths: relative inside the project, last two parts outside', () => {
  const root = 'C:\\Users\\dev\\projects\\shop_be'
  expect(shortenPaths('git -C C:/Users/dev/projects/shop_be log -1 --oneline', root)).toBe('git log -1 --oneline')
  expect(shortenPaths('C:\\Users\\dev\\projects\\shop_be\\src\\A.java', root)).toBe('src/A.java')
  expect(shortenPaths('C:\\Users\\dev\\projects\\claude-token-tools\\does-not-exist.txt', root))
    .toBe('…/claude-token-tools/does-not-exist.txt')
  expect(describeCall('Read', { file_path: 'C:/Users/dev/projects/shop_be/pom.xml' }, root)).toBe('Read pom.xml')
})

/** Every Text drawn, joined: the pane is checked as a reader sees it, whatever box holds which line. */
async function allText(ui: { findAll: (query: { type: 'Text' }) => Promise<{ text?: string }[]> }): Promise<string> {
  return (await ui.findAll({ type: 'Text' })).map(node => node.text ?? '').join('\n')
}

test('cards on every surface: name, status, calls, errors, recent calls, Xem log under each card', async ($, on) => {
  resetProfile()
  engine(on, true)
  let viewed: unknown
  on('ui.view', (_, e) => { viewed = e; return { value: {} } })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'dev <coupon> & co', subagentType: 'developer' })
  await $.tool.call({ tool: 'Edit', file_path: 'PricingServiceImpl.java', old_string: 'a', new_string: 'b' })
  await $.tool.call({ tool: 'Bash', command: 'mvn compile' })
  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const text = await allText(ui)
    expect(text).toContain('dev <coupon> & co')               // native text: no escaping needed
    expect(text).toContain('Phiên chính')
    expect(text).toMatch(/đang chạy · 2 lượt · 1 lỗi · \d+s trước/)
    expect(text).toContain('Bash mvn compile')                // recent calls listed in the card
    expect(text).toContain('Bash mvn compile — lỗi')          // …and the failure in the log
    expect(text).toContain('Cần chú ý')                       // a recent failure is raised to the top
    expect(text).not.toContain('NaN')
    expect((await ui.findAll({ type: 'Svg' })).length).toBe(0)
    await ui.unmount()
  }
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'view-a1' }) // opens the calls on the card, whatever the app can show
  expect(viewed).toBeUndefined()
  expect((await ui.find({ type: 'Button', key: 'view-a1' }))?.text).toBe('Thu gọn')
  await ui.press({ key: 'open-a1' })
  expect(viewed).toEqual({ agentId: 'a1' })
  expect(await allText(ui)).toContain('Đã mở transcript của agent ở khung chat')
  await ui.press({ key: 'view-a1' })
  expect(await ui.find({ type: 'Button', key: 'open-a1' })).toBeUndefined()
  await ui.press({ key: 'view-main' })
  await ui.press({ key: 'open-main' })
  expect(viewed).toEqual({})
  await ui.unmount()
})

test('a subagent that answered shows done, not running, once the app drops it from its list', async ($, on) => {
  resetProfile()
  engine(on)
  on('turn.complete', (_, e) => ({ text: e.answer }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'Tạo file', subagentType: 'tester' })
  await $.turn.complete({ turnId: 't9', answer: 'xong', durationMs: 10, isAborted: false, agentId: 'a1' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await allText(ui)).toMatch(/xong · 0 lượt/)
  expect((await ui.find({ type: 'Text', text: /agent ·/ }))?.text).toContain('0 đang làm')
  await ui.unmount()
})

test("Xem log: the agent's own log (task, calls, results, report) right on the card", async ($, on) => {
  resetProfile()
  engine(on)
  const asked: string[][] = []
  on('session.id', () => ({ value: 's1' }))
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    const lines = [
      { kind: 'task', text: 'Chạy mvn -q compile', error: false },
      { kind: 'call', text: 'PowerShell mvn -q compile', error: false },
      { kind: 'result', text: 'Permission denied: Vai reviewer không được chạy', error: true },
      { kind: 'answer', text: 'Bị chặn, không chạy được.', error: false },
    ]
    const stdout = e.argv.includes('agent-log') ? JSON.stringify({ agent: 'a1', path: 'x', lines }) : AGENT_USAGE
    return { value: { exitCode: 0, stdout, stderr: '',
      isStdoutTruncated: false, isStderrTruncated: false } }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'Chạy mvn', subagentType: 'code-reviewer' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'view-a1' })
  const logCall = asked.find(argv => argv.includes('agent-log')) ?? []
  expect(logCall.slice(-6)).toEqual(['--session', 's1', '--agent', 'a1', '--last', '30'])
  const text = await allText(ui)
  expect(text).toContain('▷ Việc giao: Chạy mvn -q compile')
  expect(text).toContain('→ PowerShell mvn -q compile')
  expect(text).toContain('↳ Permission denied: Vai reviewer không được chạy')
  expect(text).toContain('✔ Trả lời: Bị chặn, không chạy được.')
  expect((await ui.find({ type: 'Text', text: /↳ Permission denied/ }))?.props.color).toBe('error')
  await ui.press({ key: 'reload-a1' })
  expect(asked.filter(argv => argv.includes('agent-log')).length).toBe(2)
  await ui.unmount()
})

test('an agent quiet for 10 minutes and gone from the app is done', async ($, on) => {
  resetProfile()
  let clock = 1_700_000_000_000
  on('clock.now', () => ({ value: clock }))
  on('clock.every', () => ({ value: undefined }))
  on('command.register', () => ({ value: { command: 'control-room' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  let next = 0
  on('agent.spawn', () => ({ model: 'claude-sonnet-5-5', agentId: `a${++next}` }))
  on('agent.list', () => ({ value: [{ id: 'a2', status: 'running' }] }))
  on('tool.call', () => ({ result: {} }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'Agent cũ', subagentType: 'tester' })
  await $.agent.spawn({ prompt: 'x', description: 'Agent đang chạy', subagentType: 'tester' })
  await $.tool.call({ tool: 'Grep', pattern: 'x' })
  clock += 11 * 60_000
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await allText(ui)).toMatch(/Agent cũ[\s\S]*xong · 0 lượt/)
  expect(await allText(ui)).toMatch(/Agent đang chạy[\s\S]*đang chạy/)
  await ui.unmount()
})

test('Xoá log on one card hides its lines so far; new lines show; Hiện lại brings them back', async ($, on) => {
  resetProfile()
  engine(on)
  on('session.id', () => ({ value: 's1' }))
  const old = new Date(1_000).toISOString()
  const lines = [
    { at: old, kind: 'task', text: 'Việc cũ', error: false },
    { at: old, kind: 'call', text: 'Read cu.txt', error: false },
  ]
  on('process.run', (_, e) => ({ value: { exitCode: 0, stdout: e.argv.includes('agent-log')
    ? JSON.stringify({ agent: 'a1', path: 'x', lines }) : AGENT_USAGE, stderr: '', isStdoutTruncated: false,
  isStderrTruncated: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'Agent A', subagentType: 'tester' })
  await $.tool.call({ tool: 'Grep', pattern: 'x' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'view-a1' })
  await ui.press({ key: 'view-main' })
  expect(await allText(ui)).toContain('→ Read cu.txt')
  await ui.press({ key: 'clear-a1' })
  let text = await allText(ui)
  expect(text).toContain('Chưa có dòng mới từ lúc xoá.')
  expect(text).toMatch(/đã xoá lúc/)
  expect(text.split('→ Read cu.txt').length).toBe(2) // the main card's log still shows it, a1's no longer
  lines.push({ at: new Date(Date.now() + 3_600_000).toISOString(), kind: 'call', text: 'Read moi.txt', error: false })
  await ui.press({ key: 'reload-a1' })
  text = await allText(ui)
  expect(text).toContain('→ Read moi.txt')
  await ui.press({ key: 'restore-a1' })
  expect((await allText(ui)).split('→ Read cu.txt').length).toBe(3)
  await ui.unmount()
})

test('Xem log: 12 lines first, Xem thêm for the rest', async ($, on) => {
  resetProfile()
  engine(on)
  on('session.id', () => ({ value: 's1' }))
  const lines = Array.from({ length: 20 }, (_, n) => ({ kind: 'call', text: `Read f${n + 1}.txt`, error: false }))
  on('process.run', (_, e) => ({ value: { exitCode: 0, stdout: e.argv.includes('agent-log')
    ? JSON.stringify({ agent: 'main', path: 'x', lines }) : AGENT_USAGE, stderr: '', isStdoutTruncated: false,
  isStderrTruncated: false } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Grep', pattern: 'x' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'view-main' })
  expect(await allText(ui)).toContain('→ Read f20.txt')
  expect(await allText(ui)).not.toContain('→ Read f8.txt')
  await ui.press({ key: 'more-main' })
  expect(await allText(ui)).toContain('→ Read f1.txt')
  await ui.unmount()
})

test('Xem log: a card shows up to 20 calls when open, and why the app could not switch', async ($, on) => {
  resetProfile()
  engine(on)
  on('ui.view', () => ({ value: { deny: 'agent không còn trong danh sách' } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (let n = 1; n <= 6; n++) await $.tool.call({ tool: 'Read', file_path: `f${n}.txt` })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await allText(ui)).not.toContain('Read f1.txt')
  await ui.press({ key: 'view-main' })
  expect(await allText(ui)).toContain('Read f1.txt')
  await ui.press({ key: 'open-main' })
  expect(await allText(ui)).toContain('App không mở được transcript: agent không còn trong danh sách')
  await ui.unmount()
})

test('layout helpers: meter, tones, attention window, always-ask in words, role table', () => {
  expect(meter(50, 10)).toBe('█████░░░░░')
  expect(meter(140, 4)).toBe('████')
  expect(percentTone(95)).toBe('error')
  expect(percentTone(72)).toBe('warning')
  expect(eventTone('⛔ tester: x')).toBe('error')
  expect(eventTone('❓ dev: y')).toBe('warning')
  expect(eventTone('Đã mở')).toBeUndefined()
  const now = 100 * 60_000
  const log = [
    { at: now - 20 * 60_000, text: '⛔ old' }, { at: now - 60_000, text: '▶ start' }, { at: now - 50_000, text: '❓ a' },
    { at: now - 40_000, text: '✗ b' }, { at: now - 30_000, text: '⛔ c' }, { at: now - 20_000, text: '⛔ d' },
  ]
  expect(attention(log, now).map(event => event.text)).toEqual(['⛔ d', '⛔ c', '✗ b'])
  expect(alwaysAskWords(['\\bgit\\s+(push|commit)\\b', '\\bprod\\b', 'zzz'])).toEqual([
    'git push / commit / reset --hard / rebase / merge', 'lệnh có chữ "prod"', '`zzz`'])
  const row = { id: 'a', name: 'A', type: 'tester', model: '', status: 'running', startedAt: 0, lastAt: 0, calls: 3,
    errors: 1, role: 'tester', blocked: 2 }
  expect(cardMeta(row, 5_000)).toBe('đang chạy · 3 lượt · 1 lỗi · vai tester · 2 bị chặn · 5s trước')
})

test('a card lists at most 3 recent calls, newest first', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (const n of [1, 2, 3, 4, 5]) await $.tool.call({ tool: 'Read', file_path: `f${n}.txt` })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const text = await allText(ui)
  expect(text).not.toContain('Read f2.txt')
  expect(text.indexOf('Read f5.txt')).toBeLessThan(text.indexOf('Read f3.txt'))
  await ui.unmount()
})

test('main session is working during a turn and idle after; app tools are not counted', async ($, on) => {
  engine(on)
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await $.turn.start({ text: 'làm đi', turnId: 't1' })
  await $.tool.call({ tool: 'Grep', pattern: 'SQL' })
  await $.tool.call({ tool: 'mcp__ccd_turn__wrap_up', recap: 'x' })
  let ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await allText(ui)).toMatch(/đang chạy · 1 lượt/)
  await ui.unmount()
  await $.turn.complete({ turnId: 't1', answer: 'xong', durationMs: 10, isAborted: false })
  ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await allText(ui)).toMatch(/rảnh · 1 lượt/)
  expect((await ui.find({ type: 'Text', text: /agent ·/ }))?.text).toContain('0 đang làm')
  await ui.unmount()
})

test('M2 helpers: numbers, limits, parsing', () => {
  expect(mtok(2_671_611)).toBe('2.7M')
  expect(mtok(95_534)).toBe('96k')
  expect(usd(1.9364)).toBe('$1.94')
  expect(usd(48.93)).toBe('$48.9')
  const limits = pickLimits([{ kind: 'five_hour', percentUsed: 38 }, { kind: 'seven_day', percentUsed: 67 },
    { kind: 'spend_limit', percentUsed: 5 }])
  expect(limits.fiveHour?.percent).toBe(38)
  expect(limits.week?.percent).toBe(67)
  const parsed = parseAgentUsage(AGENT_USAGE)
  expect(parsed.agents.a1.tokens).toBe(95_534)
  expect(parsed.budget?.usedTokens).toBe(1_954_806)
  const items = usageItems({ at: 1, ...parsed, ...limits, sessionUsd: 2.1 }, 1)
  expect(items.map(item => item.label)).toEqual(['Hạn mức 5 giờ', 'Hạn mức tuần', 'Ngân sách phiếu', 'Phiên này (ctt)'])
  expect(items[2].value).toBe('2.0M/47.2M')
  expect(items[3].value).toBe('$2.04')
  expect(items[3].sub).toBe('2.8M xử lý · 167k mới · 94% đọc lại cache · Claude Code báo $2.10')
  const old = usageItems({ at: 1, agents: {}, total: { tokens: 0, usd: 0 }, contextPercent: 74 }, 1)
  expect(old[0].label).toBe('Context phiên')
})

test('token split: processed vs new vs cache re-read', () => {
  const spend = { tokens: 1_200_000, usd: 1, input: 3_000, output: 40_000, cache_read: 1_115_000, cache_write: 42_000,
    new: 85_000, cache_read_pct: 92.9 }
  expect(tokenSplit(spend)).toBe('1.2M xử lý · 85k mới · 93% đọc lại cache')
  expect(tokenSplit(spend, true)).toBe('1.2M (85k mới, 93% cache)')
  expect(tokenParts(spend)).toBe('in 3k · out 40k · ghi cache 42k · đọc cache 1.1M')
  expect(tokenSplit({ tokens: 2_000, usd: 0 })).toBe('2k token')
  expect(tokenParts({ tokens: 2_000, usd: 0 })).toBe('')
})

test('M2: refresh fills limits, cost and per-agent cost on the cards', async ($, on) => {
  engine(on)
  usageEngine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'dev-coupon', subagentType: 'developer' })
  await $.tool.call({ tool: 'Grep', pattern: 'x' }) // gives the main session its row
  await refreshUsage($)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const text = await allText(ui)
    for (const piece of ['Hạn mức tuần', '67%', 'Phiên này (ctt)', '$2.04', 'Claude Code báo $2.10',
      '96k (16k mới, 84% cache) · $0.11', '2.7M token · $1.94', 'Token phiên: in 1k · out 40k']) {
      expect(text).toContain(piece)
    }
    expect(text.indexOf('dev-coupon')).toBeLessThan(text.indexOf('Hạn mức tuần')) // agents first, limits after
    expect(text.lastIndexOf('Nhật ký')).toBeGreaterThan(text.indexOf('Hạn mức tuần')) // the log at the bottom
    await ui.unmount()
  }
})

test('M2: a failing ctt shows a line and keeps the limits', async ($, on) => {
  engine(on)
  usageEngine(on, 1)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await refreshUsage($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /Chưa đọc được token/ }))?.text).toContain('boom')
  expect(await allText(ui)).toContain('Hạn mức tuần')
  await ui.unmount()
})

test('/control-room: declared at start, again on the first turn if that failed; the plugin command opens it too', async ($, on) => {
  resetProfile()
  on('clock.now', () => ({ value: 1_700_000_000_000 }))
  on('clock.every', () => ({ value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  let tries = 0
  on('command.register', () => {
    tries += 1
    if (tries === 1) throw new Error('not ready')
    return { value: { command: 'control-room' } }
  })
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  let opened = 0
  on('ui.open', () => { opened += 1; return { value: { isPlaced: true } } })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: 'x', turnId: 't1' })
  await $.turn.start({ text: 'y', turnId: 't2' })
  expect(tries).toBe(2) // failed at start, done on the first turn, not again after
  for (const command of ['control-room', 'token-tools:control-room']) {
    expect((await $.command.run({ command })).text).toBe('Đã mở Phòng điều hành.')
  }
  expect(opened).toBe(2)
})

test('an empty session says what will appear', async ($, on) => {
  engine(on)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /Chưa có hoạt động/ }))?.text).toContain('Agent sẽ hiện')
  await ui.unmount()
})

test('a failing panel never blocks the tool', async ($, on) => {
  engine(on)
  on('state.set', () => {
    throw new Error('store down')
  })
  const ran = await $.tool.call({ tool: 'Bash', command: 'ls' })
  expect(ran.deny).toBeUndefined()
})

test('Bàn giao: from 60% context a button drafts /token-tools:handoff in the prompt box (never sends)', async ($, on) => {
  engine(on)
  usageEngine(on) // context 74%
  const filled: string[] = []
  on('prompt.fill', (_, e) => {
    filled.push(e.text)
    return { isFilled: true }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await refreshUsage($)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /context 74%/ }))?.text).toBe('context 74%')
    await ui.press({ key: 'handoff' })
    await ui.unmount()
  }
  expect(filled).toEqual(['/token-tools:handoff ', '/token-tools:handoff '])
})

test('Bàn giao: no button below 60% context', async ($, on) => {
  engine(on)
  usageEngine(on, 0, 59)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await refreshUsage($)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /^context \d+%$/ })).toBeUndefined()
  await ui.unmount()
})

test('Bàn giao: where no prompt box takes the draft, the log says what to type', async ($, on) => {
  engine(on)
  on('prompt.fill', () => ({ isFilled: false, reason: 'no_composer' }))
  usageEngine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await refreshUsage($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'handoff' })
  expect((await ui.find({ type: 'Text', text: /⇢/ }))?.text).toContain('/token-tools:handoff')
  await ui.unmount()
})

// M3 — a profile as `ctt permissions --json` gives it (trimmed to the roles the tests use).
const PROFILE = {
  common: { bash: ['cd', 'ls', 'grep', 'git status', 'git diff', 'git log'] },
  always_ask: { bash: ['\\bgit\\s+(push|commit)\\b', '\\brm\\s+-[a-z]*r'] },
  roles: {
    tester: { write: ['**/src/test/**'], ask_write: [], bash: ['mvn'], outside: 'deny' },
    developer: { write: ['**/src/main/**'], ask_write: ['**/db/migration/**'], bash: ['mvn', 'npm'], outside: 'ask' },
    reviewer: { write: [], ask_write: [], bash: [], outside: 'deny' },
    docs: { write: ['docs/**', '*.md'], ask_write: [], bash: [], outside: 'deny' },
  },
  agent_role: { 'code-reviewer': 'reviewer', tester: 'tester', developer: 'developer' },
}

test('0.20 judge: MCP tools — read-only runs, risky names ask, the rest follows the role', () => {
  const p = {
    ...PROFILE,
    always_ask: { ...PROFILE.always_ask, mcp: ['(?:^|[_-])(delete|remove|trash)', '(?:^|[_-])(send|publish)'] },
    roles: {
      ...PROFILE.roles,
      reviewer: { ...PROFILE.roles.reviewer, mcp: 'deny', mcp_allow: ['mcp__jira__get_*'] },
      tester: { ...PROFILE.roles.tester, mcp: 'ask' },
    },
  } as unknown as Profile
  const call = (role: string, tool: string, readOnly = false) => judge(p, role, tool, {}, 'C:/repo', readOnly)
  // the default is allow: a profile written before 0.20 judges MCP tools as it did (not at all)
  expect(call('developer', 'mcp__jira__create_issue')).toBeUndefined()
  expect(judge(PROFILE as unknown as Profile, 'reviewer', 'mcp__drive__trash_file', {}, 'C:/repo', false)).toBeUndefined()
  // a risky name asks for every role, on the tool's own name only (the server's name does not count)
  expect(call('developer', 'mcp__drive__trash_file')?.decision).toBe('ask')
  expect(call('developer', 'mcp__mail__send_message')?.reason).toContain('send_message')
  expect(call('developer', 'mcp__send-grid__list_templates')).toBeUndefined()
  expect(call('developer', 'mcp__docs__resend')).toBeUndefined()
  // what the engine marks read-only always runs
  expect(call('reviewer', 'mcp__drive__trash_file', true)).toBeUndefined()
  expect(call('reviewer', 'mcp__jira__search', true)).toBeUndefined()
  // the role's own setting, and its allow list
  expect(call('reviewer', 'mcp__jira__create_issue')).toEqual({ decision: 'deny',
    reason: 'Vai reviewer không được gọi tool MCP không phải chỉ đọc: mcp__jira__create_issue' })
  expect(call('reviewer', 'mcp__jira__get_issue')).toBeUndefined()
  expect(call('tester', 'mcp__jira__create_issue')?.decision).toBe('ask')
  // a role switched off from the pane: only the always-ask names still ask
  const off = { ...p, roles: { ...p.roles, reviewer: { ...p.roles.reviewer, enabled: false } } } as Profile
  expect(judge(off, 'reviewer', 'mcp__jira__create_issue', {}, 'C:/repo', false)).toBeUndefined()
  expect(judge(off, 'reviewer', 'mcp__drive__trash_file', {}, 'C:/repo', false)?.decision).toBe('ask')
})

test('0.20 ship: stalled steps and steps sent up the model ladder are spelled out', () => {
  const ship = { id: 'r1', title: 'T', stage: 3, stage_name: 'Chạy', stages: 7, steps_done: 0, steps: 3, failed: 2,
    running: ['API'], result: null, check: null, stalled: [{ name: 'API', minutes: 14 }],
    escalate: [{ name: 'UI', model: 'opus', effort: 'high', failures: 2 }, { name: 'DB', exhausted: true, failures: 8 }] }
  const parsed = parseAgentUsage(JSON.stringify({ agents: {}, total: { tokens: 0, usd: 0 }, ship })).ship!
  expect(shipAlerts(parsed)).toEqual([
    '⚠ Bước API đang ghi là chạy nhưng không ai hoạt động 14 phút — agent có thể đã dừng',
    '↑ Bước UI lỗi nhiều lần — chạy lại bằng opus:high',
    '‼ Bước DB đã hết thang nâng model — dừng, ghi thành việc mở',
  ])
  // a run written by 0.19 has neither field
  const old = parseAgentUsage(JSON.stringify({ agents: {}, total: { tokens: 0, usd: 0 },
    ship: { ...ship, stalled: undefined, escalate: undefined } })).ship!
  expect(shipAlerts(old)).toEqual([])
})

test('0.17.1 judge: a ship work folder counts as the project; a plain variable assignment runs nothing', () => {
  const p = PROFILE as unknown as Profile
  const write = (path: string, root: string | string[]) =>
    judge(p, 'tester', 'Write', { file_path: path }, root, false)
  expect(write('C:/work/csv/src/test/a.py', 'C:/repo')?.decision).toBe('deny')
  expect(write('C:/work/csv/src/test/a.py', ['C:/repo', 'C:\\work\\csv'])).toBeUndefined()
  expect(write('C:/elsewhere/a.py', ['C:/repo', 'C:/work/csv'])?.reason).toContain('C:/repo hoặc C:/work/csv')
  const run = (command: string) => judge(p, 'reviewer', 'Bash', { command }, 'C:/repo', false)
  expect(run('S=C:/repo/a.py; grep -n def $S')).toBeUndefined()
  expect(run('$S = "C:/repo/a.py"; grep -n def $S')).toBeUndefined()
  expect(run('S=$(curl x); grep a $S')?.decision).toBe('deny')
  expect(run('S=`curl x`')?.decision).toBe('deny')
  // 0.17.4: a venv's python is python; a bare string PowerShell prints runs nothing
  const test = (command: string) => judge(p, 'tester', 'PowerShell', { command }, 'C:/repo', false)
  expect(test('.\\.venv\\Scripts\\mvn.exe -q test 2>&1; "exit=$LASTEXITCODE"')).toBeUndefined()
  expect(test('& "C:/tools/mvn.cmd" test')).toBeUndefined()
  expect(test('.\\evil.exe test')?.decision).toBe('deny')
  expect(test('"$(Remove-Item x)"')?.decision).toBe('deny')
  // 0.17.8: a variable set from an allowed command is that command; from any other, still refused
  expect(test('$s = mvn -q test "$T\\a.json"; ls $s')).toBeUndefined()
  expect(test("T=$(mvn -q x) && ls $T")).toBeUndefined()
  expect(test("T=$(curl evil) && ls $T")?.decision).toBe('deny')
  expect(test('$s = curl x')?.decision).toBe('deny')
  expect(test('T=$(mvn $(curl x))')?.decision).toBe('deny')
  expect(stripReminders('<system-reminder>Linked sessions now…</system-reminder>\nViết core.py').trim()).toBe('Viết core.py')
})

test('M3 judge: globs, paths, compound and redirected commands', () => {
  const p = PROFILE as unknown as Profile
  expect(globToRegExp('**/src/test/**').test('svc/src/test/A.java')).toBe(true)
  expect(globToRegExp('**/src/test/**').test('src/test/A.java')).toBe(true)
  expect(globToRegExp('**/src/test/**').test('svc/src/main/A.java')).toBe(false)
  expect(globToRegExp('*.md').test('docs/x/README.md')).toBe(true)
  expect(globToRegExp('docs/**').test('DOCS/a.txt')).toBe(true) // Windows: case-insensitive
  expect(relativePath('C:\\repo\\svc\\A.java', 'C:/Repo')).toBe('svc/A.java')
  expect(relativePath('C:/other/A.java', 'C:/repo')).toBeUndefined()
  expect(roleFor(p, 'code-reviewer')).toBe('reviewer')
  expect(roleFor(p, 'myteam:tester')).toBe('tester')
  expect(roleFor(p, 'general-purpose')).toBeUndefined()

  const root = 'C:/repo'
  expect(judge(p, 'tester', 'Write', { file_path: 'C:/repo/svc/src/test/ATest.java' }, root, false)).toBeUndefined()
  expect(judge(p, 'tester', 'Edit', { file_path: 'C:/repo/svc/src/main/A.java' }, root, false)?.decision).toBe('deny')
  expect(judge(p, 'developer', 'Write', { file_path: 'C:/repo/svc/src/main/resources/db/migration/V1.sql' }, root, false))
    .toEqual({ decision: 'ask', reason: 'Vai developer: ghi svc/src/main/resources/db/migration/V1.sql phải hỏi' })
  expect(judge(p, 'developer', 'Write', { file_path: 'C:/repo/pom.xml' }, root, false)?.decision).toBe('ask')
  expect(judge(p, 'reviewer', 'Read', { file_path: 'C:/repo/pom.xml' }, root, true)).toBeUndefined()

  expect(judge(p, 'tester', 'Bash', { command: 'cd svc && mvn -q test 2>&1 | grep FAIL' }, root, false)).toBeUndefined()
  expect(judge(p, 'tester', 'Bash', { command: 'mvn test && curl http://x' }, root, false)?.reason).toContain('curl')
  expect(judge(p, 'tester', 'Bash', { command: 'grep "a|b;c" x.txt' }, root, false)).toBeUndefined() // quoted
  expect(judge(p, 'tester', 'Bash', { command: 'ls > out.txt' }, root, false)?.reason).toContain('ghi file')
  expect(judge(p, 'tester', 'Bash', { command: 'mvn test > /dev/null' }, root, false)).toBeUndefined()
  expect(judge(p, 'reviewer', 'Bash', { command: 'git push origin main' }, root, true)?.decision).toBe('ask')
  expect(judge(p, 'reviewer', 'Bash', { command: 'git log --oneline' }, root, false)).toBeUndefined()
  expect(judge(p, 'reviewer', 'mcp__jira__create', {}, root, false)).toBeUndefined()
  const off = { ...p, roles: { ...p.roles, tester: { ...p.roles.tester, enabled: false } } }
  expect(judge(off, 'tester', 'Edit', { file_path: 'C:/repo/svc/src/main/A.java' }, root, false)).toBeUndefined()
  expect(judge(off, 'tester', 'Bash', { command: 'curl http://x' }, root, false)).toBeUndefined()
  expect(judge(off, 'tester', 'Bash', { command: 'git push' }, root, false)?.decision).toBe('ask') // still asks
  expect(splitCommand('A=1 mvn test; npm run x || echo "x && y"').map(s => s.text))
    .toEqual(['mvn test', 'npm run x', 'echo "x && y"'])
})

function profileEngine(on: Parameters<Parameters<typeof test>[1]>[1], type: string, ok = true) {
  on('process.run', (_, e) => ({
    value: ok
      ? { exitCode: 0, stdout: e.argv.includes('permissions') ? JSON.stringify(PROFILE) : AGENT_USAGE, stderr: '',
          isStdoutTruncated: false, isStderrTruncated: false }
      : { exitCode: 1, stdout: '', stderr: 'no python', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  return { prompt: 'x', description: type, subagentType: type }
}

test('M3: a tester writing outside src/test is refused before the tool runs, with the reason', async ($, on) => {
  resetProfile()
  let ran = 0
  engine(on, false, () => { ran += 1 })
  const spawn = profileEngine(on, 'tester')
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn(spawn)
  const denied = await $.tool.call({ tool: 'Write', file_path: 'C:/repo/svc/src/main/A.java', content: 'x', agentId: 'a1' })
  expect(JSON.stringify(denied)).toContain('Vai tester chỉ ghi **/src/test/**')
  expect(ran).toBe(0)
  await $.tool.call({ tool: 'Write', file_path: 'C:/repo/svc/src/test/ATest.java', content: 'x', agentId: 'a1' })
  expect(ran).toBe(1)
  await $.tool.call({ tool: 'Write', file_path: 'C:/repo/svc/src/main/A.java', content: 'x' }) // main loop: free
  expect(ran).toBe(2)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /vai tester/ }))?.text).toContain('1 bị chặn')
  expect((await ui.find({ type: 'Text', text: /vai tester/ }))?.text).not.toContain('lỗi') // counted once, as blocked
  expect(await ui.find({ type: 'Text', text: /✗ tester/ })).toBeUndefined() // logged once, as ⛔
  expect((await ui.find({ type: 'Text', text: /⛔/ }))?.text).toContain('src/main/A.java')
  await ui.press({ key: 'roles' })
  expect(await allText(ui)).toContain('ghi **/src/test/** · chạy mvn · ngoài quyền chặn') // closed: one line
  expect(await ui.find({ type: 'Select', key: 'outside-tester' })).toBeUndefined()
  await ui.press({ key: 'edit-tester' })
  expect((await ui.find({ type: 'Button', key: 'rm-tester-write-0' }))?.text).toBe('✕ **/src/test/**')
  expect((await ui.find({ type: 'Select', key: 'outside-tester' }))?.props.value).toBe('deny')
  expect(await ui.find({ type: 'Button', key: 'edit-developer' })).toBeUndefined() // only roles running now…
  await ui.press({ key: 'roles-all' })
  expect(await ui.find({ type: 'Button', key: 'edit-developer' })).toBeDefined() // …until "Xem tất cả vai"
  expect(await allText(ui)).toContain('git push / commit / reset --hard / rebase / merge')
  expect((await ui.find({ type: 'Text', text: /Cần chú ý/ }))?.text).toBe('Cần chú ý')
  expect((await ui.find({ type: 'Text', text: /Lần chặn gần nhất/ }))?.text).toContain('⛔')
  await ui.unmount()
})

test('0.23: in Auto mode, [auto_mode] ask = "deny" turns what would ask into a refusal; elsewhere nothing changes', async ($, on) => {
  const ask = { decision: 'ask', reason: 'Lệnh luôn phải hỏi' } as const
  const deny = { decision: 'deny', reason: 'x' } as const
  const strict = { ...PROFILE, auto_mode: { ask: 'deny' } } as unknown as Profile
  expect(inAutoMode(ask, 'auto', strict)?.decision).toBe('deny')
  expect(inAutoMode(ask, 'auto', strict)?.reason).toContain('Lệnh luôn phải hỏi — phiên đang ở chế độ Auto')
  expect(inAutoMode(ask, 'default', strict)).toBe(ask)               // another mode
  expect(inAutoMode(ask, undefined, strict)).toBe(ask)               // mode not known
  expect(inAutoMode(ask, 'auto', PROFILE as unknown as Profile)).toBe(ask)   // a profile from before 0.23
  expect(inAutoMode(ask, 'auto', { ...strict, auto_mode: { ask: 'ask' } })).toBe(ask)
  expect(inAutoMode(deny, 'auto', strict)).toBe(deny)
  expect(inAutoMode(undefined, 'auto', strict)).toBeUndefined()

  resetProfile()
  let ran = 0
  engine(on, false, () => { ran += 1 })
  on('process.run', (_, e) => ({ value: { exitCode: 0, stdout: e.argv.includes('permissions') ? JSON.stringify(strict) : AGENT_USAGE,
    stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'developer', subagentType: 'developer', permissionMode: 'auto' })
  const refused = await $.tool.call({ tool: 'Bash', command: 'git push origin main', agentId: 'a1' })
  expect(JSON.stringify(refused)).toContain('chế độ Auto')
  expect(ran).toBe(0)
  const migration = await $.tool.call({ tool: 'Write', file_path: 'C:/repo/svc/src/main/resources/db/migration/V9.sql', content: 'x',
    agentId: 'a1' })
  expect(JSON.stringify(migration)).toContain('chế độ Auto')
  expect(ran).toBe(0)
  await $.tool.call({ tool: 'Bash', command: 'mvn -q compile', agentId: 'a1' }) // what the role allows still runs
  expect(ran).toBe(1)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /vai developer/ }))?.text).toContain('2 bị chặn')
  await ui.unmount()
})

test('M3: a developer writing a migration, or pushing, is put to a person', async ($, on) => {
  resetProfile()
  engine(on)
  const spawn = profileEngine(on, 'developer')
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn(spawn)
  await $.tool.call({ tool: 'Write', file_path: 'C:/repo/svc/src/main/resources/db/migration/V9.sql', content: 'x', agentId: 'a1' })
  await $.tool.call({ tool: 'Bash', command: 'git push origin main', agentId: 'a1' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const asked = await ui.findAll({ type: 'Text', text: /❓/ })
  expect(asked.map(node => node.text).join('\n')).toContain('db/migration/V9.sql phải hỏi')
  expect(asked.map(node => node.text).join('\n')).toContain('git push')
  expect((await ui.find({ type: 'Text', text: /vai developer/ }))?.text).toContain('2 phải hỏi')
  await ui.unmount()
})

test('M3: the project folder comes from the session, Windows paths included', async ($, on) => {
  resetProfile()
  let ran = 0
  engine(on, false, () => { ran += 1 })
  const spawn = profileEngine(on, 'tester')
  on('session.cwd', () => ({ value: 'C:\\Users\\dev\\projects\\shop_be' }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true }) // the copy found wrong in the app
  await $.agent.spawn(spawn)
  const base = 'C:\\Users\\dev\\projects\\shop_be\\shop-auth\\src'
  await $.tool.call({ tool: 'Write', file_path: `${base}\\test\\java\\ATest.java`, content: 'x', agentId: 'a1' })
  expect(ran).toBe(1)
  const denied = await $.tool.call({ tool: 'Write', file_path: `${base}\\main\\java\\tmp\\M3Probe.java`, content: 'x',
    agentId: 'a1' })
  expect(ran).toBe(1)
  expect(JSON.stringify(denied)).toContain('không ghi shop-auth/src/main/java/tmp/M3Probe.java')
})

/** A stand-in for `ctt permissions` / `permissions-set`: applies each change to a copy of PROFILE, as the CLI would. */
function editableProfile(on: Parameters<Parameters<typeof test>[1]>[1], calls: string[][]) {
  const profile = JSON.parse(JSON.stringify(PROFILE)) as Profile
  on('process.run', (_, e) => {
    const argv = [...e.argv]
    calls.push(argv)
    const at = (flag: string) => argv.indexOf(flag)
    let stdout = AGENT_USAGE
    if (argv.includes('permissions-set')) {
      const role = profile.roles[argv[at('--role') + 1]]
      if (!role) stdout = JSON.stringify({ error: 'không có vai' })
      else {
        if (at('--outside') > 0) role.outside = argv[at('--outside') + 1] as 'deny' | 'ask'
        if (at('--enabled') > 0) role.enabled = argv[at('--enabled') + 1] === 'true'
        if (at('--add') > 0) role[argv[at('--add') + 1] as 'write'].push(argv[at('--add') + 2])
        if (at('--remove') > 0) {
          const list = argv[at('--remove') + 1] as 'write'
          role[list] = role[list].filter(item => item !== argv[at('--remove') + 2])
        }
        stdout = JSON.stringify(profile)
      }
    } else if (argv.includes('permissions')) stdout = JSON.stringify(profile)
    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
}

test('M3 on the pane: change what happens outside, add a path, switch a role off — the next call follows', async ($, on) => {
  resetProfile()
  let ran = 0
  engine(on, false, () => { ran += 1 })
  const calls: string[][] = []
  editableProfile(on, calls)
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'tester', subagentType: 'tester' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'roles' })
  await ui.press({ key: 'edit-tester' })

  await ui.input({ key: 'add-tester-write', text: '**/testdata/**' })
  expect(calls.at(-1)?.slice(-5)).toEqual(['--role', 'tester', '--add', 'write', '**/testdata/**'])
  expect((await ui.find({ type: 'Button', key: 'rm-tester-write-1' }))?.text).toBe('✕ **/testdata/**')
  await $.tool.call({ tool: 'Write', file_path: 'C:/repo/svc/testdata/a.json', content: 'x', agentId: 'a1' })
  expect(ran).toBe(1) // the new path is allowed from the next call on

  await ui.select({ key: 'outside-tester', value: 'ask' })
  await $.tool.call({ tool: 'Write', file_path: 'C:/repo/pom.xml', content: 'x', agentId: 'a1' })
  expect(await allText(ui)).toMatch(/❓ tester: Vai tester chỉ ghi/)

  await ui.press({ key: 'toggle-tester' })
  expect(await allText(ui)).toContain('đã tắt giới hạn (lệnh nguy hiểm vẫn hỏi)')
  expect(await allText(ui)).toContain('vai tester (đã tắt giới hạn)')
  const before = ran
  await $.tool.call({ tool: 'Write', file_path: 'C:/repo/svc/src/main/A.java', content: 'x', agentId: 'a1' })
  expect(ran).toBe(before + 1)
  expect(await allText(ui)).toContain('⚙ Quyền: tester: tạm tắt giới hạn')
  await ui.unmount()
})

test('M3 on the pane: a change the CLI refuses shows under the role', async ($, on) => {
  resetProfile()
  engine(on)
  on('process.run', (_, e) => ({ value: { exitCode: 0, stdout: e.argv.includes('permissions-set')
    ? JSON.stringify({ error: 'giá trị trống' }) : e.argv.includes('permissions') ? JSON.stringify(PROFILE) : AGENT_USAGE,
  stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
  await $.session.start({ cwd: 'C:/repo', surface: 'terminal', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'tester', subagentType: 'tester' })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'roles' })
  await ui.press({ key: 'edit-tester' })
  await ui.input({ key: 'add-tester-bash', text: '   ' })
  expect(await allText(ui)).toContain('Chưa lưu được: giá trị trống')
  await ui.unmount()
})

// 0.12.0 — delegation nudges.
const TABLE = {
  main_model: 'claude-fable-5-2', contexts: [100_000, 200_000, 400_000, 600_000], sonnet: [3, 2, 2, 2],
  same: [7, 6, 4, 3], source: 'measured', samples: 15, nudge: true, hint_model: false,
}

function delegationEngine(on: Parameters<Parameters<typeof test>[1]>[1], context: { percent: number },
  table: unknown = TABLE) {
  on('session.id', () => ({ value: 's1' }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1_000_000, percent: context.percent },
    cost: { usd: 1 }, changed: ['cost'], rateLimits: [] } }))
  on('process.run', (_, e) => ({ value: { exitCode: 0, stdout: e.argv.includes('delegation') ? JSON.stringify(table)
    : AGENT_USAGE, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
}

test('delegation helpers: the row of the context, the table of the agent model, a steady prompt rule', () => {
  const t = TABLE as unknown as Thresholds
  expect(minCalls(t, 50_000)).toBe(3)            // below every row: the first
  expect(minCalls(t, 250_000)).toBe(2)
  expect(minCalls(t, 105_000, 'claude-fable-5-2')).toBe(7)
  expect(minCalls(t, 700_000, 'claude-opus-5-5')).toBe(3)
  expect(minCalls({ ...t, sonnet: [null, 2, 2, 2] }, 100_000)).toBeNull()
  expect(nudgeText(6, 300_000, 2)).toBe('💡 Phiên chính đã tự làm 6 lượt ở context 300k — phần còn lại giao cho agent ' +
    'Sonnet sẽ rẻ hơn (giao có lời từ 2 lượt).')
  expect(hintSection(t)).toContain('context 100k: agent Sonnet từ 3 lượt, agent fable 5.2 từ 7 lượt')
  expect(hintSection(t)).toBe(hintSection(t))
})

test('nudge: the main thread past the break-even at its context, once per turn, gone when the turn ends', async ($, on) => {
  resetProfile()
  engine(on)
  delegationEngine(on, { percent: 30 }) // 300k → Sonnet pays off from 2 calls
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.turn.start({ text: 'làm đi', turnId: 't1' })
  await $.tool.call({ tool: 'Read', file_path: 'a.txt' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await allText(ui)).not.toContain('💡')
  await $.tool.call({ tool: 'Read', file_path: 'b.txt' })
  await $.tool.call({ tool: 'Read', file_path: 'c.txt' })
  const text = await allText(ui)
  expect(text).toMatch(/Cần chú ý[\s\S]*💡 Phiên chính đã tự làm 2 lượt ở context 300k/)
  expect(text.split('💡').length).toBe(3) // once in "Cần chú ý", once in the log — not again on the 3rd call
  await $.turn.complete({ turnId: 't1', answer: 'xong', durationMs: 10, isAborted: false })
  expect(await allText(ui)).not.toMatch(/Cần chú ý/)
  await ui.unmount()
})

test('nudge: none on a small context under the break-even, nor without a table', async ($, on) => {
  resetProfile()
  engine(on)
  delegationEngine(on, { percent: 10 }, { error: 'x' }) // a broken table
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (const n of [1, 2, 3, 4]) await $.tool.call({ tool: 'Read', file_path: `f${n}.txt` })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await allText(ui)).not.toContain('💡')
  await ui.unmount()
})

test('↯ an agent that finished under the break-even is marked "tự làm rẻ hơn"; a long one is not', async ($, on) => {
  resetProfile()
  engine(on)
  delegationEngine(on, { percent: 10.5 }) // 105k when the agents start
  on('turn.complete', (_, e) => ({ text: e.answer }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'Tạo file', subagentType: 'tester' })
  await $.tool.call({ tool: 'Write', file_path: 'a.txt', content: 'x', agentId: 'a1' })
  await $.turn.complete({ turnId: 'a', answer: 'xong', durationMs: 1, isAborted: false, agentId: 'a1' })
  await $.agent.spawn({ prompt: 'x', description: 'Việc dài', subagentType: 'developer' })
  for (let n = 0; n < 8; n++) await $.tool.call({ tool: 'Read', file_path: `f${n}.txt`, agentId: 'a2' })
  await $.turn.complete({ turnId: 'b', answer: 'xong', durationMs: 1, isAborted: false, agentId: 'a2' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const text = await allText(ui)
  expect(text).toContain('↯ Tạo file: xong sau 2 lượt — tự làm rẻ hơn (context chính 105k, giao có lời từ ≥ 3 lượt)')
  expect(text).toMatch(/Tạo file[\s\S]*tự làm rẻ hơn ·/)
  expect(text).not.toContain('↯ Việc dài')
  await ui.unmount()
})

test('hint_model: the rule joins the system prompt only when switched on', async ($, on) => {
  resetProfile()
  engine(on)
  delegationEngine(on, { percent: 10 }, { ...TABLE, hint_model: true })
  on('prompt.compose', () => ({ sections: [{ id: 'base', text: 'x', scope: 'shared' }] }))
  const composed = await $.prompt.compose({ model: 'claude-fable-5-2', promptModel: 'claude-fable-5-2', surfaces: ['desktop'],
    tools: [], outputStyle: null, traits: [] } as never)
  expect(composed.sections.map(section => section.id)).toEqual(['base', 'token-tools:delegation'])
})

// M4 — budgets per agent.
function budgetEngine(on: Parameters<Parameters<typeof test>[1]>[1], state: { used: number },
  options: { steps?: { role: string; share: number }[]; caps?: Record<string, number>; enforce?: boolean } = {}) {
  const profile = { ...PROFILE, budget: { roles: options.caps ?? {}, enforce_model: options.enforce ?? false,
    raise_share: 0.5 }, role_models: { tester: 'sonnet', reviewer: 'opus' } }
  on('session.id', () => ({ value: 's1' }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1_000_000, percent: 10 }, cost: { usd: 1 },
    changed: ['cost'], rateLimits: [] } }))
  on('process.run', (_, e) => {
    let stdout = JSON.stringify({ error: 'x' })
    if (e.argv.includes('permissions')) stdout = JSON.stringify(profile)
    else if (e.argv.includes('agent-usage')) {
      stdout = JSON.stringify({ agents: { a1: { tokens: state.used, usd: 1 } }, total: { tokens: state.used, usd: 1 },
        budget: options.steps ? { id: 'est-1', title: 'T', budget_tokens: 10_000_000, used_tokens: 0, p50_tokens: 1,
          p90_tokens: 2, steps: options.steps } : null })
    }
    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
}

test('M4 helpers: the role share of the estimate split between peers, else the cap; levels and lines', () => {
  const steps = [{ role: 'developer', share: 0.55 }, { role: 'tester', share: 0.15 }, { role: 'tester', share: 0.15 }]
  expect(agentBudget('tester', 1, steps, 10_000_000, {})).toBe(3_000_000)
  expect(agentBudget('developer', 2, steps, 10_000_000, {})).toBe(2_750_000)
  expect(agentBudget('docs', 1, steps, 10_000_000, { docs: 500_000 })).toBe(500_000) // no step → the cap
  expect(agentBudget('docs', 1, undefined, undefined, {})).toBeUndefined()
  expect(agentBudget(undefined, 1, steps, 10_000_000, {})).toBeUndefined()
  expect([level(1, 10), level(5, 10), level(8, 10), level(10, 10)]).toEqual([0, 50, 80, 100])
  expect(levelText('tester', 80, 2_500_000, 3_000_000)).toBe('⚠ tester: qua 80% ngân sách (2.5M / 3.0M (83%))')
  expect(modelDiffers('sonnet', 'claude-opus-5-5')).toBe(true)
  expect(modelDiffers('sonnet', 'claude-sonnet-5-5')).toBe(false)
  expect(modelDiffers('inherit', 'claude-opus-5-5')).toBe(false)
})

test('M4: 50 / 80 % logged once, past 100 % each call asks; Tăng ngân sách lets it run again', async ($, on) => {
  resetProfile()
  let ran = 0
  engine(on, false, () => { ran += 1 })
  const state = { used: 1_600_000 }
  budgetEngine(on, state, { steps: [{ role: 'tester', share: 0.3 }] }) // tester: 3.0M
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'tester', subagentType: 'tester' })
  const read = { tool: 'Grep', pattern: 'x', agentId: 'a1' } as const
  await $.tool.call(read)
  await $.tool.call(read)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  let text = await allText(ui)
  expect(text.split('◔ tester: qua 50% ngân sách').length).toBe(2) // once, not per call
  expect(text).toContain('ngân sách 1.6M / 3.0M (53%)')
  state.used = 2_500_000
  await $.tool.call(read)
  expect(await allText(ui)).toMatch(/Cần chú ý[\s\S]*⚠ tester: qua 80% ngân sách/)
  state.used = 3_100_000
  await $.tool.call(read)
  text = await allText(ui)
  expect(text).toContain('‼ tester: hết ngân sách — mỗi lượt gọi tool sẽ hỏi bạn (3.1M / 3.0M (103%))')
  await ui.press({ key: 'raise-a1' })
  expect(await allText(ui)).toContain('＋ tester: tăng ngân sách thêm 1.5M')
  const before = ran
  await $.tool.call(read)
  expect(ran).toBe(before + 1)
  expect(await allText(ui)).toContain('ngân sách 3.1M / 4.5M (69%)')
  await ui.unmount()
})

test('M4: Tạm dừng refuses the next calls but never the hand-back; Tiếp tục lets it go on', async ($, on) => {
  resetProfile()
  let ran = 0
  engine(on, false, () => { ran += 1 })
  budgetEngine(on, { used: 0 })
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'Explore x', subagentType: 'Explore' }) // no role: still pausable
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'pause-a1' })
  const denied = await $.tool.call({ tool: 'Grep', pattern: 'x', agentId: 'a1' })
  expect(ran).toBe(0)
  expect(JSON.stringify(denied)).toContain('đã bị tạm dừng trên Phòng điều hành')
  await $.tool.call({ tool: 'SubagentHandback', message: 'dừng rồi', agentId: 'a1' })
  expect(ran).toBe(1)
  expect(await allText(ui)).toContain('⏸ Explore x: tạm dừng')
  expect(await allText(ui)).not.toMatch(/Explore x[\s\S]*1 lỗi/) // a held call is not an error of the agent
  await ui.press({ key: 'pause-a1' })
  await $.tool.call({ tool: 'Grep', pattern: 'x', agentId: 'a1' })
  expect(ran).toBe(2)
  await ui.unmount()
})

test('M4: an agent past its budget is logged on the next refresh, even with no further call', async ($, on) => {
  resetProfile()
  engine(on)
  const state = { used: 0 }
  budgetEngine(on, state, { caps: { tester: 150_000 } })
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'tester', subagentType: 'tester' })
  await $.tool.call({ tool: 'Grep', pattern: 'x', agentId: 'a1' }) // loads the profile; 0 % so far
  state.used = 171_000
  await refreshUsage($)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await allText(ui)).toContain('‼ tester: hết ngân sách')
  await ui.unmount()
})

test('M4: without an estimate the role cap applies; with neither, no budget at all', async ($, on) => {
  resetProfile()
  engine(on)
  budgetEngine(on, { used: 1_200_000 }, { caps: { tester: 1_000_000 } })
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'tester', subagentType: 'tester' })
  await $.agent.spawn({ prompt: 'x', description: 'dev', subagentType: 'developer' })
  await $.tool.call({ tool: 'Grep', pattern: 'x', agentId: 'a1' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const text = await allText(ui)
  expect(text).toContain('‼ tester: hết ngân sách')
  expect(await ui.find({ type: 'Button', key: 'raise-a2' })).toBeUndefined() // developer: no cap, no estimate
  await ui.unmount()
})

test('M4: an agent on another model than its role is logged; enforce_model starts it on the role model', async ($, on) => {
  resetProfile()
  engine(on)
  budgetEngine(on, { used: 0 })
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'review', subagentType: 'code-reviewer' }) // gets sonnet, role opus
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await allText(ui)).toContain('↪ review chạy sonnet 5.5, chính sách vai reviewer là opus')
  await ui.unmount()
})

test('M4: enforce_model rewrites the model the agent starts on', async ($, on) => {
  resetProfile()
  engine(on)
  budgetEngine(on, { used: 0 }, { enforce: true })
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  const spawned = await $.agent.spawn({ prompt: 'x', description: 'review', subagentType: 'code-reviewer',
    model: 'claude-sonnet-5-5' })
  expect(spawned.model).toBe('opus')
})

// M5 — sessions.
test('M5 helpers: project name, live list, stale keys, the line and the drafted question', () => {
  expect(projectName('C:\\Users\\dev\\projects\\shop_be\\')).toBe('shop_be')
  const now = 100 * 60_000
  const entry = (id: string, ago: number) => ({ id, project: id, status: 'idle' as const, agentsRunning: 0, problems: 0,
    at: now - ago })
  expect(liveSessions([entry('a', 60_000), entry('b', 1_000), entry('c', 11 * 60_000), entry('me', 0)], now, 'me')
    .map(s => s.id)).toEqual(['b', 'a'])
  expect(staleKeys([['session:x', { at: now - 25 * 3_600_000 }], ['session:y', { at: now }],
    ['cc-cost:z', { at: new Date(now - 30 * 3_600_000).toISOString() }], ['other', {}]], now)).toEqual(['session:x', 'cc-cost:z'])
  expect(summaryLine({ ...entry('a', 30_000), status: 'working', agentsRunning: 2, contextPercent: 41.6, usd: 3.456,
    problems: 1 }, now)).toBe('đang làm · 2 agent chạy · context 42% · $3.46 · 1 lỗi/bị chặn · 30s trước')
  const question = draftQuestion({ project: 'shop_be', agent: 'tester', type: 'tester', doing: 'Write a.java',
    problem: '⛔ tester: Vai tester chỉ ghi …', recent: ['✓ Read a', '✗ Bash mvn'], sessionId: 'abcdef123456' })
  expect(question).toContain('Hỏi từ phiên shop_be (abcdef12): agent "tester" (tester) đang gặp vấn đề.')
  expect(question).toContain('- ✗ Bash mvn')
})

test('M5: this session writes its summary to the shared store; the pane lists the live ones', async ($, on) => {
  resetProfile()
  engine(on)
  on('session.id', () => ({ value: 'me-1' }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  await $.session.start({ cwd: 'C:\\work\\claude-token-tools', surface: 'desktop', isInteractive: true })
  const store = stores[stores.length - 1]
  store.set('session:other-1', { id: 'other-1', project: 'shop_be', status: 'working', agentsRunning: 1, problems: 2,
    lastPrompt: 'PROJ-123', lastAlert: '⛔ tester: …', at: Date.now() - 60_000 })
  store.set('session:old-1', { id: 'old-1', project: 'cũ', status: 'idle', agentsRunning: 0, problems: 0,
    at: Date.now() - 3_600_000 })
  await $.turn.start({ text: 'Làm M5   cho khung', turnId: 't1' })
  await $.turn.complete({ turnId: 't1', answer: 'xong', durationMs: 1, isAborted: false })
  expect(store.get('session:me-1')).toMatchObject({ id: 'me-1', project: 'claude-token-tools', status: 'idle',
    lastPrompt: 'Làm M5 cho khung' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Button', key: 'tab-all' }))?.text).toBe('Tất cả phiên (2)')
  await ui.press({ key: 'tab-all' })
  const text = await allText(ui)
  expect(text).toContain('claude-token-tools (phiên này)')
  expect(text).toMatch(/shop_be[\s\S]*PROJ-123/)
  expect(text).toContain('⛔ tester: …')
  expect(text).not.toContain('cũ')
  await ui.unmount()
})

test('M5: Hỏi phiên khác drafts the question, sends only on Gửi, to the chosen session; a refusal is shown', async ($, on) => {
  resetProfile()
  engine(on, true)
  on('session.id', () => ({ value: 'me-1' }))
  const sent: unknown[] = []
  let refuse = true
  on('session.send', (_, e) => {
    sent.push(e)
    return refuse ? { isDelivered: false, reason: 'phiên không chạy' } : { isDelivered: true }
  })
  await $.session.start({ cwd: 'C:\\work\\demo', surface: 'desktop', isInteractive: true })
  stores[stores.length - 1].set('session:other-1', { id: 'other-1', project: 'shop_be', status: 'idle',
    agentsRunning: 0, problems: 0, at: Date.now() })
  await $.agent.spawn({ prompt: 'x', description: 'dev', subagentType: 'developer' })
  await $.tool.call({ tool: 'Bash', command: 'mvn compile', agentId: 'a1' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'ask-a1' })
  expect(sent).toEqual([]) // drafted, not sent
  const draft = String((await ui.find({ type: 'Input', key: 'ask-text' }))?.props.value)
  expect(draft).toContain('agent "dev" (developer)')
  expect(draft).toContain('Bash mvn compile')
  expect((await ui.find({ type: 'Select', key: 'ask-to' }))?.props.value).toBe('s:other-1')
  await ui.press({ key: 'ask-send' })
  expect(await allText(ui)).toContain('Chưa gửi được: phiên không chạy')
  refuse = false
  await ui.input({ key: 'ask-text', text: 'Câu hỏi đã sửa' })
  const last = sent.at(-1) as { to: string; text: string }
  expect(last.to).toBe('other-1') // { sessionId } as the engine spells it
  expect(last.text).toMatch(/^\[q:[a-z0-9]{4} từ:me-1\] Câu hỏi đã sửa\n\(Trả lời: mở đầu bằng \[re:/)
  expect(await ui.find({ type: 'Input', key: 'ask-text' })).toBeUndefined()
  const text = await allText(ui)
  expect(text).toMatch(/✉ Đã gửi câu hỏi \[q:[a-z0-9]{4}\] tới phiên shop_be/)
  expect(text).toMatch(/Câu hỏi đã gửi · 1 chờ trả lời[\s\S]*⏳ \[q:[a-z0-9]{4}\] tới phiên shop_be/)
  await ui.unmount()
})

test('0.16 helpers: parent mark, caps, tree, branch $, mode clash, question marks, reply address', () => {
  expect(readParent(`Làm X\n\n${parentMark('p-1', 2)}`)).toEqual({ parent: 'p-1', depth: 2 })
  expect(readParent('không có dấu')).toBeUndefined()
  const limits = { max_depth: 2, max_children: 2 }
  expect(spawnVerdict(0, 1, limits)).toBeUndefined()
  expect(spawnVerdict(2, 0, limits)).toContain('tầng 2')
  expect(spawnVerdict(1, 2, limits)).toContain('2 phiên con')
  const s = (id: string, parent?: string, usd = 1, mode?: string) => ({ id, project: id, status: 'idle' as const,
    agentsRunning: 0, problems: 0, at: 0, parent, usd, mode })
  const live = [s('c1', 'root'), s('root', undefined, 2, 'auto'), s('g1', 'c1', 1, 'default'), s('c2', 'root'), s('lone', 'gone')]
  expect(sessionTree(live).map(({ entry, level }) => `${level}${entry.id}`)).toEqual(['0root', '1c1', '2g1', '1c2', '0lone'])
  expect(treeUsd(live, 'root')).toBe(5)
  expect(modeClash(live[2], live)).toBeUndefined() // the parent's mode is unknown
  expect(modeClash({ ...live[0], mode: 'default' }, live)).toContain('default ≠ auto')
  const asked = withQuestion('Sao lỗi?', 'ab12', address('s-1', 'a-9'))
  expect(readMark(asked)).toEqual({ kind: 'q', code: 'ab12', from: 's-1/a-9' })
  expect(withQuestion(asked, 'zz99', 's-1')).toBe(asked)
  expect(readMark('[re:ab12] Do thiếu quyền')).toEqual({ kind: 're', code: 'ab12', from: undefined })
  expect(stripMarks(asked)).toBe('Sao lỗi?')
  expect(replyTarget('s-2/a-1', 's-1')).toEqual({ sessionId: 's-2' })
  expect(replyTarget('s-1/a-1', 's-1')).toEqual({ agentId: 'a-1' })
  expect(replyTarget('s-1', 's-1')).toBeUndefined()
  expect(newCode(123, 0.5)).toMatch(/^[a-z0-9]{4}$/)
  const b = briefKey('Viết   jsondiff/core.py\n đọc và so sánh JSON [tt-parent:x tầng:1]')
  expect(b).toBe('Viết jsondiff/core.py đọc và so sánh JSON')
  const now = 10 * 60_000
  const entries: [string, unknown][] = [['pending-child:a', { parent: 'p', depth: 1, brief: b, at: now - 1000 }],
    ['pending-child:old', { parent: 'q', depth: 1, brief: b, at: now - 31 * 60_000 }], ['session:x', {}]]
  expect(matchPending(entries, `Mở đầu
${b} và thêm`, now)).toBe('pending-child:a')
  expect(matchPending(entries, 'việc khác hẳn', now)).toBeUndefined()
})

test('0.17.6: a session opened from here is linked to its parent without touching the brief; past the cap it is refused', async ($, on) => {
  resetProfile()
  const calls: Record<string, unknown>[] = []
  engine(on, false, e => { calls.push(e) })
  let id = 'p-1'
  on('session.id', () => ({ value: id }))
  on('session.turns', () => ({ value: 1 }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  await $.session.start({ cwd: 'C:\work\demo', surface: 'desktop', isInteractive: true })
  const store = stores[stores.length - 1]
  const brief = 'Việc A: viết jsondiff/core.py đọc và so sánh hai file JSON, kèm test'
  await $.tool.call({ tool: 'mcp__ccd_session__start_session', title: 'Làm A', prompt: brief, initiation: 'user_asked' } as never)
  expect(calls.at(-1)?.prompt).toBe(brief) // auto mode reviews exactly what runs
  expect([...store.keys()].filter(key => key.startsWith('pending-child:')).length).toBe(1)
  // the child: the app may wrap the brief; its first prompt still contains it
  id = 'c-1'
  await $.turn.start({ text: `Background: …

${brief}`, turnId: 't1' })
  await $.turn.complete({ turnId: 't1', answer: 'xong', durationMs: 1, isAborted: false })
  expect(store.get('session:c-1')).toMatchObject({ parent: 'p-1', depth: 1 })
  expect([...store.keys()].filter(key => key.startsWith('pending-child:')).length).toBe(0) // taken once
  // the old mark still works; at depth 2 a further child would be depth 3: refused before the app is asked
  await $.turn.start({ text: '[tt-parent:c-0 tầng:2] tiếp', turnId: 't2' })
  const before = calls.length
  const refused = await $.tool.call({ tool: 'mcp__ccd_session__start_session', title: 'C', prompt: 'Việc C',
    initiation: 'user_asked' } as never)
  expect(calls.length).toBe(before)
  expect(String((refused as { deny?: string }).deny)).toContain('trần là 2 tầng')
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const text = await allText(ui)
  expect(text).toContain('⑂ Mở phiên con: Làm A (tầng 1)')
  expect(text).toContain('⛔ Không mở phiên con')
  await ui.unmount()
})

test('0.16: Tất cả phiên draws children under their parent, with the branch $ and a mode clash', async ($, on) => {
  resetProfile()
  engine(on)
  on('session.id', () => ({ value: 'root' }))
  await $.session.start({ cwd: 'C:\\work\\root', surface: 'desktop', isInteractive: true })
  const store = stores[stores.length - 1]
  const now = Date.now()
  store.set('session:kid', { id: 'kid', project: 'kid-proj', status: 'working', agentsRunning: 0, problems: 0, at: now,
    parent: 'root', depth: 1, usd: 1.5, mode: 'default' })
  store.set('session:root', { id: 'root', project: 'root', status: 'idle', agentsRunning: 0, problems: 0, at: now,
    depth: 0, usd: 2, mode: 'auto' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'tab-all' })
  const own = (store.get('session:root') as { usd?: number }).usd ?? 0 // this session's own summary, rewritten by the pane
  const text = await allText(ui)
  expect(text).toMatch(new RegExp(`root \\(phiên này\\)[\\s\\S]*cả nhánh \\$${(own + 1.5).toFixed(2).replace('.', '\\.')}` +
    '[\\s\\S]*└ ◐ kid-proj'))
  expect(text).toContain('⚠ khác chế độ quyền với phiên cha (default ≠ auto)')
  await ui.unmount()
})

test('0.16: a question in Hộp thư is answered with Trả lời; the answer pairs with the question sent', async ($, on) => {
  resetProfile()
  engine(on, true)
  on('session.id', () => ({ value: 'me-1' }))
  const sent: { to: string; text: string }[] = []
  on('session.send', (_, e) => { sent.push(e as never); return { isDelivered: true } })
  on('session.receive', (_, e) => ({ text: e.text }))
  await $.session.start({ cwd: 'C:\\work\\demo', surface: 'desktop', isInteractive: true })
  // a question from an agent of another session
  await $.session.receive({ origin: { kind: 'peer' }, text: withQuestion('Lệnh build là gì?', 'qq11', 'other-9/a-3') } as never)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await allText(ui)).toMatch(/hỏi \[q:qq11\]: Lệnh build là gì\?/)
  await ui.press({ key: 'reply-qq11' })
  expect((await ui.find({ type: 'Select', key: 'ask-to' }))?.props.value).toBe('s:other-9')
  await ui.input({ key: 'ask-text', text: '[re:qq11] mvn -o package' })
  expect(sent.at(-1)).toMatchObject({ to: 'other-9', text: '[re:qq11] mvn -o package' }) // already marked: left as is
  expect(await allText(ui)).toContain('✓ đã trả lời')
  // the model's own SendMessage to an agent of this session is marked and kept as a question
  await $.agent.spawn({ prompt: 'x', description: 'dev', subagentType: 'developer' })
  await $.session.send({ to: 'a1', text: 'Xong chưa?' }) // a model's SendMessage, the id as the tool spells it
  const q = readMark(sent.at(-1)!.text)!
  expect(q).toMatchObject({ kind: 'q', from: 'me-1' })
  await $.session.receive({ origin: { kind: 'peer' }, text: `[re:${q.code}] Xong rồi` } as never)
  const text = await allText(ui)
  expect(text).toMatch(new RegExp(`✓ \\[q:${q.code}\\] tới agent dev[\\s\\S]*↩ Xong rồi`))
  expect(text).toContain(`↩ trả lời [q:${q.code}]`)
  await ui.unmount()
})

test('M5: a message from another session shows in Hộp thư and still reaches the model', async ($, on) => {
  resetProfile()
  engine(on)
  on('session.receive', (_, e) => ({ text: e.text }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const passed = await $.session.receive({ origin: { kind: 'peer' }, text: 'Thử chạy mvn -o', event: undefined } as never)
  expect(passed).toBeDefined()
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  let text = await allText(ui)
  expect(text).toContain('✉ 1 tin mới từ phiên khác')
  expect(text).toMatch(/Hộp thư \(1 chưa đọc\)[\s\S]*từ phiên khác: Thử chạy mvn -o/)
  await ui.press({ key: 'inbox-read' })
  text = await allText(ui)
  expect(text).not.toContain('chưa đọc')
  await ui.unmount()
})

test('0.15: each card says where its agent is defined; the roles panel lists the agents of each role', async ($, on) => {
  resetProfile()
  engine(on)
  const catalog = [
    { name: 'tester', call: 'tester', level: 'project', role: 'tester', model: 'sonnet' },
    { name: 'tester', call: 'token-tools:tester', level: 'plugin', role: 'tester', model: 'sonnet' },
  ]
  on('session.cwd', () => ({ value: 'C:/repo' }))
  on('process.run', (_, e) => ({ value: { exitCode: 0, stderr: '', isStdoutTruncated: false, isStderrTruncated: false,
    stdout: e.argv.includes('agents') ? JSON.stringify({ agents: catalog })
      : e.argv.includes('permissions') ? JSON.stringify(PROFILE) : JSON.stringify({ error: 'x' }) } }))
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn({ prompt: 'x', description: 'Test A', subagentType: 'tester' })
  await $.agent.spawn({ prompt: 'x', description: 'Test B', subagentType: 'token-tools:tester' })
  await $.agent.spawn({ prompt: 'x', description: 'Tìm C', subagentType: 'Explore' })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const text = await allText(ui)
  expect(text).toContain('sonnet 5.5 · project')
  expect(text).toContain('sonnet 5.5 · plugin')
  expect(text).toContain('sonnet 5.5 · có sẵn')
  expect(text).toContain('▶ Test B (token-tools:tester, sonnet 5.5, vai tester, plugin)')
  await ui.press({ key: 'roles' })
  expect(await allText(ui)).toContain('Agent gọi được: tester (project), token-tools:tester (plugin)')
  await ui.unmount()
})

test('M3: no profile (Python missing) restricts nothing and says why', async ($, on) => {
  resetProfile()
  let ran = 0
  engine(on, false, () => { ran += 1 })
  const spawn = profileEngine(on, 'tester', false)
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  await $.agent.spawn(spawn)
  await $.tool.call({ tool: 'Write', file_path: 'C:/repo/src/main/A.java', content: 'x', agentId: 'a1' })
  expect(ran).toBe(1)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /hồ sơ quyền/ }))?.text).toContain('no python')
  await ui.unmount()
})

test('0.17: the /token-tools:ship run shows on top: stage, steps, budget, result', async ($, on) => {
  const ship = { id: 'r1', title: 'Trang báo cáo', stage: 3, stage_name: 'Chạy', stages: 7, steps_done: 1, steps: 3,
    failed: 0, running: ['API'], result: null, check: null }
  expect(shipLine(parseAgentUsage(JSON.stringify({ agents: {}, total: { tokens: 0, usd: 0 }, ship })).ship!,
    { usedTokens: 2e6, budgetTokens: 8e6 }))
    .toBe('Quy trình: Trang báo cáo · bước 3/6 Chạy · 1/3 việc xong · đang: API · ngân sách 25%')
  engine(on)
  on('session.id', () => ({ value: 's1' }))
  on('session.usage', () => ({ value: { startedAt: 0, changed: [] } }))
  on('process.run', () => ({ value: { exitCode: 0, stderr: '', isStdoutTruncated: false, isStderrTruncated: false,
    stdout: JSON.stringify({ agents: {}, total: { tokens: 0, usd: 0 }, ship: { ...ship, stage: 6, stage_name: 'Học',
      steps_done: 3, running: [], result: 'ok', check: { ratio: 1.3, within_p90: true } } }) } }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await refreshUsage($)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const text = await allText(ui)
  expect(text).toContain('Quy trình: Trang báo cáo · bước 6/6 Học · 3/3 việc xong · kết quả đạt · thực tế ×1.30 P50')
  expect(text).toContain('● ● ● ● ● ● ●')
  await ui.unmount()
})

test('0.21: an open gate shows under the ship run; a button records the decision and drafts the line', async ($, on) => {
  const gate = { id: 'g1', step: 'API', question: 'Sửa component dùng chung hay chỉ trang này?', options: ['dùng chung', 'chỉ trang này'] }
  expect(gateDraft(gate, 'dùng chung'))
    .toBe('Quyết định g1 cho bước API (Sửa component dùng chung hay chỉ trang này?): dùng chung')
  const ship = { id: 'r1', title: 'Trang báo cáo', stage: 3, stage_name: 'Chạy', stages: 7, steps_done: 1, steps: 3,
    failed: 0, running: [], result: null, check: null,
    gates: [gate, { id: 'g2', step: 'UI', question: 'Tên cột là gì?', options: [] }] }
  expect(parseAgentUsage(JSON.stringify({ agents: {}, total: { tokens: 0, usd: 0 }, ship: { ...ship, gates: undefined } }))
    .ship!.gates).toEqual([]) // a run written before 0.21
  resetProfile()
  engine(on)
  on('session.id', () => ({ value: 's1' }))
  on('session.usage', () => ({ value: { startedAt: 0, changed: [] } }))
  const ran: string[][] = []
  let open = ship.gates
  on('process.run', (_, e) => {
    ran.push([...e.argv])
    if (e.argv.includes('ship-gate')) open = open.filter(item => item.id !== 'g1')
    return { value: { exitCode: 0, stderr: '', isStdoutTruncated: false, isStderrTruncated: false,
      stdout: JSON.stringify({ agents: {}, total: { tokens: 0, usd: 0 }, ship: { ...ship, gates: open } }) } }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await refreshUsage($)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  let text = await allText(ui)
  expect(text).toContain('❓ Bước API chờ bạn quyết định: Sửa component dùng chung hay chỉ trang này?')
  expect(text).toContain('Trả lời Claude trong ô chat (cổng g2).')
  expect((await ui.find({ type: 'Button', key: 'gate-g1-1' }))?.text).toBe('chỉ trang này')
  await ui.press({ key: 'gate-g1-0' })
  await new Promise(resolve => setTimeout(resolve, 50))
  const call = ran.find(command => command.includes('ship-gate'))!
  expect(call.slice(-6)).toEqual(['ship-gate', 'r1', '--resolve', 'g1', '--answer', 'dùng chung'])
  text = await allText(ui)
  expect(text).not.toContain('Bước API chờ bạn quyết định')
  expect(text).toContain('Bước UI chờ bạn quyết định: Tên cột là gì?')
  await ui.unmount()
})

test('0.17.5: a child session the app refuses (auto mode) is logged as not opened, not as opened', async ($, on) => {
  resetProfile()
  engine(on)
  on('session.id', () => ({ value: 'p-9' }))
  on('process.run', () => ({ value: { exitCode: 1, stdout: '', stderr: 'x', isStdoutTruncated: false,
    isStderrTruncated: false } }))
  await $.session.start({ cwd: 'C:\work\demo', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'mcp__ccd_session__start_session', title: 'blocked', prompt: 'Việc A', initiation: 'user_asked' } as never)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const text = await allText(ui)
  expect(text).toContain('⛔ Mở phiên con: blocked — không mở được: chế độ Auto chặn')
  expect(text).not.toContain('⑂ Mở phiên con')
  await ui.unmount()
})

test('0.17.7: Phiên này shows a card for each child session, what it is doing, and asks it', async ($, on) => {
  resetProfile()
  engine(on)
  on('session.id', () => ({ value: 'root' }))
  await $.session.start({ cwd: 'C:/work/root', surface: 'desktop', isInteractive: true })
  stores[stores.length - 1].set('session:kid', { id: 'kid', project: 'jsondiff', status: 'working', agentsRunning: 0,
    problems: 0, at: Date.now(), parent: 'root', depth: 1, task: 'Viết jsondiff/core.py', doing: 'Write core.py',
    calls: 7, usd: 0.4 })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const text = await allText(ui)
  expect(text).toContain('0 agent · 0 đang làm · 1 phiên con (1 đang làm)')
  expect(text).toMatch(/◐ ⑂ Viết jsondiff\/core\.py[\s\S]*7 lượt[\s\S]*▸ đang: Write core\.py/)
  await ui.press({ key: 'ask-kid-kid' })
  expect((await ui.find({ type: 'Select', key: 'ask-to' }))?.props.value).toBe('s:kid')
  await ui.unmount()
})

test('0.18: Báo cáo ship makes the page with ctt and opens it with the system opener', async ($, on) => {
  expect(openerFor('C:/Users/x/.claude/claude-token-tools/reports/ship-2026-11-12.html'))
    .toEqual(['explorer.exe', String.raw`C:\Users\x\.claude\claude-token-tools\reports\ship-2026-11-12.html`])
  expect(openerFor('/Users/x/r.html')).toEqual(['open', '/Users/x/r.html'])
  expect(openerFor('/home/x/r.html')).toEqual(['xdg-open', '/home/x/r.html'])
  resetProfile()
  engine(on)
  const ran: string[][] = []
  on('process.run', (_, e) => {
    ran.push([...e.argv])
    return { value: { exitCode: 0, stderr: '', isStdoutTruncated: false, isStderrTruncated: false,
      stdout: e.argv.includes('ship-report') ? JSON.stringify({ page: 'C:/r/ship.html',
        totals: { runs: 4, done: 2, ok: 1, usd: 12.16 }, advice: [] }) : '' } }
  })
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'ship-report' })
  expect(ran.some(argv => argv.includes('ship-report') && argv.includes('--json'))).toBe(true)
  expect(ran.at(-1)).toEqual(['explorer.exe', String.raw`C:\r\ship.html`])
  expect(await allText(ui)).toContain('4 lượt · 2 đã xong · 1 đạt · $12.16 — C:/r/ship.html')
  await ui.unmount()
})

const MCP_JSON = {
  servers: [
    { name: 'plugin:gitlab:gitlab', source: 'plugin', type: 'http', status: 'needs_auth', calls: 0, errors: 0, result_tokens: 0,
      last_at: null, tools: {} },
    { name: 'code-graph', source: 'user', type: 'stdio', status: 'configured', calls: 7, errors: 1, result_tokens: 12_300,
      last_at: '2026-11-17T01:00:00Z', tools: { cg_impact: 5, cg_search: 2 } },
    { name: 'old', source: 'local', type: 'stdio', status: 'disabled', calls: 0, errors: 0, result_tokens: 0, last_at: null, tools: {} },
  ],
}
const MCP_TOOLS = [
  { name: 'mcp__code-graph__cg_impact', serverName: 'code-graph', tokens: 400, isLoaded: true },
  { name: 'mcp__code-graph__cg_search', serverName: 'code-graph', tokens: 350, isLoaded: true },
  { name: 'mcp__Figma__get_metadata', serverName: 'Figma', tokens: 900, isLoaded: true },
  { name: 'mcp__ccd_session__x', serverName: 'ccd_session', tokens: 99, isLoaded: true },
]

function mcpEngine(on: Parameters<Parameters<typeof test>[1]>[1], exitCode = 0, seen: string[][] = []) {
  engine(on)
  on('session.id', () => ({ value: 's1' }))
  on('session.cwd', () => ({ value: 'C:/repo' }))
  on('session.usage', () => ({ value: { startedAt: 0, changed: [], context: { window: 1e6, percent: 10, breakdown: { mcpTools: MCP_TOOLS } } } }))
  on('process.run', (_, e) => {
    seen.push([...e.argv])
    const mcp = e.argv.includes('mcp')
    return { value: { exitCode, stderr: exitCode ? 'boom' : '', isStdoutTruncated: false, isStderrTruncated: false,
      stdout: exitCode ? '' : mcp ? JSON.stringify(MCP_JSON) : AGENT_USAGE } }
  })
}

test('MCP helpers: label, merge with the context breakdown, engine-only servers, ccd_ left out', () => {
  expect(mcpLabel('plugin:gitlab:gitlab')).toBe('gitlab (plugin gitlab)')
  expect(mcpLabel('plugin:x:y')).toBe('y (plugin x)')
  expect(mcpLabel('code-graph')).toBe('code-graph')
  const rows = mergeMcp(MCP_JSON.servers, MCP_TOOLS)
  const by = Object.fromEntries(rows.map(row => [row.name, row]))
  expect(by['code-graph']).toMatchObject({ status: 'connected', schemaTokens: 750, loadedTools: 2, calls: 7 }) // configured → connected
  expect(by.Figma).toMatchObject({ source: 'unknown', status: 'connected', schemaTokens: 900 })
  expect(by['plugin:gitlab:gitlab'].status).toBe('needs_auth')
  expect(by.old.status).toBe('disabled')
  expect(by.ccd_session).toBeUndefined()
  // a plugin server: the engine spells it plugin_x_y or plugin:x:y, ctt plugin:x:y — one row either way
  for (const spelled of ['plugin_gitlab_gitlab', 'plugin:gitlab:gitlab']) {
    const merged = mergeMcp(MCP_JSON.servers, [{ name: `mcp__${spelled}__t`, serverName: spelled, tokens: 120, isLoaded: true }])
    const plugin = merged.filter(row => row.name.includes('gitlab'))
    expect(plugin).toHaveLength(1)
    expect(plugin[0]).toMatchObject({ name: 'plugin:gitlab:gitlab', source: 'plugin', status: 'connected', schemaTokens: 120 })
  }
  // junk in the breakdown is skipped, not thrown on
  expect(mergeMcp([], [null, 5, { name: 'x' }] as never)).toEqual([])
  expect(mergeMcp([], [{ name: 'a', serverName: 's', tokens: 5, isLoaded: false }])[0].status).toBe('connected') // known to the engine
})

test('MCP section: rows with status, schema, calls, errors, result tokens; Xem thêm shows per-tool calls', async ($, on) => {
  resetMcp()
  const seen: string[][] = []
  mcpEngine(on, 0, seen)
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await new Promise(resolve => setTimeout(resolve, 50))
  const text = await allText(ui)
  const call = seen.find(argv => argv.includes('mcp'))!
  expect(call.slice(call.indexOf('mcp'))).toEqual(['mcp', '--json', '--session', 's1', '--cwd', 'C:/repo'])
  for (const piece of ['MCP · 4 server', 'gitlab (plugin gitlab)', 'code-graph', 'đã nối', 'tắt',
    'schema 750 (2 tool)', '7 lần gọi', '1 lỗi', 'kết quả 12k token', 'Figma', 'schema 900 (1 tool)']) {
    expect(text).toContain(piece)
  }
  expect(text).not.toContain('ccd_session')
  expect(text).not.toContain('cg_impact ×5')
  await ui.press({ key: 'mcp-more-code-graph' })
  expect(await allText(ui)).toContain('cg_impact ×5 · cg_search ×2')
  await ui.unmount()
})

test('MCP section: a server that needs login adds a line to Cần chú ý', async ($, on) => {
  resetMcp()
  mcpEngine(on)
  await $.session.start({ cwd: 'C:/repo', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await new Promise(resolve => setTimeout(resolve, 50))
  const text = await allText(ui)
  expect(text).toContain('Cần chú ý')
  expect(text).toContain('gitlab (plugin gitlab) cần đăng nhập — chạy /mcp')
  expect(text).toContain('cần đăng nhập')
  await ui.unmount()
})

test('MCP section: a failing ctt shows one line and keeps the pane and the engine-only rows', async ($, on) => {
  resetMcp()
  mcpEngine(on, 1)
  await $.session.start({ cwd: 'C:/repo', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await new Promise(resolve => setTimeout(resolve, 50))
  const text = await allText(ui)
  expect(text).toContain('Chưa đọc được MCP')
  expect(text).toContain('Figma') // from the context breakdown alone
  expect(text).toContain('Phiên này') // the rest of the pane still draws
  expect(text).not.toContain('cần đăng nhập — chạy /mcp')
  await ui.unmount()
})
