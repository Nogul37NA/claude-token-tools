import { expect, mock, test } from 'claude-code/testing'

import { resetNotify, resetMcp, resetProfile } from '../hooks/register'
import {
  applyPlan, cachedItems, DEFAULT_NOTIFY, emptySent, failureText, normalizeSettings, notifyItems, notifyOptions, notifySig, planNotify, stateText, tickWork,
} from '../hooks/notify'
import type { NotifyItem, NotifySettings, NotifySnapshot, SentState } from '../hooks/notify'
import type { RaceSnap, ShipSnap } from '../types'

const NOW = 1_700_000_000_000
const SHIP: ShipSnap = {
  id: 'r1', title: 'T', stage: 3, stageName: 'Chạy', stages: 7, stepsDone: 1, steps: 3, failed: 0, running: [],
  stalled: [{ name: 'API', minutes: 12 }],
  escalate: [{ name: 'UI', model: 'opus', effort: 'high' }, { name: 'DB', exhausted: true }],
  gates: [{ id: 'g1', step: 'API', question: 'Sửa chung?', options: ['có', 'không'] }],
}
const RACE: RaceSnap = { id: 'k1', title: 'Parser', status: 'judging', suggested: 'B', entrants: [] }
const SNAP: NotifySnapshot = {
  project: 'shop_be', now: NOW,
  events: [
    { at: NOW - 1000, text: '⛔ tester: ghi ngoài thư mục test' },
    { at: NOW - 2000, text: '❓ developer: chạy lệnh mvn deploy' },
    { at: NOW - 3000, text: '⚠ developer: qua 80% ngân sách (80k / 100k (80%))' },
    { at: NOW - 4000, text: '‼ tester: hết ngân sách — mỗi lượt gọi tool sẽ hỏi bạn (120k / 100k (120%))' },
    { at: NOW - 5000, text: '✗ developer: Bash mvn — lỗi' },
    { at: NOW - 5000, text: '▶ developer (x)' },
    { at: NOW - 16 * 60_000, text: '⛔ old: quá cũ' },
  ],
  ship: SHIP, race: RACE, budget: { id: 'e', title: 'Phiếu', budgetTokens: 100, usedTokens: 85, p50Tokens: 0, p90Tokens: 0 },
  mcp: [{ name: 'plugin:gitlab:gitlab', source: 'plugin', status: 'needs_auth', calls: 0, errors: 0, resultTokens: 0, tools: {},
    schemaTokens: 0, loadedTools: 0 }],
}
const ALL_ON: NotifySettings = { on: true, delaySec: 60, cats: { blocked: true, ship: true, race: true, budget: true, mcp: true } }
const item = (key: string, category: NotifyItem['category'] = 'blocked'): NotifyItem => ({ key, text: `p: ${key}`, category })

/** Run a plan as register.tsx does, every send accepted. */
function step(items: NotifyItem[], state: SentState, settings: NotifySettings, now: number) {
  const plan = planNotify(items, state, settings, now)
  const ok = Object.fromEntries(plan.send.map(entry => [entry.key, true]))
  return { plan, state: applyPlan(state, plan, now, ok, items) }
}

test('items: each source gives stable keys, a category, project first, at most 160 chars', () => {
  const items = notifyItems(SNAP)
  const by = (key: string) => items.find(entry => entry.key === key)
  expect(by(`blocked:tester:${NOW - 1000}`)).toMatchObject({ category: 'blocked', text: 'shop_be: ⛔ tester: ghi ngoài thư mục test' })
  expect(by(`asked:developer:${NOW - 2000}`)?.category).toBe('blocked')
  expect(by('budget:developer:80')?.category).toBe('budget')
  expect(by('budget:tester:100')?.category).toBe('budget')
  expect(by('budget:ticket:e:80')?.text).toContain('85%')
  expect(by('ship:r1:stalled:API')).toMatchObject({ category: 'ship' })
  expect(items.some(entry => entry.key.includes(':UI'))).toBe(false) // a step still on the ladder is not worth a ping
  expect(by('ship:r1:esc:DB')?.text).toContain('hết thang')
  expect(by('ship:r1:gate:g1')?.text).toBe('shop_be: ❓ Bước API chờ bạn quyết định: Sửa chung?')
  expect(by('race:k1')?.text).toBe('shop_be: Race Parser: chọn bản thắng (đề xuất B)')
  expect(by('mcp:plugin:gitlab:gitlab')).toMatchObject({ category: 'mcp' })
  expect(items.some(entry => entry.key.includes('old') || entry.text.includes('✗'))).toBe(false) // too old / not attention
  expect(items.every(entry => entry.text.startsWith('shop_be: ') && entry.text.length <= 160)).toBe(true)
  const long = notifyItems({ ...SNAP, events: [{ at: NOW, text: `⛔ a: ${'x'.repeat(400)}` }], ship: undefined, race: undefined, mcp: [] })
  expect(long[0].text.length).toBe(160)
  expect(long[0].text.endsWith('…')).toBe(true)
  // finished ship run, picked race, connected server: nothing
  expect(notifyItems({ ...SNAP, events: [], budget: undefined, ship: { ...SHIP, result: 'ok' },
    race: { ...RACE, winner: 'B' }, mcp: [{ ...SNAP.mcp![0], status: 'connected' }] })).toEqual([])
})

test('plan: once per key, cancel on resolve, cooldown after it comes back', () => {
  let state = emptySent()
  let result = step([item('a'), item('b')], state, ALL_ON, NOW)
  expect(result.plan.send.map(entry => entry.key)).toEqual(['a', 'b'])
  state = result.state
  result = step([item('a'), item('b')], state, ALL_ON, NOW + 3000) // still open: nothing again
  expect(result.plan).toEqual({ send: [], cancel: [], dropped: [] })
  result = step([item('a')], result.state, ALL_ON, NOW + 6000) // b resolved
  expect(result.plan).toEqual({ send: [], cancel: ['b'], dropped: [] })
  result = step([item('a'), item('b')], result.state, ALL_ON, NOW + 9000) // b again within 10 min: cooling down
  expect(result.plan.send).toEqual([])
  result = step([item('a')], result.state, ALL_ON, NOW + 12_000)
  result = step([item('a'), item('b')], result.state, ALL_ON, NOW + 11 * 60_000) // after the cooldown: sent again
  expect(result.plan.send.map(entry => entry.key)).toEqual(['b'])
})

test('plan: at most 6 per 10 minutes; extras are reported once and sent when the window has room', () => {
  const many = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map(key => item(key))
  let result = step(many, emptySent(), ALL_ON, NOW)
  expect(result.plan.send).toHaveLength(6)
  expect(result.plan.dropped.map(entry => entry.key)).toEqual(['g', 'h'])
  result = step(many, result.state, ALL_ON, NOW + 3000)
  expect(result.plan).toEqual({ send: [], cancel: [], dropped: [] }) // not reported again
  const later = step(many, result.state, ALL_ON, NOW + 11 * 60_000) // window over: g and h go out
  expect(later.plan.send.map(entry => entry.key)).toEqual(['g', 'h'])
  expect(later.plan.dropped).toEqual([])
  expect(later.state.skipped).toEqual([])
  // one that resolves while held back is forgotten, not sent
  const gone = step(many.slice(0, 7), result.state, ALL_ON, NOW + 11 * 60_000)
  expect(gone.plan.send.map(entry => entry.key)).toEqual(['g'])
})

test('keys: a new question of the same agent is a new notification; run id and budget id are in the keys', () => {
  const first = notifyItems({ project: 'p', now: NOW, events: [{ at: NOW - 5000, text: '❓ dev: chạy mvn' }] })
  const again = notifyItems({ project: 'p', now: NOW + 60_000, events: [{ at: NOW - 5000, text: '❓ dev: chạy mvn' },
    { at: NOW + 30_000, text: '❓ dev: ghi file khác' }] })
  expect(again).toHaveLength(1) // only the newest of the agent and kind
  expect(again[0].key).not.toBe(first[0].key)
  // answered (gone), then asked again within the cooldown: still sent
  let result = step(first, emptySent(), ALL_ON, NOW)
  result = step([], result.state, ALL_ON, NOW + 10_000)
  expect(step(again, result.state, ALL_ON, NOW + 60_000).plan.send).toHaveLength(1)
  const gate = (id: string): NotifySnapshot => ({ project: 'p', now: NOW, events: [], ship: { ...SHIP, id, stalled: [], escalate: [] } })
  expect(notifyItems(gate('r1'))[0].key).not.toBe(notifyItems(gate('r2'))[0].key)
  const budget = (id: string): NotifySnapshot => ({ project: 'p', now: NOW, events: [],
    budget: { id, title: 'T', budgetTokens: 100, usedTokens: 90, p50Tokens: 0, p90Tokens: 0 } })
  expect(notifyItems(budget('e1'))[0].key).not.toBe(notifyItems(budget('e2'))[0].key)
})

test('plan: a category switched off sends nothing and takes back what was sent; off takes back everything', () => {
  const items = [item('a', 'blocked'), item('r', 'race')]
  const noRace = { ...ALL_ON, cats: { ...ALL_ON.cats, race: false } }
  let result = step(items, emptySent(), noRace, NOW)
  expect(result.plan.send.map(entry => entry.key)).toEqual(['a'])
  result = step(items, emptySent(), ALL_ON, NOW)
  result = step(items, result.state, noRace, NOW + 3000)
  expect(result.plan.cancel).toEqual(['r'])
  expect(step(items, result.state, { ...ALL_ON, on: false }, NOW + 6000).plan).toEqual({ send: [], cancel: ['a'], dropped: [] })
  expect(step(items, emptySent(), { ...ALL_ON, on: false }, NOW).plan.send).toEqual([])
})

test('options: a delay sends delayMs with untilBack; none sends now; title is token-tools', () => {
  expect(notifyOptions(item('a'), { ...ALL_ON, delaySec: 60 })).toEqual({ title: 'token-tools', key: 'a', delayMs: 60_000, untilBack: true })
  expect(notifyOptions(item('a'), { ...ALL_ON, delaySec: 300 })).toMatchObject({ delayMs: 300_000, untilBack: true })
  expect(notifyOptions(item('a'), { ...ALL_ON, delaySec: 0 })).toEqual({ title: 'token-tools', key: 'a' })
})

test('settings: defaults (on, 60 s, MCP off), bad stored values ignored, state text', () => {
  expect(DEFAULT_NOTIFY).toEqual({ on: true, delaySec: 60, cats: { blocked: false, ship: true, race: false, budget: true, mcp: false } })
  expect(normalizeSettings(undefined)).toEqual(DEFAULT_NOTIFY)
  expect(normalizeSettings({ on: false, delaySec: 7, cats: { race: false, mcp: 'x' } }))
    .toEqual({ ...DEFAULT_NOTIFY, on: false, cats: { ...DEFAULT_NOTIFY.cats, race: false } })
  expect(normalizeSettings({ delaySec: 300 }).delaySec).toBe(300)
  expect(stateText(DEFAULT_NOTIFY)).toBe('Bật · chờ 60 s')
  expect(stateText({ ...DEFAULT_NOTIFY, delaySec: 300 })).toBe('Bật · chờ 5 phút')
  expect(stateText({ ...DEFAULT_NOTIFY, delaySec: 0 })).toBe('Bật · gửi ngay')
  expect(stateText({ ...DEFAULT_NOTIFY, on: false })).toBe('Tắt')
  expect(failureText('no-channel')).toContain('bật thông báo của app')
  expect(failureText('no-surface')).toBe('không gửi được (no-surface) — app này chưa nhận thông báo từ mod; xem mục Cần chú ý trong khung')
  expect(failureText('refused')).toBe('không gửi được (refused)')
  expect(failureText('refused')).not.toContain('bật thông báo của app')
})

test('tick: pane closed still reads usage every 60 s when notifications are on, MCP only with its category', () => {
  const closed = (tick: number, settings: NotifySettings) => tickWork(tick, false, settings)
  expect(closed(5, DEFAULT_NOTIFY)).toEqual({ usage: false, mcp: false })
  expect(closed(20, DEFAULT_NOTIFY)).toEqual({ usage: true, mcp: false })
  expect(closed(20, ALL_ON)).toEqual({ usage: true, mcp: true })
  expect(closed(20, { ...ALL_ON, on: false })).toEqual({ usage: false, mcp: false })
  expect(closed(20, { ...ALL_ON, cats: { ...ALL_ON.cats, ship: false, race: false, budget: false } }).usage).toBe(false)
  expect(tickWork(5, true, DEFAULT_NOTIFY)).toEqual({ usage: true, mcp: false }) // pane open: every 15 s
  expect(tickWork(20, true, { ...DEFAULT_NOTIFY, on: false })).toEqual({ usage: true, mcp: true })
})

const RACE_USAGE = (race: unknown, ship?: unknown) => JSON.stringify({ agents: {}, total: { tokens: 0, usd: 0 }, race, ship })
const RACE_JSON = { id: 'k1', title: 'Parser', status: 'judging', winner: null, suggested: 'B', entrants: [] }
const T0 = 1_700_000_000_000

/** The engine beneath the mod: a mocked clock (the mod's own 3 s tick runs when the test advances it), a store, a
 *  usage read answering `race()`, and `ui.notify` / `ui.cancelNotify` recorded. */
function tickEngine(on: Parameters<Parameters<typeof test>[1]>[1], answer: () => unknown, race: () => unknown,
  entries: Record<string, unknown> = {}, opts: { ship?: () => unknown; failGets?: number } = {}) {
  const clock = mock.clock(on, { now: T0 })
  const store = new Map<string, unknown>(Object.entries(entries))
  const writes: string[] = []
  let failing = opts.failGets ?? 0
  on('store.get', (_, e) => {
    if (e.key === 'notify-settings' && failing > 0) { failing -= 1; throw new Error('store down') }
    return { value: store.get(e.key) }
  })
  on('store.set', (_, e) => { writes.push(e.key); store.set(e.key, JSON.parse(JSON.stringify(e.value))); return { value: undefined } })
  on('store.keys', () => ({ value: [...store.keys()] }))
  on('store.delete', (_, e) => { store.delete(e.key); return { value: undefined } })
  on('command.register', () => ({ value: { command: 'control-room' } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 's1' }))
  on('session.usage', () => ({ value: { startedAt: 0, changed: [] } }))
  on('agent.list', () => ({ value: [] }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  const ran: string[][] = [] // the ctt agent-usage reads only
  on('process.run', (_, e) => {
    if ((e.argv as string[]).includes('agent-usage')) ran.push(e.argv as string[])
    return { value: { exitCode: 0, stderr: '', isStdoutTruncated: false, isStderrTruncated: false, stdout: RACE_USAGE(race(), opts.ship?.()) } }
  })
  const sent: { text: string; options: Record<string, unknown> }[] = []
  const cancelled: string[] = []
  on('ui.notify', async (_, e) => {
    sent.push({ text: e.text, options: { title: e.title, key: e.key, delayMs: e.delayMs, untilBack: e.untilBack } })
    return { value: await answer() }
  })
  on('ui.cancelNotify', (_, e) => { cancelled.push(e.key); return { value: { isSent: true } } })
  return { clock, sent, cancelled, ran, writes }
}

// The race pick and ship gates are off by default (the app already notifies questions); these tests turn them on.
const RACE_ON = { 'notify-settings': { on: true, cats: { race: true, blocked: true } } }

const PANE_PROPS = {
  plugin: 'token-tools', component: 'Pane', requestId: 'control-room', surface: 'desktop',
  props: { title: 'Phòng điều hành', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as never

test('tick with the pane closed: usage is read, a race waiting for a pick is notified once, then cancelled when picked', async ($, on) => {
  resetProfile()
  resetMcp()
  resetNotify()
  let race: unknown = RACE_JSON
  const seen = tickEngine(on, () => ({ isSent: true, channel: 'client' }), () => race, RACE_ON)
  await $.session.start({ cwd: 'C:/work/shop_be', surface: 'desktop', isInteractive: true })
  await seen.clock.advance(30_000) // 10 ticks, pane never drawn: no usage read, so nothing to say
  expect(seen.ran).toHaveLength(0)
  expect(seen.sent).toEqual([])
  await seen.clock.advance(40_000) // the slow (60 s) read happens, the next tick notifies
  expect(seen.ran.some(argv => argv.includes('agent-usage'))).toBe(true)
  expect(seen.sent).toEqual([{ text: 'shop_be: Race Parser: chọn bản thắng (đề xuất B)',
    options: { title: 'token-tools', key: 'race:k1', delayMs: 60_000, untilBack: true } }])
  await seen.clock.advance(120_000) // still open: not again
  expect(seen.sent).toHaveLength(1)
  expect(seen.cancelled).toEqual([])
  race = { ...RACE_JSON, status: 'picked', winner: 'B' }
  await seen.clock.advance(70_000)
  expect(seen.cancelled).toEqual(['race:k1'])
  expect(seen.sent).toHaveLength(1)
})

test('defaults: a race waiting for a pick and a ship gate are not notified (the app asks those itself)', async ($, on) => {
  resetProfile()
  resetMcp()
  resetNotify()
  const seen = tickEngine(on, () => ({ isSent: true, channel: 'client' }), () => RACE_JSON, {}, { ship: () => GATE_SHIP('r1', 'g1') })
  await $.session.start({ cwd: 'C:/work/shop_be', surface: 'desktop', isInteractive: true })
  await seen.clock.advance(200_000)
  expect(seen.ran.some(argv => argv.includes('agent-usage'))).toBe(true)
  expect(seen.sent).toEqual([])
})

test('off: nothing is read while the pane is closed and nothing is sent', async ($, on) => {
  resetProfile()
  resetMcp()
  resetNotify()
  const seen = tickEngine(on, () => ({ isSent: true, channel: 'client' }), () => RACE_JSON, { 'notify-settings': { on: false } })
  await $.session.start({ cwd: 'C:/work/shop_be', surface: 'desktop', isInteractive: true })
  await seen.clock.advance(200_000)
  expect(seen.ran).toHaveLength(0)
  expect(seen.sent).toEqual([])
})

test('settings come from the store; a refused send shows its reason and hint; the pane toggles and keeps the choice', async ($, on) => {
  resetProfile()
  resetMcp()
  resetNotify()
  const seen = tickEngine(on, () => ({ isSent: false, reason: 'no-channel' }), () => RACE_JSON,
    { 'notify-settings': { on: true, delaySec: 0, cats: { race: true } } })
  await $.session.start({ cwd: 'C:/work/shop_be', surface: 'desktop', isInteractive: true })
  await seen.clock.advance(70_000)
  expect(seen.sent[0].options).toEqual({ title: 'token-tools', key: 'race:k1', delayMs: undefined, untilBack: undefined })
  const ui = await $.ui.mount(PANE_PROPS)
  const text = async () => (await ui.findAll({ type: 'Text' })).map(node => (node as { text?: string }).text ?? '').join(' | ')
  let shown = await text()
  expect(shown).toContain('Thông báo')
  expect(shown).toContain('Bật · gửi ngay')
  expect(shown).toContain('không gửi được (no-channel) — bật thông báo của app')
  expect(shown).toContain('🔕 race:k1: không gửi được (no-channel)') // and the log has it
  await ui.press({ key: 'notify-delay-300' })
  expect(await text()).toContain('Bật · chờ 5 phút')
  await ui.press({ key: 'notify-cat-race' })
  await ui.press({ key: 'notify-toggle' })
  shown = await text()
  expect(shown).toContain('Tắt')
  const before = seen.sent.length
  await seen.clock.advance(200_000)
  expect(seen.sent.length).toBe(before)
  await ui.unmount()
  // a new run reads the store again: the choices survived (off, 5 min)
  resetNotify()
  await $.session.start({ cwd: 'C:/work/shop_be', surface: 'desktop', isInteractive: true })
  const reopened = await $.ui.mount(PANE_PROPS)
  const kept = (await reopened.findAll({ type: 'Text' })).map(node => (node as { text?: string }).text ?? '').join(' | ')
  expect(kept).toContain('Tắt')
  await reopened.press({ key: 'notify-toggle' })
  expect((await reopened.findAll({ type: 'Text' })).map(node => (node as { text?: string }).text ?? '').join(' | '))
    .toContain('Bật · chờ 5 phút')
  await reopened.unmount()
})

const GATE_SHIP = (id: string, gate: string) => ({ id, title: 'T', stage: 3, stage_name: 'Chạy', stages: 7, steps_done: 1, steps: 3, failed: 0,
  running: [], result: null, check: null, gates: [{ id: gate, step: 'API', question: 'Sửa chung?', options: [] }] })

test('a store that cannot be read: nothing sent, nothing saved, retried on the next tick; then the stored settings apply', async ($, on) => {
  resetProfile()
  resetMcp()
  resetNotify()
  const seen = tickEngine(on, () => ({ isSent: true, channel: 'client' }), () => RACE_JSON,
    { 'notify-settings': { on: true, delaySec: 0, cats: { race: false } } }, { failGets: 40 })
  await $.session.start({ cwd: 'C:/work/shop_be', surface: 'desktop', isInteractive: true })
  await seen.clock.advance(100_000) // usage is read, but the settings never load
  expect(seen.sent).toEqual([])
  const ui = await $.ui.mount(PANE_PROPS)
  await ui.press({ key: 'notify-toggle' }) // no change is saved over what could not be read
  expect(seen.writes.filter(key => key === 'notify-settings')).toEqual([])
  await ui.unmount()
  await seen.clock.advance(120_000) // the store answers again: the stored choice (race off) holds, no defaults sent
  expect(seen.sent).toEqual([])
  expect(seen.writes.filter(key => key === 'notify-settings')).toEqual([])
})

test('a send that has not settled does not hold the tick: the next item still goes, each key once', async ($, on) => {
  resetProfile()
  resetMcp()
  resetNotify()
  let clockOf: { sleep: (ms: number) => Promise<void> } | undefined
  let ship: unknown = null
  const seen = tickEngine(on, () => clockOf!.sleep(300_000).then(() => ({ isSent: true, channel: 'client' })), () => RACE_JSON,
    RACE_ON, { ship: () => ship })
  clockOf = seen.clock
  await $.session.start({ cwd: 'C:/work/shop_be', surface: 'desktop', isInteractive: true })
  await seen.clock.advance(70_000) // the race goes out; its promise is still pending
  expect(seen.sent.map(entry => entry.options.key)).toEqual(['race:k1'])
  ship = GATE_SHIP('r1', 'g1')
  await seen.clock.advance(70_000) // not blocked behind the pending race send
  expect(seen.sent.map(entry => entry.options.key)).toEqual(['race:k1', 'ship:r1:gate:g1'])
  await seen.clock.advance(400_000) // both settle; neither is sent twice
  expect(seen.sent).toHaveLength(2)
  const ui = await $.ui.mount(PANE_PROPS)
  const shown = (await ui.findAll({ type: 'Text' })).map(node => (node as { text?: string }).text ?? '').join(' | ')
  expect(shown.match(/🔔 đã gửi/g)).toHaveLength(2) // one log line each, logged when the sends settled
  await ui.unmount()
  // the next run's gate g1 is a new notification (cooldown applies per key, and the run id is in it)
  ship = GATE_SHIP('r2', 'g1')
  await seen.clock.advance(70_000)
  expect(seen.sent.map(entry => entry.options.key)).toContain('ship:r2:gate:g1')
})

test('the item list is rebuilt only when usage, events or MCP changed (or each 30 s), not every tick', () => {
  const base = Math.floor(NOW / 30_000) * 30_000 // start of a 30 s bucket
  const log = [{ at: NOW, text: '⛔ a: x' }]
  let built = 0
  const build = () => { built += 1; return [item('a')] }
  let result = cachedItems(undefined, notifySig(1, 1, log, base), build)
  for (let i = 1; i <= 5; i += 1) result = cachedItems(result.cache, notifySig(1, 1, log, base + i * 3000), build) // same 30 s bucket
  expect(built).toBe(1)
  result = cachedItems(result.cache, notifySig(2, 1, log, base + 3000), build) // usage read again
  result = cachedItems(result.cache, notifySig(2, 2, log, base + 3000), build) // MCP read again
  result = cachedItems(result.cache, notifySig(2, 2, [...log, { at: NOW + 1, text: '❓ b: y' }], base + 3000), build) // new event
  expect(built).toBe(4)
  result = cachedItems(result.cache, notifySig(2, 2, [...log, { at: NOW + 1, text: '❓ b: y' }], base + 40_000), build) // next bucket
  expect(built).toBe(5)
  expect(result.built).toBe(true)
})

test('notification lines are shown in the pane log, kept apart from the events', async ($, on) => {
  resetProfile()
  resetMcp()
  resetNotify()
  const seen = tickEngine(on, () => ({ isSent: true, channel: 'client' }), () => RACE_JSON, RACE_ON)
  await $.session.start({ cwd: 'C:/work/shop_be', surface: 'desktop', isInteractive: true })
  await seen.clock.advance(300_000)
  expect(seen.sent).toHaveLength(1)
  const ui = await $.ui.mount(PANE_PROPS)
  const shown = (await ui.findAll({ type: 'Text' })).map(node => (node as { text?: string }).text ?? '').join(' | ')
  expect(shown).toContain('🔔 đã gửi race:k1')
  await ui.unmount()
})
