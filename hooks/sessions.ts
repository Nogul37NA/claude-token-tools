// M5 of the "Phòng điều hành" design: every session writes a short summary into the plugin's shared `$.store`
// (one JSON file on the machine for all sessions, verified 12/11/2026), the pane lists the live ones, and a
// question drafted from an agent's trouble goes to another session with `$.session.send({ to: { sessionId } })`.
// Pure functions; register.tsx writes, reads and sends.

export const SUMMARY_PREFIX = 'session:'
export const LIVE_MS = 10 * 60_000
export const KEEP_MS = 24 * 60 * 60_000

export type SessionSummary = {
  id: string
  project: string
  status: 'working' | 'idle'
  agentsRunning: number
  problems: number
  contextPercent?: number
  usd?: number
  week?: number
  lastPrompt?: string
  lastAlert?: string
  /** 0.16: the session that opened this one (its `$.session.id()`), how deep it sits (0 = a session opened by hand),
   *  and its permission mode when known (read from an agent.spawn). */
  parent?: string
  depth?: number
  mode?: string
  /** 0.17.7: the brief it was opened with (a child), what its main loop is doing or did last, its tool calls. */
  task?: string
  doing?: string
  calls?: number
  at: number
}

// 0.16: session tree. A session opened with start_session / hand_off_to_session carries a mark in its brief, added
// by the parent's plugin; the child reads it on its first turn and writes `parent` and `depth` into its summary.
const PARENT_RE = /\[tt-parent:([^\s\]]+)\s+tầng:(\d+)\]/u

export function parentMark(parent: string, depth: number): string {
  return `[tt-parent:${parent} tầng:${depth}]`
}

export function readParent(text: string): { parent: string; depth: number } | undefined {
  const found = PARENT_RE.exec(text)
  return found ? { parent: found[1], depth: Number(found[2]) } : undefined
}

// 0.17.6: the brief is never rewritten (auto mode refuses a call a hook changed after its review, seen 12/11/2026).
// The parent keeps `pending-child:<n>` = { parent, depth, brief } once the app started the session; the child
// finds the entry whose brief its first prompt contains, takes it and deletes it.
export const PENDING_PREFIX = 'pending-child:'
export const PENDING_MS = 30 * 60_000

export type PendingChild = { parent: string; depth: number; brief: string; at: number }

/** The first 160 characters of a brief, spaces folded: what the child's first prompt is matched on. */
export function briefKey(text: string): string {
  return stripMarks(text).replace(/\s+/g, ' ').trim().slice(0, 160)
}

/** The key of the pending entry the first prompt `text` belongs to (newest first), if any. */
export function matchPending(entries: [string, unknown][], text: string, now: number): string | undefined {
  const flat = text.replace(/\s+/g, ' ')
  return entries
    .filter(([key, value]) => key.startsWith(PENDING_PREFIX) && typeof (value as PendingChild)?.brief === 'string')
    .map(([key, value]) => [key, value as PendingChild] as const)
    .filter(([, value]) => now - value.at <= PENDING_MS && value.brief.length >= 20 && flat.includes(value.brief))
    .sort((a, b) => b[1].at - a[1].at)[0]?.[0]
}

export type SessionLimits ={ max_depth: number; max_children: number }
export const DEFAULT_LIMITS: SessionLimits = { max_depth: 2, max_children: 4 }

/** Why this session may not open one more (undefined: it may). */
export function spawnVerdict(depth: number, children: number, limits: SessionLimits): string | undefined {
  if (depth + 1 > limits.max_depth) {
    return `Phiên này đã ở tầng ${depth}; trần là ${limits.max_depth} tầng phiên con ([sessions] max_depth). ` +
      'Giao việc cho subagent trong phiên này thay vì mở phiên mới.'
  }
  if (children >= limits.max_children) {
    return `Phiên này đã có ${children} phiên con đang mở; trần là ${limits.max_children} ([sessions] max_children). ` +
      'Chờ một phiên con xong, hoặc giao cho subagent.'
  }
  return undefined
}

/** The live sessions in tree order: each root (no live parent), then its children under it, `level` deep. */
export function sessionTree(live: SessionSummary[]): { entry: SessionSummary; level: number }[] {
  const ids = new Set(live.map(entry => entry.id))
  const out: { entry: SessionSummary; level: number }[] = []
  const seen = new Set<string>()
  const walk = (entry: SessionSummary, level: number) => {
    if (seen.has(entry.id)) return
    seen.add(entry.id)
    out.push({ entry, level })
    for (const child of live.filter(other => other.parent === entry.id)) walk(child, level + 1)
  }
  for (const root of live.filter(entry => !entry.parent || !ids.has(entry.parent))) walk(root, 0)
  for (const entry of live) walk(entry, 0) // a loop of parents: shown flat rather than lost
  return out
}

/** $ of a session and every live session under it. */
export function treeUsd(live: SessionSummary[], id: string, seen: Set<string> = new Set()): number {
  if (seen.has(id)) return 0
  seen.add(id)
  const own = live.find(entry => entry.id === id)?.usd ?? 0
  return own + live.filter(entry => entry.parent === id).reduce((sum, child) => sum + treeUsd(live, child.id, seen), 0)
}

/** A child whose permission mode differs from its parent's: messages between the two are held by Claude Code. */
export function modeClash(entry: SessionSummary, live: SessionSummary[]): string | undefined {
  const parent = live.find(other => other.id === entry.parent)
  if (!entry.mode || !parent?.mode || entry.mode === parent.mode) return undefined
  return `khác chế độ quyền với phiên cha (${entry.mode} ≠ ${parent.mode}): tin nhắn giữa hai phiên sẽ bị giữ lại`
}

// 0.16: questions with a code. A message leaving this session gets `[q:<code> từ:<address>]` unless it already
// carries a mark; the answer starts with `[re:<code>]`, and the Hộp thư pairs the two. The address is
// `<sessionId>` or `<sessionId>/<agentId>`: where the answer goes.
const MARK_RE = /\[(q|re):([a-z0-9]{4,8})(?:\s+từ:([^\s\]]+))?\]/u

export type Mark = { kind: 'q' | 're'; code: string; from?: string }

export function readMark(text: string): Mark | undefined {
  const found = MARK_RE.exec(text)
  return found ? { kind: found[1] as 'q' | 're', code: found[2], from: found[3] } : undefined
}

export function newCode(seed: number = Date.now(), salt: number = Math.random()): string {
  return ((seed % 1_679_616) ^ Math.floor(salt * 1_679_616)).toString(36).padStart(4, '0').slice(-4)
}

export function address(sessionId: string, agentId?: string): string {
  return agentId ? `${sessionId}/${agentId}` : sessionId
}

/** `{ sessionId }` or `{ agentId }` for an answer to `from`, seen from session `self`; undefined: nowhere to send. */
export function replyTarget(from: string | undefined, self: string): { sessionId: string } | { agentId: string } | undefined {
  if (!from) return undefined
  const [session, agent] = from.split('/')
  if (session !== self) return { sessionId: session }
  return agent ? { agentId: agent } : undefined
}

/** The text with a question mark and the line asking for the code back; text that has a mark is left as it is. */
export function withQuestion(text: string, code: string, from: string): string {
  if (readMark(text)) return text
  return `[q:${code} từ:${from}] ${text}\n(Trả lời: mở đầu bằng [re:${code}] để bên hỏi ghép được câu trả lời.)`
}

/** The text without its marks, for the pane. */
export function stripMarks(text: string): string {
  return text.replace(MARK_RE, '').replace(/\n\(Trả lời: mở đầu bằng \[re:[a-z0-9]+\][^)]*\)/u, '')
    .replace(PARENT_RE, '').trim()
}

export type { InboxItem } from '../types'

/** The folder's last part: `C:\Users\x\projects\shop_be` → `shop_be`. */
export function projectName(cwd: string): string {
  return cwd.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || cwd || '?'
}

function clip(text: string, max: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat
}

/** The text without the app's `<system-reminder>` blocks (a child's first prompt opens with one, seen 12/11/2026). */
export function stripReminders(text: string): string {
  return text.replace(/<system-reminder>[\s\S]*?(?:<\/system-reminder>|$)/g, ' ')
}

export function promptHint(text: string): string {
  return clip(stripReminders(text), 80)
}

/** Sessions heard from in the last LIVE_MS, the most recent first; this one excluded when `self` is given. */
export function liveSessions(all: SessionSummary[], now: number, self?: string): SessionSummary[] {
  return all.filter(entry => now - entry.at <= LIVE_MS && entry.id !== self).sort((a, b) => b.at - a.at)
}

/** Keys of summaries (and of the older per-session cost notes) not touched for KEEP_MS. */
export function staleKeys(entries: [string, unknown][], now: number): string[] {
  return entries.filter(([key, value]) => {
    if (!key.startsWith(SUMMARY_PREFIX) && !key.startsWith('cc-cost:') && !key.startsWith(PENDING_PREFIX)) return false
    const record = value as { at?: number | string } | undefined
    const at = typeof record?.at === 'number' ? record.at : Date.parse(String(record?.at ?? ''))
    return !Number.isFinite(at) || now - at > KEEP_MS
  }).map(([key]) => key)
}

function ago(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.round(seconds / 60)
  return minutes < 60 ? `${minutes}p` : `${Math.floor(minutes / 60)}g${minutes % 60}p`
}

/** One line for the "Tất cả phiên" list. */
export function summaryLine(entry: SessionSummary, now: number): string {
  const parts = [
    entry.status === 'working' ? 'đang làm' : 'rảnh',
    entry.agentsRunning ? `${entry.agentsRunning} agent chạy` : '',
    entry.contextPercent !== undefined ? `context ${Math.round(entry.contextPercent)}%` : '',
    entry.usd !== undefined ? `$${entry.usd.toFixed(2)}` : '',
    entry.problems ? `${entry.problems} lỗi/bị chặn` : '',
    `${ago(now - entry.at)} trước`,
  ]
  return parts.filter(Boolean).join(' · ')
}

/** The question drafted from an agent's trouble, for the person to edit before it goes. */
export function draftQuestion(input: {
  project: string; agent: string; type: string; doing?: string; problem?: string; recent: string[]; sessionId: string
}): string {
  const lines = [
    `Hỏi từ phiên ${input.project} (${input.sessionId.slice(0, 8)}): ` +
      (input.type === 'main' ? 'phiên chính đang gặp vấn đề.' : `agent "${input.agent}" (${input.type}) đang gặp vấn đề.`),
    input.doing ? `Đang làm: ${input.doing}` : '',
    input.problem ? `Vấn đề: ${input.problem}` : '',
    input.recent.length ? `Các bước gần nhất:\n${input.recent.map(step => `- ${step}`).join('\n')}` : '',
    'Bạn có gợi ý cách xử lý không? Trả lời ngắn gọn.',
  ]
  return lines.filter(Boolean).join('\n')
}
