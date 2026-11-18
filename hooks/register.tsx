import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, UiCopyArgs } from 'claude-code'

import type { AgentRow, CallRow, EventRow, Gauge, InboxItem, QuestionRow, ShipGate, ShipSnap, Spend, UsageSnap } from '../types'
import { inAutoMode, judge, roleFor } from './permissions'
import type { Profile } from './permissions'
import { cheaperText, hintSection, minCalls, nudgeText } from './delegation'
import type { Thresholds } from './delegation'
import { agentBudget, budgetLine, level, levelText, modelDiffers } from './budget'
import type { Level } from './budget'
import {
  address, briefKey, DEFAULT_LIMITS, draftQuestion, matchPending, PENDING_PREFIX, liveSessions, modeClash, newCode, parentMark, projectName, promptHint, readMark,
  readParent, replyTarget, sessionTree, spawnVerdict, staleKeys, stripMarks, summaryLine, SUMMARY_PREFIX, treeUsd,
  withQuestion,
} from './sessions'
import type { PendingChild, SessionSummary } from './sessions'

// M1 of the "Phòng điều hành" design: who is working, on what, how many calls, what failed.
const PANE = 'control-room'
const MAIN = 'main'
const MAX_EVENTS = 40
const MAX_HISTORY = 20
const REFRESH_MS = 3_000

// Bump a shape tag when the stored fields change: it declines what an older version kept across a reload.
const agents = atom({ plugin: 'token-tools', key: 'agents' } as const, [], { shape: 'agents-2' })
const events = atom({ plugin: 'token-tools', key: 'events' } as const, [], { shape: 'events-1' })
const EMPTY_USAGE: UsageSnap = { at: 0, agents: {}, total: { tokens: 0, usd: 0 } }
const usage = atom({ plugin: 'token-tools', key: 'usage' } as const, EMPTY_USAGE, { shape: 'usage-1' })
const inbox = atom({ plugin: 'token-tools', key: 'inbox' } as const, [] as InboxItem[], { shape: 'inbox-1' })
const questions = atom({ plugin: 'token-tools', key: 'questions' } as const, [] as QuestionRow[], { shape: 'questions-1' })

// Usage is read from transcripts by the plugin's own `bin/ctt.py` (Python), every USAGE_EVERY ticks while
// the pane has been drawn in the last PANE_ALIVE_MS (no work when nobody looks).
// From this context fill the pane offers "Bàn giao": a fresh session reading a ≤ 2k-token note costs less than
// re-reading the whole context on every call. The button only drafts the command; the person sends it.
export const HANDOFF_AT = 60
export const HANDOFF_COMMAND = '/token-tools:handoff '

/** Draft the hand-over command in the prompt box; where no box takes it, copy it and say so in the pane's log
 *  (`$.ui.copy` / `$.ui.toast` are typed but absent on some builds, e.g. under `claude plugin test` 2.1.321). */
export async function draftHandoff($: EngineInterface, surface?: UiCopyArgs['surface']): Promise<void> {
  let isFilled = false
  try {
    isFilled = (await $.prompt.fill({ text: HANDOFF_COMMAND })).isFilled
  } catch {
    isFilled = false
  }
  if (isFilled) return
  let isCopied = false
  try {
    isCopied = (await $.ui.copy({ text: HANDOFF_COMMAND.trim(), surface })).isCopied
  } catch {
    isCopied = false
  }
  const text = isCopied
    ? `Đã chép ${HANDOFF_COMMAND.trim()} — dán vào ô nhập rồi gửi.`
    : `Gõ ${HANDOFF_COMMAND.trim()} vào ô nhập để bàn giao sang phiên mới.`
  try {
    $.ui.toast(text)
  } catch {
    // no toast on this build: the log line below still says it
  }
  const now = await $.clock.now()
  await update($, events, list => [...list, { at: now, text: `⇢ ${text}` }].slice(-MAX_EVENTS))
}

const USAGE_EVERY = 5
const PANE_ALIVE_MS = 30_000
let ticks = 0
let lastDrawnAt = 0
let refreshing = false

export function mtok(tokens: number): string {
  if (tokens >= 1e6) return `${(tokens / 1e6).toFixed(1)}M`
  return tokens >= 1e3 ? `${Math.round(tokens / 1e3)}k` : `${tokens}`
}

export function usd(value: number): string {
  return value >= 10 ? `$${value.toFixed(1)}` : `$${value.toFixed(2)}`
}

/** "1.2M xử lý · 85k mới · 93% đọc lại cache": what the model processed vs what was not a cache re-read.
 *  `short` drops the words for the cards. Figures from before the split show only the total. */
export function tokenSplit(spend: Spend, short = false): string {
  if (spend.new === undefined || spend.cache_read_pct === undefined) return `${mtok(spend.tokens)} token`
  const pct = Math.round(spend.cache_read_pct)
  return short
    ? `${mtok(spend.tokens)} (${mtok(spend.new)} mới, ${pct}% cache)`
    : `${mtok(spend.tokens)} xử lý · ${mtok(spend.new)} mới · ${pct}% đọc lại cache`
}

/** "in 3k · out 40k · ghi cache 42k · đọc cache 1.1M"; empty when the figures predate the split. */
export function tokenParts(spend: Spend): string {
  if (spend.cache_read === undefined) return ''
  return `in ${mtok(spend.input ?? 0)} · out ${mtok(spend.output ?? 0)} · ghi cache ${mtok(spend.cache_write ?? 0)}` +
    ` · đọc cache ${mtok(spend.cache_read)}`
}

/** `five_hour` / `seven_day` windows from session.usage() into the two gauges the pane shows. */
export function pickLimits(limits: readonly { kind: string; percentUsed: number; resetsAt?: string }[]): {
  fiveHour?: Gauge; week?: Gauge
} {
  const find = (kind: string) => limits.find(limit => limit.kind === kind)
  const asGauge = (limit?: { percentUsed: number; resetsAt?: string }) =>
    limit ? { percent: limit.percentUsed, resetsAt: limit.resetsAt } : undefined
  return { fiveHour: asGauge(find('five_hour')), week: asGauge(find('seven_day')) }
}

/** Parse `ctt agent-usage` JSON into the pane's snapshot fields. */
export function parseAgentUsage(stdout: string): Pick<UsageSnap, 'agents' | 'total' | 'budget' | 'ship'> {
  const data = JSON.parse(stdout) as {
    agents: Record<string, Spend>; total: Spend
    budget?: { id: string; title: string; budget_tokens: number; used_tokens: number; p50_tokens: number; p90_tokens: number
      steps?: { role: string; share: number }[] } | null
    ship?: { id: string; title: string; cwd?: string | null; stage: number; stage_name: string; stages: number; steps_done: number
      steps: number; failed: number; running: string[]; result?: string | null; check?: ShipSnap['check'] | null
      stalled?: ShipSnap['stalled']; escalate?: ShipSnap['escalate']; gates?: ShipSnap['gates'] } | null
  }
  if (!data.agents || !data.total) throw new Error('agent-usage: JSON thiếu agents / total')
  const s = data.ship
  const ship: ShipSnap | undefined = s
    ? { id: s.id, title: s.title, cwd: s.cwd ?? undefined, stage: s.stage, stageName: s.stage_name, stages: s.stages, stepsDone: s.steps_done,
        steps: s.steps, failed: s.failed, running: s.running ?? [], result: s.result ?? undefined,
        check: s.check ?? undefined, stalled: s.stalled ?? [], escalate: s.escalate ?? [], gates: s.gates ?? [] }
    : undefined
  const budget = data.budget
    ? { id: data.budget.id, title: data.budget.title, budgetTokens: data.budget.budget_tokens,
        usedTokens: data.budget.used_tokens, p50Tokens: data.budget.p50_tokens, p90Tokens: data.budget.p90_tokens,
        steps: data.budget.steps }
    : undefined
  return { agents: data.agents, total: data.total, budget, ship }
}

// 0.18.0: "Báo cáo ship" — `ctt ship-report --json` writes the page, the system's own opener shows it.
const shipReport: { busy: boolean; note?: string } = { busy: false }

/** The command that opens a file with the system's default app: explorer on Windows, open on macOS, else xdg-open. */
export function openerFor(path: string): string[] {
  if (/^[A-Za-z]:[\\/]/.test(path) || path.startsWith('\\\\')) return ['explorer.exe', path.replace(/\//g, '\\')]
  if (path.startsWith('/Users/')) return ['open', path]
  return ['xdg-open', path]
}

export async function openShipReport($: EngineInterface): Promise<void> {
  if (shipReport.busy) return
  shipReport.busy = true
  shipReport.note = undefined
  $.ui.invalidate('ui.render')
  try {
    const data = JSON.parse(await runCtt($, ['ship-report', '--json'])) as {
      page: string; totals: { runs: number; done: number; ok: number; usd: number }; advice: string[]
    }
    const t = data.totals
    shipReport.note = `${t.runs} lượt · ${t.done} đã xong · ${t.ok} đạt · $${t.usd.toFixed(2)} — ${data.page}`
    try {
      await $.process.run(openerFor(data.page), { timeoutMs: 10_000 })
    } catch {
      shipReport.note += ' (mở file này bằng trình duyệt)'
    }
  } catch (error) {
    shipReport.note = `Chưa làm được báo cáo: ${String(error).slice(0, 160)}`
  } finally {
    shipReport.busy = false
    $.ui.invalidate('ui.render')
  }
}

/** 0.21: what goes to the prompt box once a gate is answered on the pane, for the person to send: the main loop
 *  learns the decision from it (and from `ctt ship-next`). */
export function gateDraft(gate: ShipGate, answer: string): string {
  return `Quyết định ${gate.id} cho bước ${gate.step} (${gate.question}): ${answer}`
}

let gateNote = ''

/** Record the decision with `ctt ship-gate --resolve`, redraw, and draft the line that tells the main loop. */
export async function answerGate($: EngineInterface, runId: string, gate: ShipGate, answer: string): Promise<void> {
  try {
    await runCtt($, ['ship-gate', runId, '--resolve', gate.id, '--answer', answer])
    gateNote = ''
  } catch (error) {
    gateNote = `Chưa ghi được quyết định ${gate.id}: ${String(error).slice(0, 160)}`
    $.ui.invalidate('ui.render')
    return
  }
  await refreshUsage($)
  let isFilled = false
  try {
    isFilled = (await $.prompt.fill({ text: gateDraft(gate, answer) })).isFilled
  } catch {
    isFilled = false
  }
  if (!isFilled) gateNote = `Đã ghi quyết định ${gate.id}: ${answer}. Nhắn Claude "chạy tiếp" để bước ${gate.step} tiếp tục.`
  $.ui.invalidate('ui.render')
}

/** 0.20: what a /token-tools:ship run needs the person (or the main loop) to look at: steps nobody is working
 *  on, and failed steps with the model the ladder says to run them again on. */
export function shipAlerts(ship: ShipSnap): string[] {
  return [
    ...(ship.stalled ?? []).map(step => `⚠ Bước ${step.name} đang ghi là chạy nhưng không ai hoạt động ${step.minutes} phút — ` +
      'agent có thể đã dừng'),
    ...(ship.escalate ?? []).map(step => step.exhausted
      ? `‼ Bước ${step.name} đã hết thang nâng model — dừng, ghi thành việc mở`
      : `↑ Bước ${step.name} lỗi nhiều lần — chạy lại bằng ${step.model}:${step.effort}`),
  ]
}

/** One line for the pane: where the /token-tools:ship run stands. */
export function shipLine(ship: ShipSnap, budget?: { usedTokens: number; budgetTokens: number }): string {
  const parts = [
    `Quy trình: ${ship.title}`,
    `bước ${ship.stage}/${ship.stages - 1} ${ship.stageName}`,
    ship.steps ? `${ship.stepsDone}/${ship.steps} việc xong` : '',
    ship.failed ? `${ship.failed} việc lỗi` : '',
    ship.running.length ? `đang: ${ship.running.join(', ')}` : '',
    budget && budget.budgetTokens ? `ngân sách ${Math.round((budget.usedTokens / budget.budgetTokens) * 100)}%` : '',
    ship.result ? `kết quả ${ship.result === 'ok' ? 'đạt' : ship.result === 'partial' ? 'đạt một phần' : 'không đạt'}` : '',
    ship.check?.ratio !== undefined ? `thực tế ×${ship.check.ratio.toFixed(2)} P50` : '',
  ]
  return parts.filter(Boolean).join(' · ')
}

async function runCtt($: EngineInterface, args: string[]): Promise<string> {
  const script = `${$.plugin.root}/bin/ctt.py`
  let lastError = ''
  for (const python of ['py', 'python3']) {
    try {
      const ran = await $.process.run([python, '-I', script, ...args], { timeoutMs: 20_000 })
      if (ran.exitCode === 0) return ran.stdout
      lastError = ran.stderr.slice(-200)
    } catch (error) {
      lastError = String(error)
    }
  }
  throw new Error(lastError || 'không chạy được ctt')
}

/** Refresh limits, session cost and per-agent tokens. Never throws: a failure shows as a line in the pane. */
export async function refreshUsage($: EngineInterface): Promise<void> {
  if (refreshing) return
  refreshing = true
  try {
    const now = await $.clock.now()
    let next: UsageSnap = { ...(await read($, usage)), at: now, error: undefined }
    try {
      const session = await $.session.usage()
      next = { ...next, ...pickLimits(session.rateLimits), sessionUsd: session.cost?.usd,
        sessionStartedAt: session.startedAt, contextPercent: session.context?.percent }
      // Claude Code's own cost and where it counts from, kept for checking ctt's figures from outside (GĐ2).
      if (session.cost) {
        void $.store.set(`cc-cost:${await $.session.id()}`, {
          usd: session.cost.usd, startedAt: new Date(session.startedAt).toISOString(), at: new Date(now).toISOString(),
        })
      }
    } catch (error) {
      next = { ...next, error: `Chưa đọc được hạn mức: ${String(error).slice(0, 120)}` }
    }
    try {
      next = { ...next, ...parseAgentUsage(await runCtt($, ['agent-usage', '--session', await $.session.id()])) }
    } catch (error) {
      next = { ...next, error: `Chưa đọc được token từng agent: ${String(error).slice(0, 160)}` }
    }
    await update($, usage, () => next)
    await logAllLevels($)
    void writeSummary($)
  } catch {
    // The pane must never break a session: a refresh that cannot even read its own state is dropped.
  } finally {
    refreshing = false
  }
}

const STATUS_ICON: Record<string, string> = {
  pending: '…', running: '◐', waiting: '⏸', idle: '○', completed: '●', failed: '✗', killed: '■',
}
const STATUS_TEXT: Record<string, string> = {
  pending: 'chờ chạy', running: 'đang chạy', waiting: 'đang chờ', idle: 'rảnh', completed: 'xong',
  failed: 'lỗi', killed: 'đã dừng',
}

// The project folder the session runs in; paths under it are shown relative. Set at session.start.
let projectRoot = ''

// M3: the role profiles, read once per module load by `ctt permissions --json` (Python merges the defaults with
// the user's copy). Until it arrives, or when it cannot be read, nothing is restricted and the pane says why.
let profileLoad: Promise<Profile | undefined> | undefined
let profileError = ''
let showRoles = false
let showAllRoles = false

// "Xem log" opens the agent's recent calls right on its card: `$.ui.view` alone did nothing visible in the desktop
// app (12/11/2026), so the transcript switch is a second button whose outcome is written under the card.
const expanded = new Set<string>()
const viewNote = new Map<string, string>()

const STALE_MS = 10 * 60_000
const UNFINISHED = new Set(['running', 'pending', 'waiting'])

/** The status a card shows: the app's own while it lists the agent; an agent it no longer lists and that has been
 *  quiet for 10 minutes is done (its answer came before the pane could hear it, e.g. before 0.9.1). */
export function shownStatus(row: AgentRow, live: Map<string, string>, now: number): string {
  if (row.id === MAIN) return row.status
  const listed = live.get(row.id)
  if (listed) return listed
  return UNFINISHED.has(row.status) && now - row.lastAt > STALE_MS ? 'completed' : row.status
}

// "Xoá log" on a card hides what the agent did so far; lines that come after stay. Only the pane forgets: the
// transcript is untouched, and "Hiện lại" brings the lines back.
const logClearedAt = new Map<string, number>()

export function visibleLines(lines: LogLine[], clearedAt?: number): LogLine[] {
  if (clearedAt === undefined) return lines
  return lines.filter(line => line.at !== undefined && Date.parse(line.at) > clearedAt)
}

export async function clearLog($: EngineInterface, id: string): Promise<void> {
  logClearedAt.set(id, await $.clock.now())
  fullLog.delete(id)
  $.ui.invalidate('ui.render')
}

export function restoreLog($: EngineInterface, id: string): void {
  logClearedAt.delete(id)
  $.ui.invalidate('ui.render')
}

const ROLE_LISTS: [list: 'write' | 'ask_write' | 'bash', title: string, empty: string, hint: string][] = [
  ['write', 'Được ghi', 'không ghi file', 'Thêm đường dẫn, vd **/src/test/** hoặc *.md'],
  ['ask_write', 'Phải hỏi khi ghi', 'không có', 'Thêm đường dẫn phải hỏi, vd **/db/migration/**'],
  ['bash', 'Được chạy (ngoài lệnh đọc)', 'chỉ lệnh đọc', 'Thêm lệnh, vd mvn hoặc npm test'],
]

// Roles in the "Quyền hạn" panel are one summary line each; ▾ opens that role's editor, ▴ closes it.
const editingRoles = new Set<string>()

// "ghi <globs> · chạy mvn, npm · ngoài quyền chặn": a role at a glance.
export function roleSummary(role: { write: string[]; ask_write: string[]; bash: string[]; outside: string }): string {
  return `ghi ${role.write.join(', ') || 'không'}` +
    (role.ask_write.length ? ` · hỏi khi ghi ${role.ask_write.join(', ')}` : '') +
    ` · chạy ${role.bash.join(', ') || 'chỉ lệnh đọc'} · ngoài quyền ${role.outside === 'deny' ? 'chặn' : 'hỏi bạn'}`
}

const fullLog = new Set<string>()
const LOG_SHORT = 12

export type LogLine = { at?: string; kind: 'task' | 'user' | 'say' | 'call' | 'result' | 'answer'; text: string; error: boolean }
type AgentLog = { lines: LogLine[]; error?: string; loading?: boolean }
const agentLogs = new Map<string, AgentLog>()
const LOG_LINES = 30

/** Read the agent's own transcript through `ctt agent-log` (task, calls, results, final report). */
export async function loadLog($: EngineInterface, id: string): Promise<void> {
  agentLogs.set(id, { ...(agentLogs.get(id) ?? { lines: [] }), loading: true })
  $.ui.invalidate('ui.render')
  let next: AgentLog
  try {
    const data = JSON.parse(await runCtt($, ['agent-log', '--session', await $.session.id(), '--agent', id,
      '--last', String(LOG_LINES)])) as { lines: LogLine[]; error?: string }
    next = { lines: data.lines ?? [], error: data.error }
  } catch (error) {
    next = { lines: [], error: `Chưa đọc được log: ${String(error).slice(0, 160)}` }
  }
  agentLogs.set(id, next)
  $.ui.invalidate('ui.render')
}

export async function toggleLog($: EngineInterface, id: string): Promise<void> {
  if (expanded.has(id)) {
    expanded.delete(id)
    $.ui.invalidate('ui.render')
    return
  }
  expanded.add(id)
  await loadLog($, id)
}

const LOG_MARK: Record<LogLine['kind'], string> = {
  task: '▷ Việc giao:', user: '▷', say: '💬', call: '→', result: '  ↳', answer: '✔ Trả lời:',
}

/** The color of a log line: errors red, the final report green, the task bold-ish blue, the rest quiet. */
export function logTone(line: LogLine): Tone | undefined {
  if (line.error) return 'error'
  if (line.kind === 'answer') return 'success'
  if (line.kind === 'task' || line.kind === 'user') return 'suggestion'
  return undefined
}

export function logText(line: LogLine): string {
  return `${LOG_MARK[line.kind] ?? '·'} ${line.text}`
}

export async function openTranscript($: EngineInterface, id: string): Promise<void> {
  let note: string
  try {
    const shown = await $.ui.view(id === MAIN ? {} : { agentId: id })
    note = shown.deny
      ? `App không mở được transcript: ${shown.deny}`
      : id === MAIN ? 'Đã về hội thoại chính ở khung chat.' : 'Đã mở transcript của agent ở khung chat (bên trái).'
  } catch (error) {
    note = `App không mở được transcript: ${String(error).slice(0, 120)}`
  }
  viewNote.set(id, note)
  $.ui.invalidate('ui.render')
}
let lastBlock = ''
// Calls the role refused at tool.check: tool.call then sees them fail, and must not log or count them twice.
const roleDenied = new Set<string>()

let profileFailedAt = 0
const PROFILE_RETRY_MS = 60_000

/** For tests: forget the profile so the next load reads it again. */
export function resetProfile(): void {
  profileLoad = undefined
  profileFailedAt = 0
  profileError = ''
  lastBlock = ''
  lastProfile = undefined
  thresholdsLoad = undefined
  mainTurnCalls = 0
  nudge = ''
  roleError.clear()
  paused.clear()
  extraBudget.clear()
  levelSeen.clear()
  commandRegistered = false
  catalogLoad = undefined
  lastSummaryAt = 0
  lastPrompt = ''
  view = 'this'
  listCache = { at: 0, list: [] }
  asking = undefined
  editingRoles.clear()
  showRoles = false
  showAllRoles = false
  expanded.clear()
  agentLogs.clear()
  fullLog.clear()
  logClearedAt.clear()
  viewNote.clear()
}

let lastProfile: Profile | undefined

// 0.12.0: delegation nudges. The break-even table is read once per module load (`ctt delegation --json`); the
// main thread's own tool calls are counted per turn, and one nudge at most is shown per turn.
let thresholdsLoad: Promise<Thresholds | undefined> | undefined
let mainTurnCalls = 0
let nudge = ''

export function loadThresholds($: EngineInterface): Promise<Thresholds | undefined> {
  thresholdsLoad ??= (async () => {
    const data = JSON.parse(await runCtt($, ['delegation', '--json', '--session', await $.session.id()])) as Thresholds
    if (!Array.isArray(data.contexts) || !Array.isArray(data.sonnet) || !Array.isArray(data.same)) {
      throw new Error('JSON thiếu contexts / sonnet / same')
    }
    return data
  })().catch(() => undefined) // no table, no nudge: the pane says nothing rather than guess
  return thresholdsLoad
}

/** The main context in tokens, from the session's own figures; 0 when unknown. */
async function contextTokens($: EngineInterface): Promise<number> {
  try {
    const context = (await $.session.usage()).context
    return context ? Math.round((context.window * context.percent) / 100) : 0
  } catch {
    return 0
  }
}

function parseProfile(stdout: string): Profile {
  const profile = JSON.parse(stdout) as Profile
  if (!profile.roles || !profile.common || !profile.always_ask || !profile.agent_role) {
    throw new Error('JSON thiếu roles / common / always_ask / agent_role')
  }
  return profile
}

// Edits made on the pane: written by `ctt permissions-set` into the user's own permissions.toml, then used from the
// next tool call on. An error stays under the role it concerns.
const roleError = new Map<string, string>()

export async function changeRole($: EngineInterface, role: string, args: string[], label: string): Promise<void> {
  try {
    const data = JSON.parse(await runCtt($, ['permissions-set', '--role', role, ...args])) as Profile & { error?: string }
    if (data.error) throw new Error(data.error)
    const profile = parseProfile(JSON.stringify(data))
    profileLoad = Promise.resolve(profile)
    lastProfile = profile
    roleError.delete(role)
    const now = await $.clock.now()
    await update($, events, list => [...list, { at: now, text: `⚙ Quyền: ${label}` }].slice(-MAX_EVENTS))
  } catch (error) {
    roleError.set(role, `Chưa lưu được: ${String(error).replace(/^Error: /, '').slice(0, 160)}`)
  }
  $.ui.invalidate('ui.render')
}

/** Read once and kept; a failure is retried at most once a minute (Python missing, a bad user file being fixed). */
export function loadProfile($: EngineInterface): Promise<Profile | undefined> {
  if (profileLoad) return profileLoad
  if (Date.now() - profileFailedAt < PROFILE_RETRY_MS) return Promise.resolve(undefined)
  profileLoad = runCtt($, ['permissions', '--json'])
    .then(stdout => {
      const profile = parseProfile(stdout)
      profileError = ''
      lastProfile = profile
      return profile
    })
    .catch(error => {
      profileError = `Chưa đọc được hồ sơ quyền, agent chưa bị giới hạn: ${String(error).slice(0, 160)}`
      profileFailedAt = Date.now()
      profileLoad = undefined
      return undefined
    })
  return profileLoad
}

const RANK = { allow: 0, ask: 1, deny: 2 } as const

// 0.15.0: the agents this session can call and at which level (`ctt agents --cwd`), read once per module load.
export type CatalogAgent = { name: string; call: string; level: 'project' | 'user' | 'plugin'; role: string | null;
  model: string | null }
let catalogLoad: Promise<CatalogAgent[]> | undefined
export const LEVEL_TEXT: Record<string, string> = { project: 'project', user: 'người dùng', plugin: 'plugin', builtin: 'có sẵn' }

export function loadCatalog($: EngineInterface): Promise<CatalogAgent[]> {
  catalogLoad ??= (async () => {
    let cwd = projectRoot
    try {
      cwd = (await $.session.cwd()) || projectRoot
    } catch {
      // keep the session.start copy
    }
    const data = JSON.parse(await runCtt($, ['agents', '--cwd', cwd || '.', '--json'])) as { agents?: CatalogAgent[] }
    return Array.isArray(data.agents) ? data.agents : []
  })().catch(() => [])
  return catalogLoad
}

/** Where an agent type comes from: a `plugin:` prefix, else the catalog (project before user), else built in. */
export function levelOf(type: string, catalog: CatalogAgent[]): string {
  if (type.includes(':')) return 'plugin'
  return catalog.find(agent => agent.call === type)?.level ?? 'builtin'
}

// M5: sessions. Each writes its summary into the shared store (at most every SUMMARY_EVERY_MS); the pane reads
// them back (cached LIST_CACHE_MS) for "Tất cả phiên" and the recipient list of "Hỏi phiên khác".
const SUMMARY_EVERY_MS = 4_000 // 0.17.7: a parent watching its children saw them only every 10 s, often later
const LIST_CACHE_MS = 5_000
let lastSummaryAt = 0
let lastPrompt = ''
let view: 'this' | 'all' = 'this'
let listCache: { at: number; list: SessionSummary[] } = { at: 0, list: [] }
// The desktop app's own tools that open a session (Code tab, 12/11/2026).
const START_SESSION = 'mcp__ccd_session__start_session'
const HAND_OFF = 'mcp__ccd_session__hand_off_to_session'

// `to` is `s:<sessionId>` or `a:<agentId>` (an agent of this session); `replyTo` set when answering a question.
let asking: { agentId: string; text: string; to: string; result?: string; replyTo?: string } | undefined

// 0.16: where this session sits in the tree. Read from its first prompt's mark, kept in its summary across reloads.
let link: { parent?: string; depth: number; task?: string } | undefined
let myMode: string | undefined

async function ensureLink($: EngineInterface): Promise<{ parent?: string; depth: number; task?: string }> {
  if (link) return link
  try {
    const own = (await $.store.get(`${SUMMARY_PREFIX}${await $.session.id()}`)) as SessionSummary | undefined
    link = { parent: own?.parent, depth: own?.depth ?? 0, task: own?.task }
    myMode ??= own?.mode
  } catch {
    link = { depth: 0 }
  }
  return link
}

/** On a session's first turns: the parent that opened it, from the entry it left in the store (taken once). */
async function claimPending($: EngineInterface, text: string): Promise<{ parent: string; depth: number; task?: string } | undefined> {
  if (!text || (await ensureLink($)).parent) return undefined
  try {
    if ((await $.session.turns()) > 2) return undefined
    const entries: [string, unknown][] = []
    for (const key of await $.store.keys()) {
      if (key.startsWith(PENDING_PREFIX)) entries.push([key, await $.store.get(key)])
    }
    const key = matchPending(entries, text, Date.now())
    if (!key) return undefined
    const found = entries.find(([name]) => name === key)?.[1] as PendingChild
    await $.store.delete(key)
    return { parent: found.parent, depth: found.depth, task: promptHint(found.brief) }
  } catch {
    return undefined
  }
}

export async function writeSummary($: EngineInterface, force = false): Promise<void> {
  if (!force && Date.now() - lastSummaryAt < SUMMARY_EVERY_MS) return
  lastSummaryAt = Date.now()
  try {
    const id = await $.session.id()
    const place = await ensureLink($)
    const rows = await read($, agents)
    const snap = await read($, usage)
    const log = await read($, events)
    const now = Date.now()
    const main = rows.find(row => row.id === MAIN)
    const alert = [...log].reverse().find(event => /^[⛔‼✗]/u.test(event.text))
    const summary: SessionSummary = {
      id, project: projectName(projectRoot), status: main?.status === 'running' ? 'working' : 'idle',
      agentsRunning: rows.filter(row => row.id !== MAIN && UNFINISHED.has(row.status)).length,
      problems: rows.reduce((sum, row) => sum + row.errors + (row.blocked ?? 0), 0),
      contextPercent: snap.contextPercent, usd: snap.at ? snap.total.usd : undefined, week: snap.week?.percent,
      lastPrompt: lastPrompt || undefined, lastAlert: alert?.text, parent: place.parent, depth: place.depth,
      mode: myMode, task: place.task, doing: main?.running ?? main?.last, calls: main?.calls,
      // the session's own tokens and $ as Claude Code counts them, for the parent's card when ctt has not run here
      ...(snap.sessionUsd !== undefined && !snap.at ? { usd: snap.sessionUsd } : {}),
      at: now,
    }
    await $.store.set(`${SUMMARY_PREFIX}${id}`, summary)
  } catch {
    // the shared store is a convenience: a session that cannot write is just missing from the list
  }
}

/** Every summary in the shared store (cached a few seconds), and old ones removed now and then. */
export async function readSessions($: EngineInterface, fresh = false): Promise<SessionSummary[]> {
  if (!fresh && Date.now() - listCache.at < LIST_CACHE_MS) return listCache.list
  const list: SessionSummary[] = []
  try {
    for (const key of await $.store.keys()) {
      if (!key.startsWith(SUMMARY_PREFIX)) continue
      const value = (await $.store.get(key)) as SessionSummary | undefined
      if (value?.id) list.push(value)
    }
  } catch {
    return listCache.list
  }
  listCache = { at: Date.now(), list }
  return list
}

async function dropStale($: EngineInterface): Promise<void> {
  try {
    const entries: [string, unknown][] = []
    for (const key of await $.store.keys()) entries.push([key, await $.store.get(key)])
    for (const key of staleKeys(entries, Date.now())) await $.store.delete(key)
  } catch {
    // tidying only
  }
}

export async function startAsk($: EngineInterface, agentId: string): Promise<void> {
  const rows = await read($, agents)
  const row = rows.find(agent => agent.id === agentId)
  if (!row) return
  const log = await read($, events)
  const problem = [...log].reverse().find(event => event.text.includes(row.name) && /^[⛔❓✗‼⚠]/u.test(event.text))
  const id = await $.session.id()
  const others = liveSessions(await readSessions($, true), Date.now(), id)
  const place = await ensureLink($)
  const options = recipients(others, rows, { ...place, self: id }, agentId)
  asking = {
    agentId,
    to: options[0]?.value ?? '',
    text: draftQuestion({
      project: projectName(projectRoot), agent: row.name, type: row.type, doing: row.running ?? row.last,
      problem: problem?.text, recent: (row.history ?? []).slice(-5).map(call => `${call.ok ? '✓' : '✗'} ${call.text}`),
      sessionId: id,
    }),
  }
  $.ui.invalidate('ui.render')
}

export type Recipient = { value: string; label: string }

/** Where a question can go: the parent session first, then the children, the other live sessions, then this
 *  session's agents (a finished one is resumed with the message); `exclude` is the agent being asked about. */
export function recipients(others: SessionSummary[], rows: AgentRow[], place: { parent?: string; self?: string },
  exclude?: string): Recipient[] {
  const label = (entry: SessionSummary) => `${entry.project}${entry.lastPrompt ? ` — ${entry.lastPrompt}` : ''}`
  const kind = (entry: SessionSummary) => entry.id === place.parent ? 0 : place.self && entry.parent === place.self ? 1 : 2
  const prefix = ['Phiên cha · ', 'Phiên con · ', '']
  const out: Recipient[] = [...others].sort((a, b) => kind(a) - kind(b))
    .map(entry => ({ value: `s:${entry.id}`, label: `${prefix[kind(entry)]}${label(entry)}` }))
  for (const row of rows) {
    if (row.id === MAIN || row.id === exclude || row.status === 'killed') continue
    out.push({ value: `a:${row.id}`, label: `Agent ${row.name} (phiên này)` })
  }
  return out
}

/** A question to one child session, from its card. */
export async function startAskSession($: EngineInterface, sessionId: string): Promise<void> {
  asking = { agentId: MAIN, to: `s:${sessionId}`, text: '' }
  $.ui.invalidate('ui.render')
}

/** A question from the Hộp thư: answer it, back where it came from. */
export async function startReply($: EngineInterface, item: InboxItem): Promise<void> {
  const self = await $.session.id().catch(() => '')
  const target = replyTarget(item.fromAddress, self)
  if (!target || !item.code) return
  asking = {
    agentId: MAIN, replyTo: item.code,
    to: 'sessionId' in target ? `s:${target.sessionId}` : `a:${target.agentId}`,
    text: `[re:${item.code}] `,
  }
  $.ui.invalidate('ui.render')
}

async function recordQuestion($: EngineInterface, code: string, to: string, text: string): Promise<void> {
  const now = await $.clock.now()
  await update($, questions, list => list.some(row => row.code === code) ? list
    : [...list, { code, at: now, to, text: stripMarks(text).slice(0, 200) }].slice(-20))
}

/** A recipient's name for the log: an agent of this session, a session of the machine, or the address. */
async function recipientName($: EngineInterface, to: string): Promise<string> {
  const bare = to.replace(/^[sa]:/, '')
  const row = (await read($, agents)).find(agent => agent.id === bare)
  if (row) return `agent ${row.name}`
  const entry = (await readSessions($)).find(session => session.id === bare)
  return entry ? `phiên ${entry.project}` : bare.slice(0, 12)
}

export async function sendAsk($: EngineInterface): Promise<void> {
  if (!asking) return
  const { to, replyTo } = asking
  const body = asking.text
  if (!to || !body.trim() || (replyTo && !body.replace(`[re:${replyTo}]`, '').trim())) {
    asking = { ...asking, result: !to ? 'Chưa chọn nơi nhận.' : 'Câu hỏi đang trống.' }
    $.ui.invalidate('ui.render')
    return
  }
  const self = await $.session.id().catch(() => '')
  const code = replyTo ?? newCode()
  const text = replyTo
    ? (readMark(body)?.code === replyTo ? body : `[re:${replyTo}] ${body}`)
    : withQuestion(body, code, address(self))
  const toAgent = to.startsWith('a:')
  const bare = to.slice(2)
  const name = await recipientName($, to)
  let result: string
  try {
    const sent = await $.session.send({ to: toAgent ? { agentId: bare } : { sessionId: bare }, text })
    result = sent.isDelivered ? '' : `Chưa gửi được: ${sent.reason}`
  } catch (error) {
    result = `Chưa gửi được: ${String(error).slice(0, 160)}`
  }
  const now = await $.clock.now()
  if (!result) {
    if (replyTo) {
      await update($, inbox, list => list.map(item => item.code === replyTo ? { ...item, answered: true, read: true } : item))
    } else await recordQuestion($, code, name, text)
    const what = replyTo ? `câu trả lời [re:${replyTo}]` : `câu hỏi [q:${code}]`
    const note = toAgent ? '' : ' — phiên đó chỉ nhận khi hai phiên cùng chế độ quyền (vd cùng Auto); khác chế độ thì tin ' +
      'bị giữ lại rồi hết hạn'
    await update($, events, list => [...list, { at: now, text: `✉ Đã gửi ${what} tới ${name}${note}` }]
      .slice(-MAX_EVENTS))
    asking = undefined
  } else asking = { ...asking, result }
  $.ui.invalidate('ui.render')
}

// /control-room is declared at session.start; in the desktop app a new session showed it only after
// /reload-plugins (12/11/2026), so it is declared again on the first turn, and `/token-tools:control-room`
// (commands/control-room.md, listed by the app itself) opens the same pane.
let commandRegistered = false

async function ensureCommand($: EngineInterface): Promise<void> {
  if (commandRegistered) return
  try {
    await $.command.register({ name: 'control-room', description: 'Mở Phòng điều hành: agent nào đang làm gì trong phiên này' })
    commandRegistered = true
  } catch {
    // not ready yet: the next turn tries again
  }
}

async function openPane($: EngineInterface): Promise<{ text: string }> {
  await $.ui.open({ id: PANE, title: 'Phòng điều hành' })
  void refreshUsage($)
  return { text: 'Đã mở Phòng điều hành.' }
}

// M4: per-agent budgets. "Tạm dừng" refuses an agent's next calls (no API stops a running agent); "Tăng ngân sách"
// adds raise_share of its first budget; each level (50/80/100%) is logged once per agent.
const paused = new Set<string>()
const extraBudget = new Map<string, number>()
const levelSeen = new Map<string, Level>()
// An agent can do its whole job within 30 s (seen 12/11/2026: 5 calls, 171k of a 150k budget, no level logged),
// so a budgeted agent's call reads the figures again once they are 5 s old.
const BUDGET_FRESH_MS = 5_000

/** Log the level an agent has reached, once per level; returns it. */
async function logLevel($: EngineInterface, row: AgentRow, used: number, budget: number): Promise<Level> {
  const reached = level(used, budget)
  if (reached > (levelSeen.get(row.id) ?? 0)) {
    levelSeen.set(row.id, reached)
    const now = await $.clock.now()
    await update($, events, list => [...list, { at: now, text: levelText(row.name, reached, used, budget) }]
      .slice(-MAX_EVENTS))
  }
  return reached
}

/** After each refresh of the figures: the levels of every budgeted agent, even one that makes no further call. */
async function logAllLevels($: EngineInterface): Promise<void> {
  const rows = await read($, agents)
  const snap = await read($, usage)
  for (const row of rows) {
    if (row.id === MAIN) continue
    const budget = budgetFor(row, rows, snap, lastProfile)
    if (budget) await logLevel($, row, snap.agents?.[row.id]?.tokens ?? 0, budget)
  }
}
// Calls an agent must always be able to make: handing its report back, and the app's own bookkeeping.
const NEVER_HELD = new Set(['SubagentHandback'])

/** The budget an agent runs under now (with any raise), or undefined. */
export function budgetFor(row: AgentRow, rows: AgentRow[], snap: UsageSnap, profile?: Profile): number | undefined {
  const peers = rows.filter(other => other.role === row.role).length
  const base = agentBudget(row.role, peers, snap.budget?.steps, snap.budget?.budgetTokens, profile?.budget?.roles)
  return base === undefined ? undefined : base + (extraBudget.get(row.id) ?? 0)
}

export async function raiseBudget($: EngineInterface, id: string): Promise<void> {
  const rows = await read($, agents)
  const row = rows.find(agent => agent.id === id)
  const profile = await loadProfile($)
  if (!row) return
  const peers = rows.filter(other => other.role === row.role).length
  const snap = await read($, usage)
  const base = agentBudget(row.role, peers, snap.budget?.steps, snap.budget?.budgetTokens, profile?.budget?.roles)
  if (base === undefined) return
  const added = Math.round(base * (profile?.budget?.raise_share ?? 0.5))
  extraBudget.set(id, (extraBudget.get(id) ?? 0) + added)
  levelSeen.delete(id) // the levels count again against the new budget
  const now = await $.clock.now()
  await update($, events, list => [...list, { at: now, text: `＋ ${row.name}: tăng ngân sách thêm ${mtok(added)}` }]
    .slice(-MAX_EVENTS))
  $.ui.invalidate('ui.render')
}

export async function togglePause($: EngineInterface, id: string): Promise<void> {
  const row = (await read($, agents)).find(agent => agent.id === id)
  const now = await $.clock.now()
  const pausing = !paused.has(id)
  if (pausing) paused.add(id)
  else paused.delete(id)
  await update($, events, list => [...list, { at: now, text: `${pausing ? '⏸' : '▶'} ${row?.name ?? id}: ` +
    `${pausing ? 'tạm dừng — các lượt gọi tool tiếp theo bị chặn' : 'chạy tiếp'}` }].slice(-MAX_EVENTS))
  $.ui.invalidate('ui.render')
}

/** The budget's say on one call of an agent: refuse while paused, ask past 100%; logs each level once. */
async function budgetVerdict($: EngineInterface, row: AgentRow, tool: string, profile?: Profile):
  Promise<{ decision: 'ask' | 'deny'; reason: string } | undefined> {
  if (NEVER_HELD.has(tool)) return undefined
  if (paused.has(row.id)) {
    return { decision: 'deny', reason: `Agent ${row.name} đã bị tạm dừng trên Phòng điều hành: dừng việc ở đây và ` +
      'nộp báo cáo những gì đã làm (SubagentHandback).' }
  }
  // Fresh figures first: the estimate (and so the budget) is only known once agent-usage has been read.
  let snap = await read($, usage)
  if (Date.now() - snap.at > BUDGET_FRESH_MS) {
    await refreshUsage($)
    snap = await read($, usage)
  }
  const budget = budgetFor(row, await read($, agents), snap, profile)
  if (!budget) return undefined
  const used = snap.agents?.[row.id]?.tokens ?? 0
  const reached = await logLevel($, row, used, budget)
  if (reached < 100) return undefined
  return { decision: 'ask', reason: `Agent ${row.name} đã dùng hết ngân sách (${budgetLine(used, budget)}) — cho chạy ` +
    'tiếp lượt này? (Tăng ngân sách hoặc Tạm dừng trên Phòng điều hành)' }
}

/** One more call of the main thread this turn; from 2 on, nudge once if an agent would have been cheaper. */
async function countMainCall($: EngineInterface, now: number): Promise<void> {
  mainTurnCalls += 1
  if (nudge || mainTurnCalls < 2) return
  const table = await loadThresholds($)
  if (!table?.nudge) return
  const context = await contextTokens($)
  const n = context ? minCalls(table, context) : null
  if (n === null || mainTurnCalls < n) return
  nudge = nudgeText(mainTurnCalls, context, n)
  await update($, events, list => [...list, { at: now, text: nudge }].slice(-MAX_EVENTS))
}

/** An agent that answered in fewer calls (requests ≈ tool calls + 1) than its start costs: say so. */
async function judgeDelegation($: EngineInterface, id: string, now: number): Promise<void> {
  const row = (await read($, agents)).find(agent => agent.id === id)
  if (!row?.mainContext) return
  const table = await loadThresholds($)
  if (!table) return
  const n = minCalls(table, row.mainContext, row.model)
  const requests = row.calls + 1
  if (n === null || requests >= n) return
  await update($, agents, list => upsert(list, id, now, agent => ({ ...agent, cheaperSelf: true })))
  await update($, events, list =>
    [...list, { at: now, text: cheaperText(row.name, requests, row.mainContext ?? 0, n) }].slice(-MAX_EVENTS))
}

/** " · vai tester · 2 bị chặn · 1 phải hỏi" on an agent's card; empty for an agent with no role. */
export function roleMeta(row: AgentRow): string {
  if (!row.role) return ''
  const off = lastProfile?.roles[row.role]?.enabled === false ? ' (đã tắt giới hạn)' : ''
  return ` · vai ${row.role}${off}${row.blocked ? ` · ${row.blocked} bị chặn` : ''}${row.asked ? ` · ${row.asked} phải hỏi` : ''}`
}

/** The app's own bookkeeping tools (task list, wrap-up, sessions…) say nothing about the work. */
export function isInternalTool(tool: string): boolean {
  return tool.startsWith('mcp__ccd_')
}

/** `mcp__code-graph__cg_impact` → `cg_impact`; built-in names unchanged. */
export function toolLabel(tool: string): string {
  const match = /^mcp__.+?__(.+)$/.exec(tool)
  return match ? match[1] : tool
}

/** `claude-haiku-5-5` → `haiku 5.5`, `claude-opus-4-8-20260101` → `opus 4.8`. */
export function modelLabel(model: string): string {
  const match = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{6,})?$/.exec(model)
  return match ? `${match[1]} ${match[2]}${match[3] ? `.${match[3]}` : ''}` : model
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Paths under the project become relative; other long absolute paths keep their last two parts. */
export function shortenPaths(text: string, root: string): string {
  let result = text.replace(/\\/g, '/')
  const base = root.replace(/\\/g, '/').replace(/\/+$/, '')
  if (base) {
    const rootPattern = escapeRegExp(base)
    result = result
      .replace(new RegExp(`\\s-C\\s+["']?${rootPattern}["']?(?=\\s|$)`, 'gi'), '')
      .replace(new RegExp(`${rootPattern}/`, 'gi'), '')
      .replace(new RegExp(rootPattern, 'gi'), '.')
  }
  return result.replace(/(?:[A-Za-z]:)?\/(?:[^\s/"']+\/){2,}([^\s/"']+\/[^\s/"']+)/g, '…/$1')
}

/** One short line saying what a tool call targets: file, command, pattern, url… */
export function describeCall(tool: string, input: Record<string, unknown>, root = projectRoot): string {
  const pick = (key: string) => (typeof input[key] === 'string' ? (input[key] as string) : '')
  const target =
    pick('file_path') || pick('command') || pick('pattern') || pick('url') || pick('path') ||
    pick('description') || pick('query') || pick('prompt') || pick('ref')
  const flat = shortenPaths(target, root).replace(/\s+/g, ' ').trim()
  const short = flat.length > 60 ? `${flat.slice(0, 57)}…` : flat
  const label = toolLabel(tool)
  return short ? `${label} ${short}` : label
}

export function ago(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.round(seconds / 60)
  return minutes < 60 ? `${minutes}p` : `${Math.floor(minutes / 60)}g${minutes % 60}p`
}

// ---- Layout: native elements only (Box, Text, Button, Input, Select), so the pane follows the app's own fonts and
// widths. The SVG drawings of 0.6–0.8 were fixed at 380 px and the app scaled tall ones down to a thumbnail.

type Tone = 'suggestion' | 'warning' | 'error' | 'success' | 'inactive' | 'subtle' | 'claude'

const STATUS_TONE: Record<string, Tone> = {
  running: 'suggestion', waiting: 'warning', idle: 'inactive', completed: 'success', failed: 'error',
  killed: 'inactive', pending: 'inactive',
}

/** `████████░░░░░░░░` for a percent; drawn in text so it sizes with the font on every surface. */
export function meter(percent: number, cells = 14): string {
  const filled = Math.round((cells * Math.min(100, Math.max(0, percent))) / 100)
  return '█'.repeat(filled) + '░'.repeat(cells - filled)
}

export function percentTone(percent: number): Tone {
  return percent >= 90 ? 'error' : percent >= 70 ? 'warning' : 'suggestion'
}

/** The color of a log line by its mark: ⛔ ✗ red, ❓ amber, ▶ blue, ⇢ accent. */
export function eventTone(text: string): Tone | undefined {
  if (text.startsWith('⛔') || text.startsWith('✗') || text.startsWith('‼')) return 'error'
  if (/^(❓|💡|↯|⚠|◔|↪|⏸)/u.test(text)) return 'warning'
  if (text.startsWith('▶')) return 'suggestion'
  if (text.startsWith('⇢')) return 'claude'
  return undefined
}

const ATTENTION_MS = 15 * 60_000

/** What needs the person: blocked, asked or failed calls of the last 15 minutes, newest first, at most 3. */
export function attention(log: EventRow[], now: number): EventRow[] {
  return log.filter(event => now - event.at <= ATTENTION_MS && /^[⛔❓✗⚠‼]/u.test(event.text)).slice(-3).reverse()
}

export type UsageItem = { label: string; value: string; percent?: number; sub: string }

/** Plan limits, the approved estimate's budget (else context fill), the session's cost. */
export function usageItems(snap: UsageSnap, now: number): UsageItem[] {
  const resets = (gauge?: Gauge) => {
    if (!gauge?.resetsAt) return ''
    const left = Date.parse(gauge.resetsAt) - now
    return Number.isFinite(left) && left > 0 ? `đặt lại sau ${ago(left)}` : ''
  }
  const items: UsageItem[] = []
  if (snap.fiveHour) items.push({ label: 'Hạn mức 5 giờ', value: `${Math.round(snap.fiveHour.percent)}%`, percent: snap.fiveHour.percent, sub: resets(snap.fiveHour) })
  if (snap.week) items.push({ label: 'Hạn mức tuần', value: `${Math.round(snap.week.percent)}%`, percent: snap.week.percent, sub: resets(snap.week) })
  if (snap.budget) {
    const percent = (snap.budget.usedTokens / Math.max(1, snap.budget.budgetTokens)) * 100
    items.push({ label: 'Ngân sách phiếu', value: `${mtok(snap.budget.usedTokens)}/${mtok(snap.budget.budgetTokens)}`, percent,
      sub: `${snap.budget.id} · ${Math.round(percent)}%` })
  } else if (snap.contextPercent !== undefined) {
    items.push({ label: 'Context phiên', value: `${Math.round(snap.contextPercent)}%`, percent: snap.contextPercent, sub: 'chưa có phiếu ước lượng' })
  }
  // Two sources, never mixed: ctt's own figure from the transcripts, and Claude Code's, which counts from the
  // process start (sessionStartedAt) and can differ.
  const ccNote = snap.sessionUsd !== undefined ? ` · Claude Code báo ${usd(snap.sessionUsd)}` : ''
  items.push({ label: 'Phiên này (ctt)', value: usd(snap.total.usd), sub: `${tokenSplit(snap.total)}${ccNote}` })
  return items
}

/** The second line of an agent's card: status, calls, errors, role, tokens and $, last activity. */
export function cardMeta(row: AgentRow, now: number, mine?: Spend): string {
  return `${STATUS_TEXT[row.status] ?? row.status} · ${row.calls} lượt${row.errors ? ` · ${row.errors} lỗi` : ''}` +
    roleMeta(row) + (row.cheaperSelf ? ' · tự làm rẻ hơn' : '') +
    `${mine ? ` · ${tokenSplit(mine, true)} · ${usd(mine.usd)}` : ''} · ${ago(now - row.lastAt)} trước`
}

// What each `always_ask` pattern of the defaults means, found by trying it on a sample; others are shown as written.
const ALWAYS_ASK_SAMPLES: [string, string][] = [
  ['git push origin main', 'git push / commit / reset --hard / rebase / merge'],
  ['rm -rf build', 'xoá đệ quy (rm -r)'],
  ['Remove-Item x -Recurse', 'xoá đệ quy (Remove-Item -Recurse)'],
  ['find . -delete', 'find -delete / -exec'],
  ['drop table users', 'DROP / TRUNCATE bảng, database, schema'],
  ['deploy prod', 'lệnh có chữ "prod"'],
]

export function alwaysAskWords(patterns: readonly string[]): string[] {
  return patterns.map(pattern => {
    try {
      const regex = new RegExp(pattern, 'i')
      return ALWAYS_ASK_SAMPLES.find(([sample]) => regex.test(sample))?.[1] ?? `\`${pattern}\``
    } catch {
      return `\`${pattern}\``
    }
  })
}


function upsert(list: AgentRow[], id: string, now: number, change: (row: AgentRow) => AgentRow): AgentRow[] {
  const found = list.find(row => row.id === id)
  const base: AgentRow = found ?? {
    id, name: id === MAIN ? 'Phiên chính' : `agent ${id.slice(0, 6)}`, type: id === MAIN ? 'main' : '?',
    model: '', status: id === MAIN ? 'idle' : 'running', startedAt: now, lastAt: now, calls: 0, errors: 0,
  }
  const next = change(base)
  return found ? list.map(row => (row.id === id ? next : row)) : [...list, next]
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    projectRoot = e.cwd ?? ''
    void loadProfile($)
    await ensureCommand($)
    void dropStale($)
    // Redraw on a timer so "… trước" keeps moving while nothing happens. Stops when the module reloads.
    // Verified in the desktop app 11/11/2026 (store heartbeat): fires every REFRESH_MS.
    $.clock.every(REFRESH_MS, () => {
      $.ui.invalidate('ui.render')
      ticks += 1
      if (ticks % USAGE_EVERY === 0 && Date.now() - lastDrawnAt < PANE_ALIVE_MS) void refreshUsage($)
    })
    return next(e)
  })

  on('command.run', { command: 'control-room' }, async $ => openPane($))
  // The plugin's own `commands/control-room.md` is always listed; answered here, its prompt never reaches the model.
  on('command.run', { command: 'token-tools:control-room' }, async $ => openPane($))

  // The main loop's own state: working during a turn, idle (waiting for the person) between turns.
  on('turn.start', async ($, e, next) => {
    if (!e.agentId) {
      void ensureCommand($)
      const opened = readParent(e.text ?? '') ?? await claimPending($, e.text ?? '')
      lastPrompt = promptHint(stripMarks(e.text ?? ''))
      if (opened) {
        link = { task: promptHint(stripMarks(e.text ?? "")), ...opened }
        void writeSummary($, true)
      }
      mainTurnCalls = 0
      nudge = ''
      const now = await $.clock.now()
      await update($, agents, list => upsert(list, MAIN, now, row => ({ ...row, status: 'running', lastAt: now })))
    }
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const now = await $.clock.now()
    if (!e.agentId) {
      nudge = ''
      await update($, agents, list => upsert(list, MAIN, now, row => ({
        ...row, status: 'idle', running: undefined, lastAt: now,
      })))
      await writeSummary($, true)
    } else {
      // A subagent's answer: it is done. `$.agent.list()` drops finished agents, so without this the card kept
      // saying "đang chạy" long after (seen 12/11/2026).
      const id = e.agentId
      const known = (await read($, agents)).some(row => row.id === id)
      if (known) {
        await update($, agents, list => upsert(list, id, now, row => ({
          ...row, status: e.isAborted ? 'killed' : 'completed', running: undefined, lastAt: now,
        })))
        await judgeDelegation($, id, now)
      }
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('agent.spawn', async ($, e, next) => {
    if (!e.parentAgentId && e.permissionMode) myMode = e.permissionMode // the main loop's mode, which it inherits
    // [budget] enforce_model: an agent of a role starts on the role's model, whatever was asked for.
    const policy = await loadProfile($)
    const wanted = policy?.budget?.enforce_model && policy ? policy.role_models?.[roleFor(policy, e.subagentType) ?? ''] : undefined
    const spawned = await next(wanted && wanted !== 'inherit' && modelDiffers(wanted, e.model ?? '') ? { ...e, model: wanted } : e)
    if (spawned.deny !== undefined || !spawned.agentId) return spawned
    const now = await $.clock.now()
    const id = spawned.agentId
    const name = e.name || e.description || e.subagentType
    const profile = await loadProfile($)
    const role = profile ? roleFor(profile, e.subagentType) : undefined
    const agentLevel = levelOf(e.subagentType ?? '', await loadCatalog($))
    const policyModel = role ? profile?.role_models?.[role] : undefined
    if (modelDiffers(policyModel, spawned.model)) {
      await update($, events, list => [...list, { at: now, text: `↪ ${name} chạy ${modelLabel(spawned.model)}, ` +
        `chính sách vai ${role} là ${policyModel}` }].slice(-MAX_EVENTS))
    }
    const mainContext = await contextTokens($)
    await update($, agents, list => upsert(list, id, now, row => ({
      ...row, name, type: e.subagentType, model: spawned.model, status: 'running',
      parentId: e.parentAgentId, startedAt: now, lastAt: now, role, mainContext, level: agentLevel,
    })))
    await update($, events, list =>
      [...list, { at: now, text: `▶ ${name} (${e.subagentType}, ${modelLabel(spawned.model)}` +
        `${role ? `, vai ${role}` : ''}, ${LEVEL_TEXT[agentLevel] ?? agentLevel})` }].slice(-MAX_EVENTS))
    void $.ui.open({ id: PANE, title: 'Phòng điều hành' })
    return spawned
  }).catch(($, e, next) => next(e)) // a broken panel must never stop an agent from starting

  // M3: a subagent with a role is held to its profile before the tool runs. Claude Code decides first; the role
  // only tightens (allow → ask/deny, ask → deny). An ask nobody can answer is a deny, by the engine.
  on('tool.check', async ($, e, next) => {
    const base = await next(e)
    if (!e.agentId || base.decision === 'deny' || isInternalTool(e.tool)) return base
    const row = (await read($, agents)).find(agent => agent.id === e.agentId)
    if (row && paused.has(row.id) && !NEVER_HELD.has(e.tool)) { // any agent can be paused, role or not
      if (e.tool_use_id) roleDenied.add(e.tool_use_id)
      return { decision: 'deny', reason: `Agent ${row.name} đã bị tạm dừng trên Phòng điều hành: dừng việc ở đây và ` +
        'nộp báo cáo những gì đã làm (SubagentHandback).' }
    }
    if (!row?.role) return base
    const profile = await loadProfile($)
    const held = inAutoMode(await budgetVerdict($, row, e.tool, profile), myMode, profile)
    if (!profile) return held && RANK[held.decision] >= RANK[base.decision] ? held : base
    // Ask the session for its folder on each check: the module's copy from session.start was found empty in the
    // desktop app (12/11/2026, every file judged "outside the project").
    let root = projectRoot
    try {
      root = (await $.session.cwd()) || projectRoot
    } catch {
      // older engine without session.cwd: keep the session.start copy
    }
    if (root) projectRoot = root
    // A /token-tools:ship run may work in a folder other than the session's: both count as the project.
    const work = (await read($, usage)).ship?.cwd
    const byRole = inAutoMode(judge(profile, row.role, e.tool, (e.input ?? {}) as Record<string, unknown>,
      work && work !== root ? [root, work] : root, e.isReadOnly === true), myMode, profile)
    // The stricter of the role and the budget; a budget hold is logged by budgetVerdict, not as a role ⛔/❓.
    if (held && (!byRole || RANK[held.decision] > RANK[byRole.decision])) {
      if (RANK[held.decision] < RANK[base.decision]) return base
      if (held.decision === 'deny' && e.tool_use_id) roleDenied.add(e.tool_use_id)
      return held
    }
    const verdict = byRole
    // An equal ask is still answered as the role's: a hook's ask goes to a person, never to auto mode's classifier.
    if (!verdict || RANK[verdict.decision] < RANK[base.decision]) return base
    const now = await $.clock.now()
    const blocked = verdict.decision === 'deny'
    await update($, agents, list => upsert(list, row.id, now, agent => ({
      ...agent, asked: (agent.asked ?? 0) + (blocked ? 0 : 1), blocked: (agent.blocked ?? 0) + (blocked ? 1 : 0),
    })))
    const line = `${blocked ? '⛔' : '❓'} ${row.name}: ${verdict.reason}`
    if (blocked) {
      lastBlock = line
      if (e.tool_use_id) roleDenied.add(e.tool_use_id)
    }
    await update($, events, list => [...list, { at: now, text: line }].slice(-MAX_EVENTS))
    return { decision: verdict.decision, reason: verdict.reason }
  }).catch(($, e, next) => next(e)) // a broken check leaves Claude Code's own decision standing

  on('tool.call', async ($, e, next) => {
    if (isInternalTool(e.tool)) return next(e)
    const id = e.agentId ?? MAIN
    const doing = describeCall(e.tool, e as unknown as Record<string, unknown>)
    const started = await $.clock.now()
    await update($, agents, list => upsert(list, id, started, row => ({
      ...row, running: doing, lastAt: started, status: id === MAIN ? 'running' : row.status,
    })))
    const ran = await next(e)
    const ended = await $.clock.now()
    const denied = ran.deny !== undefined
    const byRole = roleDenied.delete(e.tool_use_id) // already counted and logged as ⛔ by tool.check
    const failed = !byRole && (denied || ran.isError === true)
    await update($, agents, list => upsert(list, id, ended, row => ({
      ...row, running: undefined, lastAt: ended, calls: row.calls + 1, errors: row.errors + (failed ? 1 : 0),
      last: `${doing} ${byRole ? '⛔' : failed ? '✗' : '✓'} (${ago(ended - started)})`,
      history: [...(row.history ?? []),
        { at: ended, text: byRole ? `⛔ ${doing}` : doing, ok: !failed && !byRole, ms: ended - started } as CallRow]
        .slice(-MAX_HISTORY),
    })))
    if (id === MAIN) await countMainCall($, ended)
    void writeSummary($)
    if (failed) {
      const who = (await read($, agents)).find(row => row.id === id)?.name ?? id
      const why = denied ? `bị từ chối: ${ran.deny}` : 'lỗi'
      await update($, events, list => [...list, { at: ended, text: `✗ ${who}: ${doing} — ${why}` }]
        .slice(-MAX_EVENTS))
    }
    return ran
  }).catch(($, e, next) => next(e)) // …nor a tool from running

  // Off by default ([delegation] hint_model): one steady section, so the prompt cache is not broken turn by turn.
  on('prompt.compose', async ($, e, next) => {
    const composed = await next(e)
    const table = await loadThresholds($)
    if (!table?.hint_model) return composed
    return { ...composed, sections: [...composed.sections, { id: 'token-tools:delegation', text: hintSection(table),
      scope: 'session' }] }
  }).catch(($, e, next) => next(e))

  // 0.16: a session opened from here carries the mark of where it sits; past the [sessions] caps it is refused.
  on('tool.call', { tool: [START_SESSION, HAND_OFF] }, async ($, e, next) => {
    const field = e.tool === START_SESSION ? 'prompt' : e.tool === HAND_OFF ? 'remaining_plan' : ''
    const args = e as unknown as Record<string, unknown>
    if (!field || e.agentId || typeof args[field] !== 'string' || args.proposal_id) return next(e)
    const self = await $.session.id()
    const place = await ensureLink($)
    const now = Date.now()
    const children = liveSessions(await readSessions($, true), now, self).filter(entry => entry.parent === self).length
    const limits = (await loadProfile($))?.sessions ?? DEFAULT_LIMITS
    // A hand-off moves this task to a sibling, not a child: it keeps this session's parent and depth.
    const isHandoff = field === 'remaining_plan'
    const verdict = isHandoff ? undefined : spawnVerdict(place.depth, children, limits)
    const at = await $.clock.now()
    if (verdict) {
      await update($, events, list => [...list, { at, text: `⛔ Không mở phiên con: ${verdict}` }].slice(-MAX_EVENTS))
      return { deny: verdict }
    }
    const linkTo = isHandoff ? (place.parent ? { parent: place.parent, depth: place.depth } : undefined)
      : { parent: self, depth: place.depth + 1 }
    const wanted = typeof args.permission_mode === 'string' ? args.permission_mode : undefined
    const title = typeof args.title === 'string' ? args.title : ''
    const ran = await next(e) // never rewritten: auto mode refuses a call a hook changed after its review
    // Logged once the app answered: auto mode may refuse the start (seen 12/11/2026, "Blocked by auto mode").
    const what = `${isHandoff ? 'Bàn giao sang phiên mới' : 'Mở phiên con'}${title ? `: ${title}` : ''}`
    const done = await $.clock.now()
    const answer = JSON.stringify((ran as { result?: unknown }).result ?? '').slice(0, 400)
    const refused = ran.deny !== undefined || ran.isError === true ||
      /not started|nothing was started|blocked|refused|từ chối/i.test(answer)
    if (!refused && linkTo) {
      const brief = briefKey(args[field] as string)
      try {
        await $.store.set(`${PENDING_PREFIX}${self}-${done}`, { ...linkTo, brief, at: Date.now() })
      } catch {
        // without the entry the child shows at the root of the tree; the session itself is not affected
      }
    }
    const reason = String(ran.deny ?? (/blocked by auto mode/i.test(answer)
      ? 'chế độ Auto chặn (cần bạn nói rõ cho mở phiên con)'
      : /trust/i.test(answer) ? 'cần bấm Trust repository cho thư mục đó'
      : /symbolic link|another spelling/i.test(answer) ? 'đường dẫn cwd viết khác cách hệ điều hành viết'
      : 'app từ chối'))
    const text = refused
      ? `⛔ ${what} — không mở được: ${reason.slice(0, 160)}`
      : `⑂ ${what}${isHandoff ? '' : ` (tầng ${place.depth + 1})`}` +
        `${wanted && myMode && wanted !== myMode ? ` — chế độ ${wanted} ≠ ${myMode} của phiên này: tin giữa hai phiên sẽ bị giữ` : ''}`
    await update($, events, list => [...list, { at: done, text }].slice(-MAX_EVENTS))
    return ran
  }).catch(($, e, next) => next(e))

  // 0.16: every message leaving this session (the model's SendMessage, a subagent's, the pane's) is a question with
  // a code unless it already carries a mark; the question is kept until an answer with the same code comes back.
  on('session.send', async ($, e, next) => {
    const self = await $.session.id()
    const text = withQuestion(e.text, newCode(), address(self, e.agentId))
    const sent = await next(text === e.text ? e : { ...e, text })
    const mark = readMark(text)
    try {
      if (sent.isDelivered && mark?.kind === 'q') await recordQuestion($, mark.code, await recipientName($, e.to), text)
    } catch (error) {
      $.ui.log(`token-tools: không ghi được câu hỏi đã gửi: ${String(error).slice(0, 160)}`)
    }
    return sent // never sent twice: a failure past next() is only logged
  }).catch(($, e, next) => next(e))

  on('session.receive', async ($, e, next) => {
    const kind = (e.origin as { kind?: string }).kind
    if (kind === 'peer' || kind === 'peer-send-message') {
      const now = await $.clock.now()
      const mark = readMark(e.text)
      const toAgent = e.agentId ? (await read($, agents)).find(row => row.id === e.agentId)?.name ?? e.agentId : undefined
      const self = await $.session.id().catch(() => '')
      const [fromSession, fromAgent] = (mark?.from ?? '').split('/')
      const sender = !mark?.from ? undefined
        : fromSession === self ? (fromAgent ? await recipientName($, fromAgent) : 'phiên chính')
        : `${await recipientName($, fromSession)}${fromAgent ? ' (một agent)' : ''}`
      const from = sender ?? e.event?.from ?? 'phiên khác'
      if (mark?.kind === 're') {
        await update($, questions, list => list.map(row => row.code === mark.code
          ? { ...row, answeredAt: now, answer: stripMarks(e.text).slice(0, 400) } : row))
      }
      await update($, inbox, list => [...list, {
        at: now, text: e.text, from, read: false, toAgent,
        ...(mark?.kind === 'q' ? { code: mark.code, fromAddress: mark.from } : {}),
        ...(mark?.kind === 're' ? { replyTo: mark.code } : {}),
      }].slice(-20))
      void $.ui.open({ id: PANE, title: 'Phòng điều hành' })
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Input, Select } = $.ui.resolve(e)
    const now = await $.clock.now()
    const live = new Map((await $.agent.list()).map(agent => [agent.id, agent.status as string]))
    const rows = (await read($, agents)).map(row =>
      ({ ...row, status: shownStatus(row, live, now) }))
    const log = await read($, events)
    const snap = await read($, usage)
    lastDrawnAt = Date.now()
    if (snap.at === 0) void refreshUsage($) // first drawing: fetch figures right away
    const working = rows.filter(row => row.status === 'running').length
    const problems = rows.reduce((sum, row) => sum + row.errors + (row.blocked ?? 0), 0)
    const urgent = attention(log, now)
    const shipNeeds = snap.ship && !snap.ship.result ? shipAlerts(snap.ship) : []
    const profile = showRoles ? await loadProfile($) : undefined
    const activeRoles = [...new Set(rows.map(row => row.role).filter((role): role is string => !!role))]
    const shownRoles = profile ? (showAllRoles || activeRoles.length === 0 ? Object.keys(profile.roles) : activeRoles) : []
    const context = snap.contextPercent ?? 0
    const catalog = showRoles ? await loadCatalog($) : []
    const selfId = await $.session.id().catch(() => '')
    const sessions = await readSessions($)
    const others = liveSessions(sessions, Date.now(), selfId)
    const mail = await read($, inbox)
    const unread = mail.filter(item => !item.read).length
    const asked = await read($, questions)
    const place = await ensureLink($)
    const options = asking ? recipients(others, rows, { ...place, self: selfId }, asking.agentId) : []
    if (asking?.replyTo && !options.some(option => option.value === asking?.to)) {
      options.unshift({ value: asking.to, label: `Bên hỏi (${asking.to.slice(2, 14)})` })
    }
    const liveAll = [...sessions.filter(entry => entry.id === selfId), ...others]
    const kids = others.filter(entry => entry.parent === selfId)

    return (
      <Box flexDirection="column" gap={1}>
        {/* 1. What is going on, and what needs the person */}
        <Box flexDirection="row" flexWrap="wrap" justifyContent="space-between" gap={1}>
          <Text weight="semibold" size="heading">
            {rows.length} agent · {working} đang làm
            {kids.length ? ` · ${kids.length} phiên con (${kids.filter(kid => kid.status === 'working').length} đang làm)` : ''}
          </Text>
          <Text color={problems ? 'error' : 'subtle'}>{problems ? `${problems} lỗi / bị chặn` : 'không có lỗi'}</Text>
        </Box>
        {(urgent.length > 0 || nudge || shipNeeds.length > 0) && (
          <Box key="attention" flexDirection="column" borderStyle="round" paddingX={1}
            borderColor={urgent.some(event => event.text.startsWith('⛔')) ? 'error' : 'warning'}>
            <Text weight="semibold">Cần chú ý</Text>
            {nudge && <Text color="warning" wrap="wrap">{nudge}</Text>}
            {shipNeeds.map(line => <Text color="warning" wrap="wrap">{line}</Text>)}
            {urgent.map(event => (
              <Text color={eventTone(event.text)} wrap="wrap">
                {event.text} <Text dimColor>· {ago(now - event.at)} trước</Text>
              </Text>
            ))}
          </Box>
        )}
        {snap.ship && (
          <Box key="ship" flexDirection="column" borderStyle="round" paddingX={1}
            borderColor={snap.ship.failed || snap.ship.result === 'failed' ? 'error' : snap.ship.result ? 'success' : 'claude'}>
            <Text wrap="wrap">{shipLine(snap.ship, snap.budget)}</Text>
            <Text mono dimColor size="footnote">
              {Array.from({ length: snap.ship.stages }, (_, i) => i < snap.ship!.stage || snap.ship!.result ? '●'
                : i === snap.ship!.stage ? '◐' : '○').join(' ')}
            </Text>
            {(snap.ship.gates ?? []).map(gate => (
              <Box key={`gate-${gate.id}`} flexDirection="column">
                <Text color="warning" wrap="wrap">❓ Bước {gate.step} chờ bạn quyết định: {gate.question}</Text>
                {gate.options.length > 0 ? (
                  <Box flexDirection="row" flexWrap="wrap" gap={1}>
                    {gate.options.map((option, index) => (
                      <Button key={`gate-${gate.id}-${index}`} variant={index === 0 ? 'primary' : 'secondary'}
                        onPress={() => void answerGate($, snap.ship!.id, gate, option)}>
                        {option}
                      </Button>
                    ))}
                  </Box>
                ) : (
                  <Text dimColor wrap="wrap">Trả lời Claude trong ô chat (cổng {gate.id}).</Text>
                )}
              </Box>
            ))}
            {gateNote && <Text color="warning" wrap="wrap">{gateNote}</Text>}
          </Box>
        )}
        {profileError && <Text color="warning" wrap="wrap">{profileError}</Text>}
        <Box flexDirection="row" flexWrap="wrap" gap={1} alignItems="center">
          <Button key="tab-this" variant={view === 'this' ? 'primary' : 'secondary'} onPress={() => {
            view = 'this'
            $.ui.invalidate('ui.render')
          }}>
            Phiên này
          </Button>
          <Button key="tab-all" variant={view === 'all' ? 'primary' : 'secondary'} onPress={() => {
            view = 'all'
            listCache.at = 0
            $.ui.invalidate('ui.render')
          }}>
            {`Tất cả phiên (${others.length + 1})`}
          </Button>
          {unread > 0 && <Text color="claude">✉ {unread} tin mới từ phiên khác</Text>}
        </Box>
        {asking && (
          <Box key="ask" flexDirection="column" borderStyle="round" borderColor="claude" paddingX={1}>
            <Text weight="semibold">{asking.replyTo ? `Trả lời câu hỏi [q:${asking.replyTo}]` : 'Hỏi phiên khác / agent'}</Text>
            {options.length === 0 ? (
              <Text color="warning" wrap="wrap">Không thấy phiên nào khác đang mở (có hoạt động trong 10 phút) hay agent nào khác trong phiên này.</Text>
            ) : (
              <Select key="ask-to" label="Gửi tới" value={asking.to}
                options={options}
                onSelect={value => {
                  if (asking) asking = { ...asking, to: value, result: undefined }
                  $.ui.invalidate('ui.render')
                }} />
            )}
            <Input key="ask-text" value={asking.text} rows={8} submitLabel="Gửi"
              onInput={value => { if (asking) asking = { ...asking, text: value } }}
              onSubmit={value => {
                if (asking) asking = { ...asking, text: value }
                void sendAsk($)
              }} />
            {asking.result && <Text color="error" wrap="wrap">{asking.result}</Text>}
            <Box flexDirection="row" gap={1}>
              <Button key="ask-send" variant="primary" onPress={() => void sendAsk($)}>Gửi</Button>
              <Button key="ask-cancel" variant="secondary" onPress={() => {
                asking = undefined
                $.ui.invalidate('ui.render')
              }}>
                Huỷ
              </Button>
            </Box>
          </Box>
        )}
        {mail.length > 0 && (
          <Box key="inbox" flexDirection="column" borderStyle="round" borderColor={unread ? 'claude' : 'subtle'} paddingX={1}>
            <Box flexDirection="row" justifyContent="space-between" gap={1}>
              <Text weight="semibold">Hộp thư{unread ? ` (${unread} chưa đọc)` : ''}</Text>
              {unread > 0 && (
                <Button key="inbox-read" variant="secondary" onPress={() => void update($, inbox,
                  list => list.map(item => ({ ...item, read: true })))}>
                  Đã đọc
                </Button>
              )}
            </Box>
            {[...mail].reverse().slice(0, 5).map((item, index) => {
              const body = stripMarks(item.text)
              const canReply = !!item.code && !item.answered && !!replyTarget(item.fromAddress, selfId)
              return (
                <Box key={`mail-${item.at}-${index}`} flexDirection="column">
                  <Text size="footnote" wrap="wrap" weight={item.read ? undefined : 'semibold'}>
                    <Text dimColor>
                      {ago(now - item.at)} trước · từ {item.from}{item.toAgent ? ` → agent ${item.toAgent}` : ''}
                      {item.code ? ` · hỏi [q:${item.code}]${item.answered ? ' ✓ đã trả lời' : ''}` : ''}
                      {item.replyTo ? ` · ↩ trả lời [q:${item.replyTo}]` : ''}:{' '}
                    </Text>
                    {body.length > 400 ? `${body.slice(0, 399)}…` : body}
                  </Text>
                  {canReply && (
                    <Box flexDirection="row">
                      <Button key={`reply-${item.code}`} variant="secondary" onPress={() => void startReply($, item)}>
                        Trả lời
                      </Button>
                    </Box>
                  )}
                </Box>
              )
            })}
          </Box>
        )}
        {asked.length > 0 && (
          <Box key="questions" flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1}>
            <Text weight="semibold">
              Câu hỏi đã gửi · {asked.filter(row => !row.answeredAt).length} chờ trả lời
            </Text>
            {[...asked].reverse().slice(0, 5).map(row => (
              <Text key={`q-${row.code}`} size="footnote" wrap="wrap">
                <Text color={row.answeredAt ? 'success' : 'warning'}>{row.answeredAt ? '✓' : '⏳'} </Text>
                <Text dimColor>[q:{row.code}] tới {row.to} · {ago(now - row.at)} trước: </Text>
                {row.text.length > 120 ? `${row.text.slice(0, 119)}…` : row.text}
                {row.answer ? <Text color="success">{`\n  ↩ ${row.answer.length > 300 ? `${row.answer.slice(0, 299)}…` : row.answer}`}</Text> : ''}
              </Text>
            ))}
          </Box>
        )}
        {view === 'all' && (
          <Box key="sessions" flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1}>
            <Text weight="semibold">Các phiên trên máy (có hoạt động trong 10 phút), phiên con dưới phiên cha</Text>
            {sessionTree(liveAll).map(({ entry, level: depth }) => {
              const clash = modeClash(entry, liveAll)
              const hasChildren = liveAll.some(other => other.parent === entry.id)
              return (
                <Box key={`session-${entry.id}`} flexDirection="column" marginTop={1} marginLeft={depth * 2}>
                  <Text weight="semibold" color={entry.problems ? 'warning' : undefined}>
                    {depth ? '└ ' : ''}{entry.status === 'working' ? '◐' : '○'} {entry.project}
                    {entry.id === selfId ? ' (phiên này)' : ''}{entry.mode ? <Text dimColor> · {entry.mode}</Text> : ''}
                    {entry.lastPrompt ? <Text dimColor> — {entry.lastPrompt}</Text> : ''}
                  </Text>
                  <Text dimColor size="footnote">
                    {summaryLine(entry, Date.now())}
                    {hasChildren ? ` · cả nhánh $${treeUsd(liveAll, entry.id).toFixed(2)}` : ''}
                  </Text>
                  {clash && <Text color="warning" size="footnote" wrap="wrap">⚠ {clash}</Text>}
                  {entry.lastAlert && <Text color="error" size="footnote" wrap="truncate-end">{entry.lastAlert}</Text>}
                </Box>
              )
            })}
            {others.length === 0 && <Text dimColor>Không có phiên nào khác đang mở.</Text>}
            {(() => {
              const live = liveAll
              const total = live.reduce((sum, entry) => sum + (entry.usd ?? 0), 0)
              const week = live.find(entry => entry.week !== undefined)?.week
              return <Text dimColor size="caption">Tổng $ các phiên: ${total.toFixed(2)}{week !== undefined ? ` · hạn mức tuần ${Math.round(week)}%` : ''}</Text>
            })()}
            <Text dimColor size="caption" wrap="wrap">
              Phiên con do Claude mở (start_session) được plugin đánh dấu phiên cha; mở tay từ thanh bên thì hiện ở gốc.
            </Text>
          </Box>
        )}
        {view === 'this' && (
          <Box key="this-session" flexDirection="column" gap={1}>

        {/* 0.17.7: the sessions opened from this one, beside its agents, as live as their summaries */}
        {kids.length > 0 && (
          <Box flexDirection="row" flexWrap="wrap" gap={1}>
            {kids.map(kid => (
              <Box key={`kid-${kid.id}`} flexDirection="column" borderStyle="round" paddingX={1} minWidth={44} flexGrow={1}
                borderColor={kid.problems ? 'warning' : kid.status === 'working' ? 'suggestion' : 'inactive'}>
                <Box flexDirection="row" justifyContent="space-between" gap={1}>
                  <Text weight="semibold" wrap="truncate-end" color={kid.status === 'working' ? 'suggestion' : undefined}>
                    {kid.status === 'working' ? '◐' : '○'} ⑂ {kid.task ?? kid.lastPrompt ?? kid.project}
                  </Text>
                  <Text dimColor mono>phiên con{kid.mode ? ` · ${kid.mode}` : ''}</Text>
                </Box>
                <Text dimColor wrap="wrap">
                  {summaryLine(kid, Date.now())}{kid.calls ? ` · ${kid.calls} lượt` : ''}
                </Text>
                {kid.doing && (
                  <Text color={kid.status === 'working' ? 'suggestion' : undefined} wrap="truncate-end" dimColor={kid.status !== 'working'}>
                    {kid.status === 'working' ? '▸ đang: ' : 'cuối: '}{kid.doing}
                  </Text>
                )}
                {modeClash(kid, liveAll) && <Text color="warning" size="footnote" wrap="wrap">⚠ {modeClash(kid, liveAll)}</Text>}
                {kid.lastAlert && <Text color="error" size="footnote" wrap="truncate-end">{kid.lastAlert}</Text>}
                <Box flexDirection="row" gap={1}>
                  <Button key={`ask-kid-${kid.id}`} variant="secondary" onPress={() => void startAskSession($, kid.id)}>
                    Hỏi phiên con
                  </Button>
                </Box>
              </Box>
            ))}
          </Box>
        )}

        {/* 2. The agents: one card each, wrapping into columns as the pane widens */}
        {rows.length === 0 && kids.length === 0 && <Text dimColor>Chưa có hoạt động nào. Agent sẽ hiện ở đây khi được giao việc.</Text>}
        {rows.length > 0 && (
          <Box flexDirection="row" flexWrap="wrap" gap={1}>
            {rows.map(row => {
              const tone = STATUS_TONE[row.status] ?? 'inactive'
              const open = expanded.has(row.id)
              const budget = row.id === MAIN ? undefined : budgetFor(row, rows, snap, lastProfile)
              const spent = snap.agents?.[row.id]?.tokens ?? 0
              const agentLog = open ? agentLogs.get(row.id) : undefined
              const showLog = !!agentLog && agentLog.lines.length > 0
              const cleared = logClearedAt.get(row.id)
              const lines = visibleLines(agentLog?.lines ?? [], cleared)
              const shown = fullLog.has(row.id) ? lines : lines.slice(-LOG_SHORT)
              // Open: the agent's own log when it could be read, else the calls the pane saw (up to MAX_HISTORY).
              const calls = showLog ? [] : (row.history ?? []).filter(call => cleared === undefined || call.at > cleared)
                .slice(open ? -MAX_HISTORY : -3).reverse()
              return (
                <Box key={`card-${row.id}`} flexDirection="column" borderStyle="round" borderColor={tone} paddingX={1}
                  minWidth={44} flexGrow={1}>
                  <Box flexDirection="row" justifyContent="space-between" gap={1}>
                    <Text weight="semibold" color={tone} wrap="truncate-end">
                      {STATUS_ICON[row.status] ?? '·'} {row.name}
                    </Text>
                    <Text dimColor mono>
                      {row.model ? modelLabel(row.model) : row.type}{row.level ? ` · ${LEVEL_TEXT[row.level] ?? row.level}` : ''}
                    </Text>
                  </Box>
                  <Text dimColor wrap="wrap">{cardMeta(row, now, snap.agents?.[row.id])}</Text>
                  {budget !== undefined && (
                    <Text size="footnote" color={percentTone(Math.min(100, (spent / budget) * 100))}>
                      <Text mono>{meter((spent / budget) * 100, 16)}</Text> ngân sách {budgetLine(spent, budget)}
                      {paused.has(row.id) ? ' · đã tạm dừng' : ''}
                    </Text>
                  )}
                  {row.running && <Text color="suggestion" wrap="truncate-end">▸ đang: {row.running}</Text>}
                  {calls.map(call => (
                    <Text size="footnote" wrap={open ? 'wrap' : 'truncate-end'}>
                      <Text color={call.text.startsWith('⛔') ? 'error' : call.ok ? 'success' : 'error'}>
                        {call.text.startsWith('⛔') ? '' : call.ok ? '✓ ' : '✗ '}
                      </Text>
                      {call.text} <Text dimColor>· {ago(now - call.at)} trước · chạy {ago(call.ms)}</Text>
                    </Text>
                  ))}
                  {open && agentLog?.loading && <Text dimColor size="footnote">Đang đọc log…</Text>}
                  {open && agentLog?.error && <Text color="warning" size="footnote" wrap="wrap">{agentLog.error}</Text>}
                  {showLog && (
                    <Box key={`log-${row.id}`} flexDirection="column" borderStyle="single" borderColor="subtle" paddingX={1}>
                      <Text dimColor size="caption">
                        Log của agent · {shown.length} / {lines.length} dòng
                        {cleared !== undefined ? ` · đã xoá lúc ${new Date(cleared).toLocaleTimeString('vi-VN')}` : ' gần nhất'}
                      </Text>
                      {lines.length === 0 && <Text dimColor size="footnote">Chưa có dòng mới từ lúc xoá.</Text>}
                      {shown.map(line => (
                        <Text size="footnote" color={logTone(line)} mono={line.kind === 'call'} wrap="wrap"
                          weight={line.kind === 'task' || line.kind === 'answer' ? 'semibold' : undefined}>
                          {logText(line)}
                        </Text>
                      ))}
                    </Box>
                  )}
                  {open && viewNote.get(row.id) && (
                    <Text dimColor size="caption" wrap="wrap">{viewNote.get(row.id)}</Text>
                  )}
                  <Box flexDirection="row" gap={1}>
                    {budget !== undefined && (
                      <Button key={`raise-${row.id}`} variant="secondary" onPress={() => void raiseBudget($, row.id)}>
                        Tăng ngân sách
                      </Button>
                    )}
                    {(row.id !== MAIN || others.length > 0 || rows.length > 1) && (
                      <Button key={`ask-${row.id}`} variant="secondary" onPress={() => void startAsk($, row.id)}>
                        Hỏi phiên / agent
                      </Button>
                    )}
                    {row.id !== MAIN && UNFINISHED.has(row.status) && (
                      <Button key={`pause-${row.id}`} variant="secondary" onPress={() => void togglePause($, row.id)}>
                        {paused.has(row.id) ? 'Tiếp tục' : 'Tạm dừng'}
                      </Button>
                    )}
                    <Button key={`view-${row.id}`} variant="secondary" onPress={() => void toggleLog($, row.id)}>
                      {open ? 'Thu gọn' : 'Xem log'}
                    </Button>
                    {showLog && lines.length > LOG_SHORT && (
                      <Button key={`more-${row.id}`} variant="secondary" onPress={() => {
                        if (fullLog.has(row.id)) fullLog.delete(row.id)
                        else fullLog.add(row.id)
                        $.ui.invalidate('ui.render')
                      }}>
                        {fullLog.has(row.id) ? 'Bớt' : 'Xem thêm'}
                      </Button>
                    )}
                    {open && (showLog || (row.history ?? []).length > 0) && (cleared === undefined ? (
                      <Button key={`clear-${row.id}`} variant="secondary" onPress={() => void clearLog($, row.id)}>
                        Xoá log
                      </Button>
                    ) : (
                      <Button key={`restore-${row.id}`} variant="secondary" onPress={() => restoreLog($, row.id)}>
                        Hiện lại
                      </Button>
                    ))}
                    {open && (
                      <Button key={`reload-${row.id}`} variant="secondary" onPress={() => void loadLog($, row.id)}>
                        Tải lại
                      </Button>
                    )}
                    {open && (
                      <Button key={`open-${row.id}`} variant="secondary" onPress={() => void openTranscript($, row.id)}>
                        {row.id === MAIN ? 'Về hội thoại chính' : 'Mở transcript trong app'}
                      </Button>
                    )}
                  </Box>
                </Box>
              )
            })}
          </Box>
        )}

        {/* 3. Limits and cost, one slim row */}
        {snap.at === 0 ? (
          <Text dimColor>Đang đọc số liệu chi phí…</Text>
        ) : (
          <Box flexDirection="row" flexWrap="wrap" gap={2}>
            {usageItems(snap, now).map(item => (
              // Gauges keep a fixed width so label, value and bar stay together on a wide pane; the cost item
              // takes the rest of the row for its longer line.
              <Box key={`usage-${item.label}`} flexDirection="column" minWidth={30}
                width={item.percent !== undefined ? 30 : undefined} flexGrow={item.percent !== undefined ? 0 : 1}>
                <Box flexDirection="row" justifyContent="space-between" gap={1}>
                  <Text dimColor size="footnote">{item.label}</Text>
                  <Text weight="semibold" tabular>{item.value}</Text>
                </Box>
                {item.percent !== undefined && <Text color={percentTone(item.percent)} mono>{meter(item.percent, 28)}</Text>}
                {item.sub && <Text dimColor size="caption" wrap="wrap">{item.sub}</Text>}
              </Box>
            ))}
          </Box>
        )}
        {snap.at > 0 && tokenParts(snap.total) && (
          <Text dimColor size="caption" mono wrap="wrap">Token phiên: {tokenParts(snap.total)}</Text>
        )}
        {snap.error && <Text color="warning" wrap="wrap">{snap.error}</Text>}

        {/* 4. Actions */}
        <Box flexDirection="row" flexWrap="wrap" gap={1} alignItems="center">
          {context >= HANDOFF_AT && (
            <Button key="handoff" variant="primary" onPress={() => void draftHandoff($, e.surface)}>
              Bàn giao sang phiên mới
            </Button>
          )}
          {context >= HANDOFF_AT && <Text color="warning">context {Math.round(context)}%</Text>}
          <Button key="roles" variant="secondary" onPress={() => {
            showRoles = !showRoles
            $.ui.invalidate('ui.render')
          }}>
            {showRoles ? 'Ẩn quyền hạn' : 'Quyền hạn'}
          </Button>
          {showRoles && profile && activeRoles.length > 0 && (
            <Button key="roles-all" variant="secondary" onPress={() => {
              showAllRoles = !showAllRoles
              $.ui.invalidate('ui.render')
            }}>
              {showAllRoles ? 'Chỉ vai đang chạy' : 'Xem tất cả vai'}
            </Button>
          )}
          <Button key="ship-report" variant="secondary" onPress={() => void openShipReport($)}>
            {shipReport.busy ? 'Đang làm báo cáo…' : 'Báo cáo ship'}
          </Button>
          {shipReport.note && <Text dimColor size="footnote" wrap="wrap">{shipReport.note}</Text>}
        </Box>
        {showRoles && (
          <Box key="roles-panel" flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1}>
            <Text weight="semibold">
              Quyền theo vai{profile && !showAllRoles && activeRoles.length > 0 ? ' — vai đang chạy' : ''}
            </Text>
            <Text dimColor size="footnote" wrap="wrap">
              Chỉ siết thêm quy tắc của Claude Code; phiên chính và agent không có vai không bị giới hạn. Lệnh đọc
              (git status/diff/log, grep, ls…) vai nào cũng chạy được.
            </Text>
            {!profile && <Text dimColor>{profileError || 'Đang đọc hồ sơ quyền…'}</Text>}
            {profile && shownRoles.map(name => {
              const role = profile.roles[name]
              if (!role) return null
              const off = role.enabled === false
              const editing = editingRoles.has(name)
              const agentsOf = Object.entries(profile.agent_role).filter(([, mapped]) => mapped === name)
                .map(([agent]) => agent)
              return (
                <Box key={`role-${name}`} flexDirection="column" borderStyle="single"
                  borderColor={off ? 'warning' : 'subtle'} paddingX={1} marginTop={1}>
                  <Box flexDirection="row" flexWrap="wrap" justifyContent="space-between" gap={1} alignItems="center">
                    <Text weight="semibold" color={off ? 'warning' : undefined}>
                      {name}{agentsOf.length ? ` (${agentsOf.join(', ')})` : ''}
                      {off ? ' — đã tắt giới hạn (lệnh nguy hiểm vẫn hỏi)' : ''}
                    </Text>
                    <Button key={`edit-${name}`} variant="secondary" onPress={() => {
                      if (editingRoles.has(name)) editingRoles.delete(name)
                      else editingRoles.add(name)
                      $.ui.invalidate('ui.render')
                    }}>
                      {editing ? '▴' : '▾'}
                    </Button>
                  </Box>
                  {!editing && <Text dimColor size="footnote" wrap="wrap">{roleSummary(role)}</Text>}
                  <Text size="footnote" wrap="wrap">
                    <Text dimColor>Agent gọi được: </Text>
                    {catalog.filter(agent => agent.role === name)
                      .map(agent => `${agent.call} (${LEVEL_TEXT[agent.level] ?? agent.level})`).join(', ') || 'chưa có'}
                  </Text>
                  {editing && (
                    <Box flexDirection="row" flexWrap="wrap" gap={1} alignItems="center" marginTop={1}>
                      <Select key={`outside-${name}`} label="Ngoài quyền thì" value={role.outside}
                        options={[{ value: 'deny', label: 'chặn' }, { value: 'ask', label: 'hỏi bạn' }]}
                        onSelect={value => void changeRole($, name, ['--outside', value],
                          `${name}: ngoài quyền thì ${value === 'deny' ? 'chặn' : 'hỏi bạn'}`)} />
                      <Button key={`toggle-${name}`} variant="secondary" onPress={() => void changeRole($, name,
                        ['--enabled', off ? 'true' : 'false'], `${name}: ${off ? 'bật lại' : 'tạm tắt'} giới hạn`)}>
                        {off ? 'Bật lại' : 'Tạm tắt'}
                      </Button>
                      <Button key={`reset-${name}`} variant="secondary"
                        onPress={() => void changeRole($, name, ['--reset'], `${name}: về mặc định`)}>
                        Về mặc định
                      </Button>
                    </Box>
                  )}
                  {editing && ROLE_LISTS.map(([list, title, empty, hint]) => (
                    <Box key={`list-${name}-${list}`} flexDirection="column" marginTop={1}>
                      <Text dimColor size="footnote">{title}</Text>
                      <Box flexDirection="row" flexWrap="wrap" gap={1} alignItems="center">
                        {role[list].length === 0 && <Text dimColor size="footnote">{empty}</Text>}
                        {role[list].map((value, index) => (
                          <Button key={`rm-${name}-${list}-${index}`} variant="secondary" onPress={() => void changeRole(
                            $, name, ['--remove', list, value], `${name}: bỏ ${title.toLowerCase()} ${value}`)}>
                            ✕ {value}
                          </Button>
                        ))}
                      </Box>
                      <Input key={`add-${name}-${list}`} placeholder={hint} submitLabel="Thêm"
                        onSubmit={value => void changeRole($, name, ['--add', list, value],
                          `${name}: thêm ${title.toLowerCase()} ${value.trim()}`)} />
                    </Box>
                  ))}
                  {roleError.get(name) && <Text color="error" wrap="wrap">{roleError.get(name)}</Text>}
                </Box>
              )
            })}
            {profile && (
              <Text wrap="wrap">
                <Text weight="semibold">Luôn hỏi bạn: </Text>
                {alwaysAskWords(profile.always_ask.bash).join(' · ')}
              </Text>
            )}
            <Text wrap="wrap">
              <Text weight="semibold">Lần chặn gần nhất: </Text>
              <Text color={lastBlock ? 'error' : 'subtle'}>{lastBlock || 'chưa có'}</Text>
            </Text>
            <Text dimColor size="caption" wrap="wrap">
              Lưu vào ~/.claude/claude-token-tools/permissions.toml (bản trước: .bak) · áp dụng từ lượt gọi tool kế tiếp
            </Text>
          </Box>
        )}

        {/* 5. The log, newest first, at the bottom */}
        {log.length > 0 && (
          <Box flexDirection="column">
            <Text weight="semibold" dimColor>Nhật ký</Text>
            {log.slice(-8).reverse().map(event => (
              <Text size="footnote" color={eventTone(event.text)} wrap="wrap">
                <Text dimColor>{ago(now - event.at)} trước · </Text>
                {event.text}
              </Text>
            ))}
          </Box>
        )}
          </Box>
        )}
      </Box>
    )
  })
}
