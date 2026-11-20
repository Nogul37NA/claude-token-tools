"""Was handing work to a subagent cheaper than doing it in the main thread? Measured from local transcripts.

For each subagent (<folder>/<session>/subagents/agent-<id>.jsonl) against its session's main thread:

- delegated (actual): the agent's own cost, plus the main thread writing its report into cache once and reading
  it again on every later main call;
- done in the main thread (estimate): the same number of calls, each re-reading the main context as it stood
  when the agent started, growing by what the agent's tools brought in; that growth also stays in the main
  context and is read again on every later main call; plus the agent's output, written by the main model.

Only tokens and $ are compared: an agent that got it wrong and had to redo is not seen here.
"""
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ctt.transcripts import Request, _cache_writes, _number

CHARS_PER_TOKEN = 2.5  # the same rough rule as the hand-over note (Vietnamese text runs short per token)
CONTEXTS = (100_000, 200_000, 400_000, 600_000)
HANDBACK = "SubagentHandback"


@dataclass
class AgentRun:
    session: str
    project: str
    agent: str
    model: str
    main_model: str
    calls: int
    start_context: int
    growth: int
    output: int
    report_tokens: int
    main_context: int
    later_main_calls: int
    agent_usd: float
    actual_usd: float
    self_usd: float
    first: Request
    rest: list

    @property
    def saving(self):
        return self.self_usd - self.actual_usd


def _time(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_requests(path, session, is_subagent):
    """(requests in time order, report text): each API request once (a message split over lines keeps its last)."""
    by_id = {}
    handback = last_text = ""
    from ctt.config import long_path
    with open(long_path(path), encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            message = row.get("message") or {}
            if row.get("type") != "assistant":
                continue
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == HANDBACK:
                    handback = str((block.get("input") or {}).get("message", ""))
                elif block.get("type") == "text" and block.get("text", "").strip():
                    last_text = block["text"]
            usage = message.get("usage")
            when = _time(row.get("timestamp"))
            if not isinstance(usage, dict) or when is None:
                continue
            write_5m, write_1h = _cache_writes(usage)
            key = message.get("id") or row.get("requestId") or f"{when.isoformat()}-{len(by_id)}"
            by_id[key] = Request(session, is_subagent, when, message.get("model") or "",
                                 _number(usage.get("input_tokens")), _number(usage.get("output_tokens")),
                                 _number(usage.get("cache_read_input_tokens")), write_5m, write_1h)
    # The report the main thread reads: the hand-back message, else the agent's last words.
    return sorted(by_id.values(), key=lambda request: request.timestamp), handback or last_text


def _usd(prices, model, *, read=0, write_5m=0, write_1h=0, input_tokens=0, output=0):
    price = prices.lookup(model)[0].at(input_tokens + read + write_5m + write_1h)
    return (input_tokens * price.input + output * price.output + read * price.cache_read
            + write_5m * price.cache_write_5m + write_1h * price.cache_write_1h) / 1e6


def measure(main_requests, agent_requests, report, prices, session="", project="", agent=""):
    """One AgentRun, or None when the agent made no request or nothing of the main thread came before it."""
    if not agent_requests:
        return None
    start, end = agent_requests[0].timestamp, agent_requests[-1].timestamp
    before = [request for request in main_requests if request.timestamp <= start]
    if not before:
        return None
    main_at_spawn = before[-1]
    later = [request for request in main_requests if request.timestamp > end]
    main_model = main_at_spawn.model
    calls = len(agent_requests)
    start_context = agent_requests[0].context
    growth = max(0, agent_requests[-1].context - start_context)
    output = sum(request.output_tokens for request in agent_requests)
    report_tokens = round(len(report) / CHARS_PER_TOKEN)
    agent_usd = sum(prices.cost(request)[0] for request in agent_requests)
    main_context = main_at_spawn.context
    actual = agent_usd + _usd(prices, main_model, write_1h=report_tokens, read=report_tokens * len(later))
    self_done = _usd(prices, main_model, read=round(calls * (main_context + growth / 2)), write_1h=growth,
                     output=output) + _usd(prices, main_model, read=growth * len(later))
    return AgentRun(session, project, agent, agent_requests[0].model, main_model, calls, start_context, growth, output,
                    report_tokens, main_context, len(later), agent_usd, actual, self_done, agent_requests[0],
                    agent_requests[1:])


def runs(projects_dir, prices, days=30, now=None):
    """Every subagent of the last `days` days with its main thread, measured."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    found = []
    from ctt.config import long_path
    for main_path in sorted(long_path(projects_dir).glob("*/*.jsonl")):
        agents_dir = main_path.parent / main_path.stem / "subagents"
        agent_paths = sorted(agents_dir.glob("agent-*.jsonl")) if agents_dir.is_dir() else []
        if not agent_paths:
            continue
        main_requests, _ = read_requests(main_path, main_path.stem, False)
        for path in agent_paths:
            agent_requests, report = read_requests(path, main_path.stem, True)
            if not agent_requests or agent_requests[0].timestamp < since:
                continue
            run = measure(main_requests, agent_requests, report, prices, main_path.stem, main_path.parent.name,
                          path.stem[len("agent-"):])
            if run:
                found.append(run)
    return found


MAX_CALLS = 200


def break_even(found, prices, main_model, agent_model, contexts=CONTEXTS):
    """{main context: fewest calls for delegating to pay off, or None within MAX_CALLS}, from the medians of the
    runs found: the agent's first call and later calls, what each call adds to the context, the report's size and
    how many main calls follow. Both sides are costed with the same model as in `measure`."""
    if not found:
        return {}
    first_new = statistics.median(run.first.input_tokens + run.first.cache_write for run in found)
    first_read = statistics.median(run.first.cache_read for run in found)
    later = [request for run in found for request in run.rest] or [run.first for run in found]
    call_new = statistics.median(request.input_tokens + request.cache_write for request in later)
    call_read = statistics.median(request.cache_read for request in later)
    call_out = statistics.median(request.output_tokens for request in later)
    grow = statistics.median(run.growth / run.calls for run in found)
    report_tokens = statistics.median(run.report_tokens for run in found)
    after = statistics.median(run.later_main_calls for run in found)
    start = _usd(prices, agent_model, write_5m=first_new, read=first_read, output=call_out)
    per_call = _usd(prices, agent_model, write_5m=call_new, read=call_read, output=call_out)
    report_cost = _usd(prices, main_model, write_1h=report_tokens, read=report_tokens * after)
    result = {}
    for context in contexts:
        result[context] = None
        for calls in range(1, MAX_CALLS + 1):
            delegated = start + (calls - 1) * per_call + report_cost
            growth = grow * calls
            self_done = _usd(prices, main_model, read=calls * (context + growth / 2), write_1h=growth,
                             output=call_out * calls) + _usd(prices, main_model, read=growth * after)
            if self_done >= delegated:
                result[context] = calls
                break
    return result


def _k(tokens):
    return f"{tokens / 1e6:.1f}M" if tokens >= 1e6 else f"{round(tokens / 1e3)}k"


def report(found, prices, days, main_model=None):
    """The text report: one line per agent, totals, and the break-even table (for `main_model`, else the most
    frequent main model of the runs)."""
    if not found:
        return f"Chưa có agent con nào trong {days} ngày qua — chưa đo được."
    lines = [f"Giao việc cho agent — {len(found)} agent, {days} ngày qua (chỉ so token và $; không thấy việc làm lại)",
             "",
             f"{'agent':<10} {'model':<18} {'lượt':>4} {'ctx chính':>9} {'khởi động':>9} {'đã giao':>8} "
             f"{'tự làm':>8} {'chênh':>8}"]
    for run in sorted(found, key=lambda run: run.saving):
        lines.append(f"{run.agent[:10]:<10} {run.model.replace('claude-', '')[:18]:<18} {run.calls:>4} "
                     f"{_k(run.main_context):>9} {_k(run.start_context):>9} ${run.actual_usd:>7.2f} "
                     f"${run.self_usd:>7.2f} {'+' if run.saving >= 0 else '-'}${abs(run.saving):>6.2f}")
    saved = sum(run.saving for run in found)
    wins = sum(run.saving >= 0 for run in found)
    lines += ["", f"Tổng: giao việc {'tiết kiệm' if saved >= 0 else 'tốn thêm'} ${abs(saved):.2f} · "
                  f"{wins}/{len(found)} agent có lời · khởi động trung vị "
                  f"{_k(statistics.median(run.start_context for run in found))} token context"]
    main_model = main_model or statistics.mode(run.main_model for run in found)
    lines += ["", f"Hòa vốn — phiên chính chạy {main_model.replace('claude-', '')}: việc cần ít nhất bao nhiêu lượt "
                  "thì giao mới có lời (theo context phiên chính lúc giao)"]
    agent_models = sorted({main_model, "claude-sonnet-5-5"})
    lines.append(f"{'context chính':>14} " + " ".join(f"{model.replace('claude-', ''):>16}" for model in agent_models))
    tables = {model: break_even(found, prices, main_model, model) for model in agent_models}
    for context in CONTEXTS:
        cells = []
        for model in agent_models:
            calls = tables[model].get(context)
            cells.append(f"{'không hòa vốn' if calls is None else f'≥ {calls} lượt':>16}")
        lines.append(f"{_k(context):>14} " + " ".join(cells))
    lines += ["", f"Mẫu: {len(found)} agent — ít thì ngưỡng còn thô; đo lại sau 2 tuần (GĐ6). Giá đọc cache là giả định "
                  "trong prices.toml."]
    return "\n".join(lines)


SONNET = "claude-sonnet-5-5"


def session_model(projects_dir, session_id):
    """The model of the session's latest main-thread request, or None."""
    from ctt.config import long_path
    main = next(long_path(projects_dir).glob(f"*/{session_id}.jsonl"), None)
    if main is None:
        return None
    requests, _ = read_requests(main, session_id, False)
    return requests[-1].model if requests else None


def thresholds(projects_dir, prices, policy, session_id=None, now=None):
    """What the control-room pane nudges by: for each main-context size, the fewest calls for delegating to pay
    off, to a Sonnet agent and to one on the main model. Measured when enough agents are found, else the policy's
    table. A null cell: no break-even within MAX_CALLS."""
    settings = policy.get("delegation", {})
    days = int(settings.get("days", 30))
    contexts = [int(context) for context in settings.get("contexts", CONTEXTS)]
    found = runs(projects_dir, prices, days, now)
    main_model = (session_model(projects_dir, session_id) if session_id else None) or         (statistics.mode(run.main_model for run in found) if found else None) or prices.fallback
    measured = len(found) >= int(settings.get("min_samples", 5))
    if measured:
        sonnet = break_even(found, prices, main_model, SONNET, contexts)
        same = break_even(found, prices, main_model, main_model, contexts)
        sonnet, same = [sonnet[c] for c in contexts], [same[c] for c in contexts]
    else:
        sonnet = list(settings.get("sonnet", [3, 2, 2, 2]))
        same = list(settings.get("same", [7, 6, 4, 3]))
    return {"main_model": main_model, "contexts": contexts, "sonnet": sonnet, "same": same,
            "source": "measured" if measured else "default", "samples": len(found),
            "nudge": bool(settings.get("nudge", True)), "hint_model": bool(settings.get("hint_model", False))}
