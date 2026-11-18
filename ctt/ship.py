"""/token-tools:ship — one request in, a product out, with two approvals.

A run is one JSON file in <ctt home>/ship/: the request, the stage it is at (0..6), the work plan once approved and
each step's state. The command writes it through `ctt ship-*`; the Phòng điều hành reads it back with the session's
usage (`ctt agent-usage`), so the pane shows where the run stands without the model saying anything.

0.19.0: a step may wait for others (`needs`), and each start of a step is a try with its own code — a report that
carries the code of an earlier try is refused, so a late agent cannot close the step a newer try is working on.
"""
import json
import secrets
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
    seen = set()
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ValueError(f"Bước {index} phải là một object")
        name = str(step.get("name", "")).strip()
        mode = step.get("mode", "main")
        if not name:
            raise ValueError(f"Bước {index} thiếu name")
        if name in seen:
            raise ValueError(f"Hai bước trùng tên {name!r} — mỗi bước một tên riêng")
        seen.add(name)
        needs = step.get("needs") or []
        if isinstance(needs, str) or not isinstance(needs, list):
            raise ValueError(f"Bước {name}: needs phải là danh sách tên bước")
        needs = list(dict.fromkeys(str(n).strip() for n in needs))
        if mode not in MODES:
            raise ValueError(f"Bước {name}: mode {mode!r}, chỉ nhận {' | '.join(MODES)}")
        tokens = step.get("tokens")
        if mode == "session" and isinstance(tokens, (int, float)) and tokens < limits["session_min_tokens"]:
            raise ValueError(f"Bước {name}: ước {tokens:,.0f} token, dưới ngưỡng mở phiên con "
                             f"{limits['session_min_tokens']:,} ([workflow] session_min_tokens) — giao subagent")
        out.append({"name": name, "role": step.get("role"), "mode": mode, "files": list(step.get("files", [])),
                    "done": step.get("done", ""), "tokens": tokens, "needs": needs, "status": "pending"})
    _check_needs(out)
    sessions = sum(1 for step in out if step["mode"] == "session")
    if sessions > limits.get("max_children", sessions):
        raise ValueError(f"Kế hoạch mở {sessions} phiên con; trần là {limits['max_children']} ([sessions] max_children)")
    return out


def _check_needs(plan):
    """Every `needs` names another step of the plan and the steps can be ordered; ValueError otherwise."""
    by_name = {step["name"]: step for step in plan}
    for step in plan:
        for need in step["needs"]:
            if need == step["name"]:
                raise ValueError(f"Bước {step['name']}: needs trỏ về chính nó")
            if need not in by_name:
                raise ValueError(f"Bước {step['name']}: needs {need!r} không có trong kế hoạch")
    state = {}  # name → 1 while on the path being walked, 2 once cleared

    def walk(name, path):
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            loop = path[path.index(name):] + [name]
            raise ValueError("Các bước chờ nhau thành vòng: " + " → ".join(loop))
        state[name] = 1
        for need in by_name[name]["needs"]:
            walk(need, path + [name])
        state[name] = 2

    for step in plan:
        walk(step["name"], [])


def _waiting_on(run, step):
    """Names of the steps `step` needs that are not over yet (done or skipped)."""
    status = {s["name"]: s["status"] for s in run.get("plan", [])}
    return [need for need in step.get("needs", []) if status.get(need) not in ("done", "skipped")]


def next_steps(run):
    """What can start now, what is running, what waits and on what, what failed — for `ship-next` and the resume."""
    out = {"ready": [], "running": [], "waiting": [], "failed": []}
    for step in run.get("plan", []):
        if step["status"] == "running":
            out["running"].append({"name": step["name"], "attempt": step.get("attempt")})
        elif step["status"] == "failed":
            out["failed"].append({"name": step["name"], "attempts": step.get("attempts", 0)})
        elif step["status"] == "pending":
            waiting = _waiting_on(run, step)
            if waiting:
                out["waiting"].append({"name": step["name"], "on": waiting})
            else:
                out["ready"].append({"name": step["name"], "role": step.get("role"), "mode": step["mode"]})
    out["all_over"] = bool(run.get("plan")) and all(s["status"] in ("done", "skipped") for s in run["plan"])
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


def set_step(run_id, name, status, note=None, attempt=None, files=None, tests=None, now=None):
    """One step's state. `running` opens a try and gives it a code (`attempt` on the step); `done` / `failed` of a
    step that has a try open must carry that code. `files` and `tests` are what the step reports when it ends."""
    if status not in STEP_STATES:
        raise ValueError(f"Trạng thái {status!r}: chỉ nhận {' | '.join(STEP_STATES)}")
    run = load(run_id)
    step = next((s for s in run["plan"] if s["name"] == name), None)
    if step is None:
        raise ValueError(f"Kế hoạch không có bước {name!r}")
    now = now or _now()
    current = step.get("attempt")
    if status == "running":
        waiting = _waiting_on(run, step)
        if waiting:
            raise ValueError(f"Bước {name} còn chờ: {', '.join(waiting)} — xem `ship-next`")
        if current:  # started again while a try is open: that try is given up, its code stops working
            _close_try(step, current, "abandoned", now)
        step["attempts"] = step.get("attempts", 0) + 1
        step["attempt"] = f"a{step['attempts']}-{secrets.token_hex(2)}"
        step.setdefault("tries", []).append({"id": step["attempt"], "started_at": now})
        step.pop("report", None)
    elif status in ("done", "failed"):
        if attempt and attempt != current:
            known = any(t["id"] == attempt for t in step.get("tries", []))
            raise ValueError(f"Bước {name}: mã lần thử {attempt} " + (
                f"là của lần thử cũ, lần đang chạy là {current}" if known and current else
                "là của lần thử đã đóng" if known else "không có") + " — bỏ qua báo cáo này")
        if current and not attempt:
            raise ValueError(f"Bước {name} đang ở lần thử {current}: thêm --attempt {current}")
        if current:
            _close_try(step, current, status, now)
            step["attempt"] = None
        report = {key: value for key, value in (("files", list(files or [])), ("tests", tests)) if value}
        if report:
            step["report"] = report
    else:  # pending | skipped: any open try is given up
        if current:
            _close_try(step, current, "abandoned", now)
            step["attempt"] = None
    step["status"] = status
    if note:
        step["note"] = note
    return save(run)


def _close_try(step, attempt, status, now):
    for entry in step.get("tries", []):
        if entry["id"] == attempt:
            entry.update(status=status, ended_at=now)


def attach(run_id, session_id):
    """A later session takes the run over (resume): the pane of that session shows it from now on."""
    run = load(run_id)
    if session_id and session_id != run.get("session_id"):
        if run.get("session_id"):
            run.setdefault("past_sessions", []).append(run["session_id"])
        run["session_id"] = session_id
        save(run)
    return run


def unfinished():
    """Runs that never reached `ship-finish`, newest first."""
    folder = runs_dir()
    runs = []
    for path in folder.glob("*.json") if folder.is_dir() else []:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not run.get("finished_at"):
            runs.append(run)
    return sorted(runs, key=lambda r: r.get("started_at", ""), reverse=True)


def resume_text(run):
    """What a new session needs to carry the run on: where it stands, each step, what to do next."""
    lines = [summary(run)]
    lines.append(f"Thư mục làm việc: {run.get('cwd') or '—'} · phiếu: {run.get('estimate_id') or 'chưa có'}")
    nxt = next_steps(run)
    if run.get("finished_at"):
        lines.append(f"Lượt đã kết thúc ({run.get('result')}), không còn gì để chạy tiếp.")
        return "\n".join(lines)
    if nxt["running"]:
        lines.append("Đang ghi là chạy (agent của phiên trước có thể đã mất — kiểm tra kết quả, rồi chạy lại "
                     "bằng --status running để có mã mới): " + ", ".join(
                         f"{s['name']} [{s['attempt'] or 'không mã'}]" for s in nxt["running"]))
    if nxt["failed"]:
        lines.append("Lỗi: " + ", ".join(f"{s['name']} ({s['attempts']} lần thử)" for s in nxt["failed"]))
    if nxt["ready"]:
        lines.append("Chạy được ngay: " + ", ".join(s["name"] for s in nxt["ready"]))
    if nxt["waiting"]:
        lines.append("Còn chờ: " + "; ".join(f"{s['name']} ← {', '.join(s['on'])}" for s in nxt["waiting"]))
    if not run.get("plan"):
        lines.append(f"Chưa có bảng chia việc: làm tiếp từ bước {run['stage']} ({STAGES[run['stage']]}).")
    elif nxt["all_over"] and run["stage"] <= 3:
        lines.append("Mọi bước đã xong: sang bước 4 (Kiểm chứng).")
    elif run["stage"] > 3:
        lines.append(f"Làm tiếp từ bước {run['stage']} ({STAGES[run['stage']]}).")
    return "\n".join(lines)


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
            "retries": sum(max(0, s.get("attempts", 0) - 1) for s in plan),
            "result": run.get("result"), "check": run.get("check")}


def summary(run):
    """Plain lines for the command's output."""
    lines = [f"Lượt {run['id']} · {run['title']} · bước {run['stage']}/{len(STAGES) - 1} {STAGES[run['stage']]}"]
    for step in run.get("plan", []):
        line = f"  [{step['status']:<7}] {step['name']} · {step.get('role') or '—'} · {step['mode']}"
        if step.get("needs"):
            line += f" · chờ {', '.join(step['needs'])}"
        if step.get("attempt"):
            line += f" · mã lần thử {step['attempt']}"
        elif step.get("attempts", 0) > 1:
            line += f" · {step['attempts']} lần thử"
        report = step.get("report") or {}
        if report.get("files"):
            line += f" · {len(report['files'])} file"
        if report.get("tests"):
            line += f" · test: {report['tests']}"
        lines.append(line)
    if run.get("check"):
        c = run["check"]
        lines.append(f"  Thực tế {c.get('actual', 0) / 1e6:.1f}M token / P50 {c.get('p50', 0) / 1e6:.1f}M "
                     f"(×{c.get('ratio', 0):.2f}, {'trong' if c.get('within_p90') else 'vượt'} P90)")
    return "\n".join(lines)
