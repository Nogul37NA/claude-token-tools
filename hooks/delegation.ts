// Delegation nudges from the break-even table `ctt delegation --json` measures (0.12.0): when the main thread has
// done enough calls itself for an agent to have been cheaper, and when an agent finished in fewer calls than it
// takes to pay for its start. Pure functions; register.tsx loads the table and wires the events.

export type Thresholds = {
  main_model: string
  contexts: number[]
  /** fewest calls for delegating to pay off, per context; null: no break-even */
  sonnet: (number | null)[]
  same: (number | null)[]
  source: 'measured' | 'default'
  samples: number
  nudge: boolean
  hint_model: boolean
}

function k(tokens: number): string {
  return tokens >= 1e6 ? `${(tokens / 1e6).toFixed(1)}M` : `${Math.round(tokens / 1e3)}k`
}

function modelName(model: string): string {
  return model.replace(/^claude-/, '').replace(/-(\d+)-(\d+)$/, ' $1.$2').replace(/-(\d+)$/, ' $1')
}

/** The table for an agent model: Sonnet's, else the main model's (an Opus or Haiku agent is costed like it). */
export function tableFor(t: Thresholds, agentModel?: string): (number | null)[] {
  return !agentModel || /sonnet/i.test(agentModel) ? t.sonnet : t.same
}

/** Fewest calls at this main context: the row of the largest listed context not above it (the first below all). */
export function minCalls(t: Thresholds, context: number, agentModel?: string): number | null {
  const table = tableFor(t, agentModel)
  let index = 0
  t.contexts.forEach((size, i) => {
    if (context >= size) index = i
  })
  return table[index] ?? null
}

export function nudgeText(calls: number, context: number, n: number): string {
  return `💡 Phiên chính đã tự làm ${calls} lượt ở context ${k(context)} — phần còn lại giao cho agent Sonnet sẽ rẻ hơn ` +
    `(giao có lời từ ${n} lượt).`
}

export function cheaperText(name: string, calls: number, context: number, n: number): string {
  return `↯ ${name}: xong sau ${calls} lượt — tự làm rẻ hơn (context chính ${k(context)}, giao có lời từ ≥ ${n} lượt)`
}

/** The optional system-prompt rule. Steady for the session (the table is read once), so the prompt cache holds. */
export function hintSection(t: Thresholds): string {
  const rows = t.contexts.map((context, i) => {
    const sonnet = t.sonnet[i]
    const same = t.same[i]
    return `context ${k(context)}: agent Sonnet từ ${sonnet ?? '—'} lượt, agent ${modelName(t.main_model)} từ ${same ?? '—'} lượt`
  })
  return [
    'Giao việc cho agent (đo từ transcript của máy này): mỗi agent khởi động tốn một phần context cố định, nên việc 1–2',
    'lượt tool thì tự làm; việc nhiều lượt hơn ngưỡng dưới đây thì giao cho agent (ưu tiên agent Sonnet) để output',
    'của tool không dồn vào context phiên chính. Ngưỡng theo context phiên chính hiện tại:',
    ...rows.map(row => `- ${row}`),
  ].join('\n')
}
