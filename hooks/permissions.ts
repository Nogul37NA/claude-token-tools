// M3 of the "Phòng điều hành" design: role permission profiles, judged at `tool.check` before a tool runs.
// Pure functions only; register.tsx loads the profile (`ctt permissions --json`) and wires the hook.
// The mod only tightens: what Claude Code already refused stays refused, and the main loop and agents with no
// role are never judged here.

export type RoleProfile = {
  write: string[]; ask_write: string[]; bash: string[]; outside: 'deny' | 'ask'
  /** false: the role's limits are switched off from the pane; the always-ask commands still ask. */
  enabled?: boolean
  /** 0.20: MCP tools the engine does not mark read-only — allow (the default, as before) | ask | deny; and the
   *  tool-name globs let through whatever `mcp` says. */
  mcp?: 'allow' | 'ask' | 'deny'
  mcp_allow?: string[]
}
export type Profile = {
  common: { bash: string[] }
  always_ask: { bash: string[]; mcp?: string[] }
  roles: Record<string, RoleProfile>
  agent_role: Record<string, string>
  /** M4: per-role token caps without an estimate, and the model each role should run (policy.toml). */
  budget?: { roles: Record<string, number>; enforce_model: boolean; raise_share: number }
  role_models?: Record<string, string>
  /** 0.16: [sessions] of policy.toml. */
  sessions?: { max_depth: number; max_children: number }
  /** 0.23: what an "ask" of these rules becomes in a session in Auto mode. */
  auto_mode?: { ask: 'ask' | 'deny' }
}
export type Verdict = { decision: 'ask' | 'deny'; reason: string }

/** 0.23: in Auto mode an "ask" from a hook was seen to reach nobody (the call ran with no prompt), so
 *  `[auto_mode] ask = "deny"` turns every ask of the role rules and of the agent budget into a refusal there.
 *  Any other mode, any other verdict, or the default setting: the verdict as it came. */
export function inAutoMode(verdict: Verdict | undefined, mode: string | undefined, profile: Profile | undefined):
  Verdict | undefined {
  if (!verdict || verdict.decision !== 'ask' || mode !== 'auto' || profile?.auto_mode?.ask !== 'deny') return verdict
  return { decision: 'deny', reason: `${verdict.reason} — phiên đang ở chế độ Auto nên không ai được hỏi: bị chặn ` +
    '([auto_mode] ask = "deny"). Dừng việc này, ghi nó vào báo cáo để người dùng tự làm.' }
}

const WRITE_TOOLS = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit'])
const SHELL_TOOLS = new Set(['Bash', 'PowerShell'])
const QUIET_TARGETS = new Set(['/dev/null', '$null', 'nul', '&1', '&2'])

/** `developer`, `myteam:developer` → its role through policy.toml's [agent_role]; undefined: not restricted. */
export function roleFor(profile: Profile, agentType: string | undefined): string | undefined {
  const name = (agentType ?? '').split(':').pop() ?? ''
  const role = profile.agent_role[name] ?? name
  return profile.roles[role] ? role : undefined
}

/** `**` any folders (none included), `*` within one folder, `?` one character; case-insensitive (Windows).
 *  A glob with no `/` is matched against the file name in any folder. */
export function globToRegExp(glob: string): RegExp {
  let source = ''
  for (let i = 0; i < glob.length; i++) {
    const char = glob[i]
    if (char === '*' && glob[i + 1] === '*') {
      if (glob[i + 2] === '/') {
        source += '(?:.*/)?'
        i += 2
      } else {
        source += '.*'
        i += 1
      }
    } else if (char === '*') source += '[^/]*'
    else if (char === '?') source += '[^/]'
    else source += char.replace(/[.+^${}()|[\]\\]/g, '\\$&')
  }
  return new RegExp(glob.includes('/') ? `^${source}$` : `(?:^|/)${source}$`, 'i')
}

export function matchesAny(path: string, globs: readonly string[]): boolean {
  return globs.some(glob => globToRegExp(glob).test(path))
}

/** The path relative to the project, `/`-separated; undefined when it lies outside the project. */
export function relativePath(path: string, root: string): string | undefined {
  const file = path.replace(/\\/g, '/')
  if (!/^(?:[A-Za-z]:)?\//.test(file)) return file.replace(/^\.\//, '')
  const base = root.replace(/\\/g, '/').replace(/\/+$/, '')
  if (!base || !file.toLowerCase().startsWith(`${base.toLowerCase()}/`)) return undefined
  return file.slice(base.length + 1)
}

export type Segment = { text: string; writesFile: boolean }

/** A segment that only sets a shell variable to a plain value: no `$(…)`, backtick or `@(…)` that could run a
 *  command (seen 12/11/2026: a reviewer's `S=C:/…/a.py` refused as a command it may not run). */
export function isAssignment(text: string): boolean {
  if (/\$\(|`|@\(|<\(/.test(text)) return false
  return /^\$?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"';|&]*)\s*$/.test(text) &&
    !/^\$?[A-Za-z_][A-Za-z0-9_]*\s*==/.test(text)
}

/** The command a variable is set from, judged in its place: `$s = py -m x` → `py -m x`, `T=$(mktemp -d)` →
 *  `mktemp -d` (0.17.8: both refused whole). Undefined for anything else, nested `$(…)` included. */
export function assignedCommand(text: string): string | undefined {
  const found = /^\$?[A-Za-z_][A-Za-z0-9_]*\s*=(?!=)\s*([\s\S]+)$/.exec(text.trim())
  if (!found) return undefined
  let value = found[1].trim()
  const wrapped = /^\$\(([^()]*)\)$/.exec(value) ?? /^`([^`]*)`$/.exec(value)
  if (wrapped) value = wrapped[1].trim()
  if (!value || /\$\(|`/.test(value) || /^["']/.test(value)) return undefined
  return value
}

/** Split a shell command on `&&`, `||`, `;`, `|` and new lines outside quotes; mark output redirected to a file. */
export function splitCommand(command: string): Segment[] {
  const segments: Segment[] = []
  let text = ''
  let writesFile = false
  let quote = ''
  const push = () => {
    // FOO=bar cmd → cmd; `T=$(mktemp -d)` is left whole for assignedCommand (its value runs a command)
    const trimmed = text.trim().replace(/^(?:[A-Za-z_][A-Za-z0-9_]*=(?!\$\()\S*\s+)+/, '')
    if (trimmed) segments.push({ text: trimmed, writesFile })
    text = ''
    writesFile = false
  }
  for (let i = 0; i < command.length; i++) {
    const char = command[i]
    if (quote) {
      if (char === quote) quote = ''
      text += char
    } else if (char === '"' || char === "'") {
      quote = char
      text += char
    } else if (char === '\n' || char === ';') push()
    else if ((char === '&' || char === '|') && command[i + 1] === char) {
      push()
      i += 1
    } else if (char === '|') push()
    else if (char === '>') {
      // `2>&1;` → target `&1`, not `&1;` (0.17.4: read as a file write)
      const target = /^>?\s*(&\d|[^\s;|&]+)/.exec(command.slice(i + 1))?.[1] ?? ''
      if (!QUIET_TARGETS.has(target.toLowerCase().replace(/^["']|["']$/g, ''))) writesFile = true
      text += char
    } else text += char
  }
  push()
  return segments
}

function startsWithCommand(segment: string, prefix: string): boolean {
  const head = prefix.toLowerCase()
  return [segment, bareProgram(segment)].some(form => {
    const text = form.toLowerCase()
    return text === head || text.startsWith(`${head} `)
  })
}

/** The segment with its program named bare: `& ".\.venv\Scripts\python.exe" -m pytest` → `python -m pytest`
 *  (0.17.4: a tester's venv python was refused although `python` is allowed). */
export function bareProgram(segment: string): string {
  const found = /^(?:&\s*)?(?:"([^"]+)"|'([^']+)'|(\S+))([\s\S]*)$/.exec(segment.trim())
  if (!found) return segment
  const program = found[1] ?? found[2] ?? found[3] ?? ''
  const name = (program.split(/[\\/]/).pop() ?? program).replace(/\.(exe|cmd|bat)$/i, '')
  return `${name}${found[4]}`
}

/** A segment that runs nothing: a plain quoted string PowerShell just prints (`"exit=$LASTEXITCODE"`). */
export function isPlainOutput(text: string): boolean {
  return /^(?:"[^"`]*"|'[^']*')$/.test(text) && !text.includes('$(')
}

function listed(globs: readonly string[]): string {
  return globs.length ? globs.join(', ') : 'không ghi file'
}

/** The role's verdict on one call, or undefined where the role allows it (or the tool is not judged here). */
export function judge(
  profile: Profile, roleName: string, tool: string, input: Record<string, unknown>, root: string | readonly string[],
  isReadOnly: boolean,
): Verdict | undefined {
  const role = profile.roles[roleName]
  if (!role) return undefined
  const outside = (reason: string): Verdict => ({ decision: role.outside, reason })
  const off = role.enabled === false
  // 0.17.1: the session's folder, and the work folder of a /token-tools:ship run when it is elsewhere.
  const roots = (typeof root === 'string' ? [root] : [...root]).filter(Boolean)
  root = roots.join(' hoặc ')
  if (WRITE_TOOLS.has(tool) && !off) {
    const path = String(input.file_path ?? input.notebook_path ?? '')
    if (!path) return undefined
    const rel = (roots.length ? roots : ['']).map(base => relativePath(path, base)).find(found => found !== undefined)
    if (rel === undefined) {
      return outside(`Vai ${roleName} không ghi ngoài thư mục project (${root || 'chưa biết thư mục project'}): ${path}`)
    }
    if (matchesAny(rel, role.ask_write)) return { decision: 'ask', reason: `Vai ${roleName}: ghi ${rel} phải hỏi` }
    if (matchesAny(rel, role.write)) return undefined
    return outside(`Vai ${roleName} chỉ ghi ${listed(role.write)} — không ghi ${rel}`)
  }
  if (tool.startsWith('mcp__')) {
    // 0.20: a read-only MCP tool always runs; a name on the always-ask list asks; the rest follows the role's `mcp`.
    if (isReadOnly) return undefined
    const short = tool.split('__').slice(2).join('__') || tool
    const risky = (profile.always_ask.mcp ?? []).find(pattern => new RegExp(pattern, 'i').test(short))
    if (risky) return { decision: 'ask', reason: `Tool MCP luôn phải hỏi (${risky}): ${short}` }
    const mode = role.mcp ?? 'allow'
    if (off || mode === 'allow' || matchesAny(tool, role.mcp_allow ?? [])) return undefined
    return { decision: mode, reason: `Vai ${roleName} ${mode === 'deny' ? 'không được gọi' : 'phải hỏi khi gọi'} tool MCP ` +
      `không phải chỉ đọc: ${tool}` }
  }
  if (!SHELL_TOOLS.has(tool)) return undefined
  const command = String(input.command ?? '')
  const always = profile.always_ask.bash.find(pattern => new RegExp(pattern, 'i').test(command))
  if (always) return { decision: 'ask', reason: `Lệnh luôn phải hỏi (${always}): ${command.slice(0, 80)}` }
  if (off || isReadOnly || role.bash.includes('*')) return undefined
  const allowed = [...profile.common.bash, ...role.bash]
  for (const segment of splitCommand(command)) {
    if (isAssignment(segment.text) || isPlainOutput(segment.text)) continue // `S=path`, `"exit=$LASTEXITCODE"`
    if (segment.writesFile) return outside(`Vai ${roleName} không ghi file bằng lệnh: ${segment.text.slice(0, 80)}`)
    const run = assignedCommand(segment.text) ?? segment.text
    if (!allowed.some(prefix => startsWithCommand(run, prefix))) {
      return outside(`Vai ${roleName} không được chạy: ${segment.text.slice(0, 80)}` +
        (role.bash.length ? ` (được: ${role.bash.join(', ')})` : ' (chỉ lệnh đọc)'))
    }
  }
  return undefined
}
