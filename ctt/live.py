"""Live per-agent usage of one session, read incrementally (for the control-room pane).

Main thread: <projects>/<folder>/<session>.jsonl; each subagent: <folder>/<session>/subagents/agent-<id>.jsonl.
Byte offsets per file are kept in <ctt_home>/live_state/<session>.json, so each call reads only new lines.
"""
import json
from pathlib import Path

from ctt import config
from ctt.estimate import ledger

MAIN = "main"


def session_files(projects_dir, session_id):
    """{agent id or 'main': transcript path} for a session; empty when the session is unknown."""
    main = next(config.long_path(projects_dir).glob(f"*/{session_id}.jsonl"), None)
    if main is None:
        return {}
    files = {MAIN: main}
    for path in sorted((main.parent / session_id / "subagents").glob("agent-*.jsonl")):
        files[path.stem[len("agent-"):]] = path
    return files


def _spend(parts, usd):
    """tokens = everything the model processed; `new` = what was not a cache re-read (input + cache write +
    output); `cache_read_pct` = share of processed tokens that were re-read from cache."""
    tokens = sum(parts.values())
    return {"tokens": tokens, "usd": round(usd, 4), **parts,
            "new": tokens - parts["cache_read"],
            "cache_read_pct": round(100 * parts["cache_read"] / tokens, 1) if tokens else 0.0}


def _stalled(projects_dir, session_id, run):
    """The run's running steps with no transcript written to (this session, its agents, its child sessions) for
    [workflow] stall_minutes. A broken policy never breaks the pane's figures: nothing is reported then."""
    if not run or run.get("finished_at"):
        return []
    from ctt import ship
    from ctt.policy import load_policy
    try:
        minutes = ship.settings(load_policy(None))["stall_minutes"]
    except (ValueError, OSError):
        return []
    newest = None
    for sid in [session_id, *run.get("children", [])]:
        for path in session_files(projects_dir, sid).values():
            try:
                newest = max(newest or 0, path.stat().st_mtime)
            except OSError:
                continue
    return ship.stalled(run, newest, minutes)


def agent_usage(projects_dir, session_id, prices, state_dir=None):
    """{'session': id, 'agents': {id: {tokens, usd}}, 'total': {...}, 'budget': {...} | None}."""
    state_dir = Path(state_dir or config.ctt_home() / "live_state")
    state_path = state_dir / f"{session_id}.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    agents = {}
    for agent_id, path in session_files(projects_dir, session_id).items():
        entry = state.get(agent_id)
        # Start over when the file was rewritten, or the entry predates the token split (no "parts").
        if not entry or "parts" not in entry or path.stat().st_size < entry["offset"]:
            entry = {"offset": 0, "last_key": None, "parts": dict.fromkeys(ledger.PARTS, 0), "usd": 0.0}
        parts, usd, entry["offset"], entry["last_key"] = ledger.usage_parts_since(
            path, entry["offset"], prices, entry["last_key"], include_sidechain=agent_id != MAIN)
        entry["parts"] = {key: entry["parts"].get(key, 0) + parts[key] for key in ledger.PARTS}
        entry["usd"] += usd
        state[agent_id] = entry
        agents[agent_id] = _spend(entry["parts"], entry["usd"])
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    total_parts = {key: sum(state[a]["parts"][key] for a in agents) for key in ledger.PARTS}

    budget = None
    record = ledger.approved_for_session(session_id)
    if record:
        est = record["estimate"]
        try:  # the budget hook already counts main-thread usage since the estimate; reuse its figure
            hook = json.loads((config.ctt_home() / "hook_state" / f"{session_id}.json").read_text(encoding="utf-8"))
            used = (hook.get("budget") or {}).get("tokens", 0) if (hook.get("budget") or {}).get("id") == est["id"] else 0
        except (OSError, ValueError):
            used = 0
        budget = {"id": est["id"], "title": est["title"], "budget_tokens": est["tokens"]["budget"],
                  "p50_tokens": est["tokens"]["p50"], "p90_tokens": est["tokens"]["p90"], "used_tokens": used,
                  "steps": [{"role": s["role"], "share": s["share"]} for s in est["parts"][0]["steps"]]}
    from ctt import ship
    run = ship.for_session(session_id)
    from ctt import race
    from ctt.policy import load_policy
    try:
        tie_points = race.settings(load_policy(None))["tie_points"]
    except (ValueError, OSError):
        tie_points = None  # a broken [race] never breaks the pane's figures
    return {
        "session": session_id,
        "agents": agents,
        "total": _spend(total_parts, sum(state[a]["usd"] for a in agents)),
        "budget": budget,
        # 0.17: the session's /token-tools:ship run, if any; 0.20: with the running steps nobody is working on
        "ship": ship.brief(run, _stalled(projects_dir, session_id, run)),
        # 0.25: the session's running / judging /token-tools:race, if any
        "race": race.brief(race.for_session(session_id), tie_points,
                          {a: {"tokens": s["tokens"], "usd": s["usd"]} for a, s in agents.items()}),
    }
