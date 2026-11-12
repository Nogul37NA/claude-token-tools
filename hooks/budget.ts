// M4 of the "Phòng điều hành" design: a token budget per agent. Pure functions; register.tsx reads the figures
// (`ctt agent-usage`, `ctt permissions --json`) and enforces at `tool.check`.

export type BudgetPolicy = { roles: Record<string, number>; enforce_model: boolean; raise_share: number }
export type Step = { role: string; share: number }
export type Level = 0 | 50 | 80 | 100

/** An agent's budget in processed tokens: its role's share of the approved estimate, split evenly between the
 *  agents of that role; without an estimate (or a step for the role), the policy's cap for the role. */
export function agentBudget(
  role: string | undefined, peers: number, steps: readonly Step[] | undefined, estimateTokens: number | undefined,
  caps: Record<string, number> | undefined,
): number | undefined {
  if (!role) return undefined
  if (steps && estimateTokens) {
    const share = steps.filter(step => step.role === role).reduce((sum, step) => sum + step.share, 0)
    if (share > 0) return Math.round((estimateTokens * share) / Math.max(1, peers))
  }
  return caps?.[role]
}

export function level(used: number, budget: number): Level {
  const percent = (used / budget) * 100
  return percent >= 100 ? 100 : percent >= 80 ? 80 : percent >= 50 ? 50 : 0
}

function m(tokens: number): string {
  return tokens >= 1e6 ? `${(tokens / 1e6).toFixed(1)}M` : `${Math.round(tokens / 1e3)}k`
}

export function budgetLine(used: number, budget: number): string {
  return `${m(used)} / ${m(budget)} (${Math.round((used / budget) * 100)}%)`
}

/** The log line when an agent crosses 50, 80 or 100% of its budget. */
export function levelText(name: string, reached: Level, used: number, budget: number): string {
  const mark = reached === 100 ? '‼' : reached === 80 ? '⚠' : '◔'
  const what = reached === 100 ? 'hết ngân sách — mỗi lượt gọi tool sẽ hỏi bạn' : `qua ${reached}% ngân sách`
  return `${mark} ${name}: ${what} (${budgetLine(used, budget)})`
}

/** The policy's model alias for a role (`sonnet`) against the model an agent got (`claude-opus-5-5`). */
export function modelDiffers(policyModel: string | undefined, actual: string | undefined): boolean {
  if (!policyModel || policyModel === 'inherit' || !actual) return false
  return !actual.toLowerCase().includes(policyModel.toLowerCase())
}
