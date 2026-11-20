import type { BudgetSnap, EventRow, McpServerRow, RaceSnap, ShipSnap } from '../types'

// 0.26: desktop / phone notifications for what the pane's "Cần chú ý" lists. Pure helpers only: register.tsx reads
// the snapshot, calls `$.ui.notify` / `$.ui.cancelNotify` and keeps the state.

export type Category = 'blocked' | 'ship' | 'race' | 'budget' | 'mcp'
export const CATEGORIES: readonly Category[] = ['blocked', 'ship', 'race', 'budget', 'mcp']
export const CATEGORY_LABEL: Record<Category, string> = {
  blocked: 'Chặn / hỏi / duyệt', ship: 'Ship treo / hết bậc', race: 'Race', budget: 'Ngân sách', mcp: 'MCP',
}
export const DELAYS = [0, 60, 300] as const

export type NotifySettings = { on: boolean; delaySec: number; cats: Record<Category, boolean> }
// Off by default: what the app already notifies with Remote Control on (permission requests, questions — ⛔ / ❓,
// ship gates, the race pick). On: what nobody asks about, so the app stays silent (budget, stalled step, exhausted
// escalation ladder).
export const DEFAULT_NOTIFY: NotifySettings = {
  on: true, delaySec: 60, cats: { blocked: false, ship: true, race: false, budget: true, mcp: false },
}
export const NOTIFY_TITLE = 'token-tools'
export const COOLDOWN_MS = 10 * 60_000
export const RATE_WINDOW_MS = 10 * 60_000
export const MAX_PER_WINDOW = 6
export const MAX_TEXT = 160
export const EVENT_WINDOW_MS = 15 * 60_000 // same window as the pane's attention()

export type NotifyItem = { key: string; text: string; category: Category }
export type NotifySnapshot = {
  project: string; now: number; events: readonly EventRow[]
  ship?: ShipSnap; race?: RaceSnap; budget?: BudgetSnap; mcp?: readonly McpServerRow[]
}
/** active: keys sent and not yet resolved; lastAt: when each key was last sent (cooldown); recent: send times (rate
 *  cap); skipped: keys the cap held back and the log already said so (they are retried once the window has room). */
export type SentState = {
  active: Record<string, { at: number; text: string; ok: boolean }>
  lastAt: Record<string, number>
  recent: number[]
  skipped: string[]
}
export type NotifyPlan = { send: NotifyItem[]; cancel: string[]; dropped: NotifyItem[] }

export function emptySent(): SentState {
  return { active: {}, lastAt: {}, recent: [], skipped: [] }
}

/** Merge what the store holds over the defaults, ignoring anything of the wrong shape. */
export function normalizeSettings(raw: unknown): NotifySettings {
  const value = (raw && typeof raw === 'object' ? raw : {}) as Partial<NotifySettings>
  const cats = { ...DEFAULT_NOTIFY.cats }
  for (const name of CATEGORIES) {
    if (typeof value.cats?.[name] === 'boolean') cats[name] = value.cats[name]
  }
  return {
    on: typeof value.on === 'boolean' ? value.on : DEFAULT_NOTIFY.on,
    delaySec: (DELAYS as readonly number[]).includes(value.delaySec as number) ? (value.delaySec as number) : DEFAULT_NOTIFY.delaySec,
    cats,
  }
}

export function clipText(text: string, max = MAX_TEXT): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat
}

/** "name" of "⛔ name: reason". */
function agentOf(text: string): string {
  const rest = text.replace(/^\S+\s+/u, '')
  const cut = rest.indexOf(':')
  return (cut > 0 ? rest.slice(0, cut) : rest).trim()
}

/** What needs the person, as notifications: one item per key (a later one of the same key wins). */
export function notifyItems(snap: NotifySnapshot): NotifyItem[] {
  const items = new Map<string, NotifyItem>()
  const add = (key: string, body: string, category: Category) =>
    items.set(key, { key, text: clipText(`${snap.project}: ${body}`), category })
  // Blocked / asked: the newest event of each agent and kind. Its time is part of the key, so a new question of the
  // same agent after the old one was answered is a new notification.
  const latest = new Map<string, EventRow>()
  for (const event of snap.events) {
    if (snap.now - event.at > EVENT_WINDOW_MS) continue
    const mark = event.text.slice(0, 1)
    if (mark === '⛔' || mark === '❓') {
      latest.set(`${mark === '⛔' ? 'blocked' : 'asked'}:${agentOf(event.text)}`, event)
    } else if (mark === '⚠' || mark === '‼') {
      const reached = / hết ngân sách/.test(event.text) ? 100 : /: qua 80% ngân sách/.test(event.text) ? 80 : 0
      if (reached) add(`budget:${agentOf(event.text)}:${reached}`, event.text, 'budget')
    }
  }
  for (const [kind, event] of latest) add(`${kind}:${event.at}`, event.text, 'blocked')
  if (snap.budget?.budgetTokens) {
    const percent = (snap.budget.usedTokens / snap.budget.budgetTokens) * 100
    const reached = percent >= 100 ? 100 : percent >= 80 ? 80 : 0
    if (reached) {
      add(`budget:ticket:${snap.budget.id}:${reached}`, `${reached === 100 ? '‼ hết' : '⚠ qua 80% của'} ngân sách phiếu ${snap.budget.title} ` +
        `(${Math.round(percent)}%)`, 'budget')
    }
  }
  const ship = snap.ship
  if (ship && !ship.result) {
    for (const step of ship.stalled ?? []) {
      add(`ship:${ship.id}:stalled:${step.name}`, `⚠ Bước ${step.name} đang ghi là chạy nhưng không ai hoạt động ${step.minutes} phút`, 'ship')
    }
    for (const step of ship.escalate ?? []) {
      if (step.exhausted) add(`ship:${ship.id}:esc:${step.name}`, `‼ Bước ${step.name} đã hết thang nâng model — cần bạn quyết`, 'ship')
    }
    for (const gate of ship.gates ?? []) add(`ship:${ship.id}:gate:${gate.id}`, `❓ Bước ${gate.step} chờ bạn quyết định: ${gate.question}`, 'blocked')
  }
  if (snap.race && snap.race.status === 'judging' && snap.race.suggested && !snap.race.winner) {
    add(`race:${snap.race.id}`, `Race ${snap.race.title}: chọn bản thắng (đề xuất ${snap.race.suggested})`, 'race')
  }
  for (const row of snap.mcp ?? []) {
    if (row.status === 'needs_auth') add(`mcp:${row.name}`, `MCP ${row.name} cần đăng nhập — chạy /mcp`, 'mcp')
  }
  return [...items.values()]
}

/** What to do now: send what is new (not active, not cooling down, within the cap), cancel what resolved or was
 *  switched off; `dropped` lists what the cap refuses for the first time (it is retried on later ticks). Does not change `state` (see `applyPlan`). */
export function planNotify(items: readonly NotifyItem[], state: SentState, settings: NotifySettings, now: number): NotifyPlan {
  if (!settings.on) return { send: [], cancel: Object.keys(state.active), dropped: [] }
  const wanted = items.filter(item => settings.cats[item.category])
  const keys = new Set(wanted.map(item => item.key))
  const cancel = Object.keys(state.active).filter(key => !keys.has(key))
  let room = MAX_PER_WINDOW - state.recent.filter(at => now - at < RATE_WINDOW_MS).length
  const send: NotifyItem[] = []
  const dropped: NotifyItem[] = []
  for (const item of wanted) {
    if (state.active[item.key]) continue
    if (now - (state.lastAt[item.key] ?? -Infinity) < COOLDOWN_MS) continue
    if (room > 0) {
      send.push(item)
      room -= 1
    } else if (!state.skipped.includes(item.key)) dropped.push(item) // reported once; retried while it stays open
  }
  return { send, cancel, dropped }
}

/** The state after a plan was carried out; `ok` tells for each sent key whether the engine accepted it. */
export function applyPlan(state: SentState, plan: NotifyPlan, now: number, ok: Record<string, boolean>,
  items: readonly NotifyItem[]): SentState {
  const active = { ...state.active }
  for (const key of plan.cancel) delete active[key]
  const lastAt = { ...state.lastAt }
  for (const item of plan.send) {
    active[item.key] = { at: now, text: item.text, ok: ok[item.key] !== false }
    lastAt[item.key] = now
  }
  const seen = new Set(items.map(item => item.key))
  const sentKeys = new Set(plan.send.map(item => item.key))
  const skipped = [...state.skipped.filter(key => seen.has(key) && !sentKeys.has(key)), ...plan.dropped.map(item => item.key)]
  for (const key of Object.keys(lastAt)) if (now - lastAt[key] >= COOLDOWN_MS) delete lastAt[key]
  return { active, lastAt, recent: [...state.recent.filter(at => now - at < RATE_WINDOW_MS), ...plan.send.map(() => now)], skipped }
}

/** Options for `$.ui.notify`: with a delay it is held and dropped if the person comes back first. */
export function notifyOptions(item: NotifyItem, settings: NotifySettings):
  { title: string; key: string; delayMs?: number; untilBack?: boolean } {
  return settings.delaySec > 0
    ? { title: NOTIFY_TITLE, key: item.key, delayMs: settings.delaySec * 1000, untilBack: true }
    : { title: NOTIFY_TITLE, key: item.key }
}

/** Which work one 3 s tick does. With the pane closed, usage (ship / race / budget) is read every 60 s, and the MCP
 *  list too when its category is on, but only while notifications are on. */
export function tickWork(tick: number, paneOpen: boolean, settings: NotifySettings):
  { usage: boolean; mcp: boolean } {
  const usageEvery = tick % 5 === 0
  const slow = tick % 20 === 0
  const wantsUsage = settings.on && (settings.cats.ship || settings.cats.race || settings.cats.budget)
  return {
    usage: paneOpen ? usageEvery : slow && wantsUsage,
    mcp: slow && (paneOpen || (settings.on && settings.cats.mcp)),
  }
}

/** Why a send did not happen, with the way out. */
export function failureText(reason: string): string {
  const hint = reason === 'disabled' || reason === 'no-channel' ? ' — bật thông báo của app' : ''
  return `không gửi được (${reason})${hint}`
}

/** "Bật · chờ 60 s" / "Bật · gửi ngay" / "Tắt". */
export function stateText(settings: NotifySettings): string {
  if (!settings.on) return 'Tắt'
  return `Bật · ${settings.delaySec === 0 ? 'gửi ngay' : settings.delaySec >= 120 ? `chờ ${settings.delaySec / 60} phút` : `chờ ${settings.delaySec} s`}`
}

/** What the item list depends on: when usage and MCP were read, the log's length and last line, and a 30 s bucket (the
 *  events' 15 min window moves with the clock). Equal signature, equal list: it need not be rebuilt. */
export function notifySig(usageAt: number, mcpAt: number, events: readonly EventRow[], now: number): string {
  const last = events[events.length - 1]
  return `${usageAt}|${mcpAt}|${events.length}|${last?.at}|${last?.text}|${Math.floor(now / 30_000)}`
}

export type ItemCache = { sig: string; items: NotifyItem[] }

/** The cached list when `sig` is unchanged, else a rebuilt one (`built` tells which). */
export function cachedItems(cache: ItemCache | undefined, sig: string, build: () => NotifyItem[]): { cache: ItemCache; built: boolean } {
  return cache?.sig === sig ? { cache, built: false } : { cache: { sig, items: build() }, built: true }
}
