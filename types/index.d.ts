export type AgentRow = {
  id: string
  name: string
  type: string
  model: string
  status: string
  parentId?: string
  startedAt: number
  lastAt: number
  calls: number
  errors: number
  /** M3: the role its type maps to (undefined: not restricted), and what the role check did to its calls. */
  role?: string
  asked?: number
  blocked?: number
  /** 0.12.0: the main context (tokens) when the agent was handed its job; true when doing it in the main thread
   *  would have been cheaper than the agent's start (fewer calls than the break-even). */
  mainContext?: number
  cheaperSelf?: boolean
  /** 0.15.0: where the agent type is defined: project | user | plugin | builtin */
  level?: string
  running?: string
  last?: string
  history?: CallRow[]
}

export type CallRow = { at: number; text: string; ok: boolean; ms: number }

export type EventRow = { at: number; text: string }

/** M5: a message another session sent here, kept for the pane's "Hộp thư". */
export type InboxItem = {
  at: number; text: string; from: string; read: boolean
  /** 0.16: the question's code and where to answer it (`[q:…]`), or the code this answers (`[re:…]`); the agent
   *  of this session it was delivered to, when not the main conversation. */
  code?: string; replyTo?: string; fromAddress?: string; toAgent?: string; answered?: boolean
}

/** 0.16: a question that left this session, and its answer once one came back with the same code. */
export type QuestionRow = { code: string; at: number; to: string; text: string; answeredAt?: number; answer?: string }

/** From `ctt agent-usage`: tokens = all processed; new = input + cache_write + output; the rest re-read from cache. */
export type Spend = {
  tokens: number; usd: number
  input?: number; output?: number; cache_read?: number; cache_write?: number; new?: number; cache_read_pct?: number
}
export type Gauge = { percent: number; resetsAt?: string }
export type BudgetSnap = {
  id: string; title: string; budgetTokens: number; usedTokens: number; p50Tokens: number; p90Tokens: number
  /** M4: the estimate's steps, each a role and its share of the budget */
  steps?: { role: string; share: number }[]
}
/** 0.17: the session's /token-tools:ship run, as `ctt agent-usage` reads it. */
export type ShipSnap = {
  id: string; title: string; stage: number; stageName: string; stages: number
  /** 0.17.1: the folder the run works in, counted as the project by the role check */
  cwd?: string
  stepsDone: number; steps: number; failed: number; running: string[]; result?: string
  check?: { actual?: number; p50?: number; ratio?: number; within_p90?: boolean }
  /** 0.20: running steps with no transcript activity for [workflow] stall_minutes; failed steps the [escalation]
   *  ladder sends to another model (or that ran out of ladder). */
  stalled?: { name: string; minutes: number }[]
  escalate?: { name: string; model?: string; effort?: string; exhausted?: boolean }[]
  /** 0.21: decision gates nobody has answered yet — a question tied to one step, which waits for the answer. */
  gates?: ShipGate[]
}
export type ShipGate = { id: string; step: string; question: string; options: string[] }
/** One row of the "MCP" section: `ctt mcp --json` merged with the engine's context breakdown. */
export type McpServerRow = {
  name: string
  source: string
  type?: string
  /** connected (schemas in context) | configured | needs_auth | disabled */
  status: string
  calls: number
  errors: number
  resultTokens: number
  lastAt?: string
  /** tool → calls */
  tools: Record<string, number>
  /** schema tokens of the tools loaded in context */
  schemaTokens: number
  loadedTools: number
}
export type McpSnap = { at: number; rows: McpServerRow[]; error?: string }
export type UsageSnap = {
  at: number
  ship?: ShipSnap
  agents: Record<string, Spend>
  total: Spend
  budget?: BudgetSnap
  fiveHour?: Gauge
  week?: Gauge
  sessionUsd?: number
  sessionStartedAt?: number
  contextPercent?: number
  error?: string
}

declare module 'claude-code' {
  interface PluginState {
    'token-tools': {
      agents: Shaped<AgentRow[]>; events: Shaped<EventRow[]>; usage: Shaped<UsageSnap>; inbox: Shaped<InboxItem[]>
      questions: Shaped<QuestionRow[]>
    }
  }
}
