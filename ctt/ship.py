"""/token-tools:ship — one request in, a product out, with two approvals.

A run is one JSON file in <ctt home>/ship/: the request, the stage it is at (0..6), the work plan once approved and
each step's state. The command writes it through `ctt ship-*`; the Phòng điều hành reads it back with the session's
usage (`ctt agent-usage`), so the pane shows where the run stands without the model saying anything.
"""
import json
import re
import unicodedata
from datetime import datetime, timezone

from ctt import config

STAGES = ("Nhận yêu cầu", "Ước lượng", "Chia việc", "Chạy", "Kiểm chứng", "Giao", "Học")
MODES = ("main", "subagent", "session")
STEP_STATES = ("pending", "running", "done", "failed", "skipped")

DEFAULTS = {
    "clarify_max": 3,            # questions asked at most, in one round, before anything starts
    "max_steps": 8,              # rows of the work plan
    "fix_rounds": 2,             # verify → fix rounds before handing over what is left as open
    "session_min_tokens": 2_000_000,  # a step estimated below this never gets a session of its own
    "auto_approve_usd": 0.0,     # 0: always ask; > 0: a run whose budget is at most this many $ needs no 1st approval
}


def settings(policy):
    """[workflow] of policy.toml over the defaults; ValueError on a value the command could not follow."""
    raw = policy.get("workflow", {})
    out = dict(DEFAULTS)
    for key, fallback in DEFAULTS.items():
        value = raw.get(key, fallback)
        kinds = (int, float) if isinstance(fallback, float) else (int,)
        if isinstance(value, bool) or not isinstance(value, kinds) or value < 0:
            whole = "" if isinstance(fallback, float) else " nguyên"
            raise ValueError(f"[workflow] {key} = {value!r}: cần số{whole} ≥ 0")
        out[key] = float(value) if isinstance(fallback, float) else value
    return out


def runs_dir():
    return config.ctt_home() / "ship"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(run_id):
    if not re.fullmatch(r"[a-z0-9-]{1,80}", run_id):
        raise ValueError(f"Mã lượt không hợp lệ: {run_id!r}")
    return runs_dir() / f"{run_id}.json"


def load(run_id):
    path = _path(run_id)
    if not path.is_file():
        raise ValueError(f"Không thấy lượt {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(run):
    runs_dir().mkdir(parents=True, exist_ok=True)
    _path(run["id"]).write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    return run


def _slug(title):
    flat = unicodedata.normalize("NFKD", title.lower().replace("đ", "d"))
    text = re.sub(r"[^a-z0-9]+", "-", flat.encode("ascii", "ignore").decode()).strip("-")
    return text[:40] or "task"


def start(title, session_id=None, cwd=None, now=None):
    now = now or _now()
    base = f"{now[:10]}-{_slug(title)}"
    run_id, n = base, 2
    while (runs_dir() / f"{run_id}.json").exists():
        run_id, n = f"{base}-{n}", n + 1
    return save({"id": run_id, "title": title.strip(), "session_id": session_id, "cwd": cwd, "stage": 0,
                 "started_at": now, "history": [{"stage": 0, "at": now}], "plan": [], "estimate_id": None})


def check_plan(steps, limits):
    """The plan's rows, checked against [workflow] and [sessions]; ValueError naming what is wrong."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("Kế hoạch cần ít nhất 1 bước")
    if len(steps) > limits["max_steps"]:
        raise ValueError(f"Kế hoạch có {len(steps)} bước; trần là {limits['max_steps']} ([workflow] max_steps)")
    out = []
    for index, step in enumerate(steps, 1):
        name = str(step.get("name", "")).strip()
        mode = step.get("mode", "main")
        if not name:
            raise ValueError(f"Bước {index} thiếu name")
        if mode not in MODES:
            raise ValueError(f"Bước {name}: mode {mode!r}, chỉ nhận {' | '.join(MODES)}")
        tokens = step.get("tokens")
        if mode == "session" and isinstance(tokens, (int, float)) and tokens < limits["session_min_tokens"]:
            raise ValueError(f"Bước {name}: ước {tokens:,.0f} token, dưới ngưỡng mở phiên con "
                             f"{limits['session_min_tokens']:,} ([workflow] session_min_tokens) — giao subagent")
        out.append({"name": name, "role": step.get("role"), "mode": mode, "files": list(step.get("files", [])),
                    "done": step.get("done", ""), "tokens": tokens, "status": "pending"})
    sessions = sum(1 for step in out if step["mode"] == "session")
    if sessions > limits.get("max_children", sessions):
        raise ValueError(f"Kế hoạch mở {sessions} phiên con; trần là {limits['max_children']} ([sessions] max_children)")
    return out


def set_stage(run_id, stage, note=None, estimate_id=None, plan=None, limits=None, now=None, cwd=None):
    if not 0 <= stage < len(STAGES):
        raise ValueError(f"Bước quy trình {stage}: chỉ từ 0 tới {len(STAGES) - 1}")
    run = load(run_id)
    now = now or _now()
    if cwd:
        run["cwd"] = cwd
    if plan is not None:
        run["plan"] = check_plan(plan, limits or DEFAULTS)
    if estimate_id:
        run["estimate_id"] = estimate_id
    run["stage"] = stage
    run["history"].append({"stage": stage, "at": now, **({"note": note} if note else {})})
    return save(run)


def set_step(run_id, name, status, note=None):
    if status not in STEP_STATES:
        raise ValueError(f"Trạng thái {status!r}: chỉ nhận {' | '.join(STEP_STATES)}")
    run = load(run_id)
    step = next((s for s in run["plan"] if s["name"] == name), None)
    if step is None:
        raise ValueError(f"Kế hoạch không có bước {name!r}")
    step["status"] = status
    if note:
        step["note"] = note
    return save(run)


def finish(run_id, result, note=None, check=None, now=None):
    """Stage 6: the run is over; `check` is the estimate's row from `estimate-check` (actual vs P50/P90)."""
    run = set_stage(run_id, len(STAGES) - 1, note, now=now)
    run["finished_at"] = now or _now()
    run["result"] = result
    if check:
        run["check"] = {key: check[key] for key in ("p50", "p90", "actual", "ratio", "within_p90") if key in check}
    return save(run)


def for_session(session_id):
    """The newest run of a session, for the pane; None when it has none."""
    folder = runs_dir()
    if not session_id or not folder.is_dir():
        return None
    runs = []
    for path in folder.glob("*.json"):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if run.get("session_id") == session_id:
            runs.append(run)
    return max(runs, key=lambda r: r.get("started_at", ""), default=None)


def brief(run):
    """What the pane shows: stage name and number, steps done / total, result when over."""
    if not run:
        return None
    plan = run.get("plan", [])
    return {"id": run["id"], "title": run["title"], "cwd": run.get("cwd"), "stage": run["stage"], "stage_name": STAGES[run["stage"]],
            "stages": len(STAGES), "steps_done": sum(1 for s in plan if s["status"] in ("done", "skipped")),
            "steps": len(plan), "failed": sum(1 for s in plan if s["status"] == "failed"),
            "running": [s["name"] for s in plan if s["status"] == "running"],
            "result": run.get("result"), "check": run.get("check")}


def summary(run):
    """Plain lines for the command's output."""
    lines = [f"Lượt {run['id']} · {run['title']} · bước {run['stage']}/{len(STAGES) - 1} {STAGES[run['stage']]}"]
    for step in run.get("plan", []):
        lines.append(f"  [{step['status']:<7}] {step['name']} · {step.get('role') or '—'} · {step['mode']}")
    if run.get("check"):
        c = run["check"]
        lines.append(f"  Thực tế {c.get('actual', 0) / 1e6:.1f}M token / P50 {c.get('p50', 0) / 1e6:.1f}M "
                     f"(×{c.get('ratio', 0):.2f}, {'trong' if c.get('within_p90') else 'vượt'} P90)")
    return "\n".join(lines)
