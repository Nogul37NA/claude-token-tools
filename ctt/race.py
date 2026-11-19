"""/token-tools:race — the same task given to 2-3 subagents (different model / effort), each in its own git
worktree; tests and a blind reviewer score each, a table with the cost is shown and the user picks the winner.

A race is one JSON file in <ctt home>/races/. This module is only the bookkeeping (entrants, scores, figures from
the subagent transcripts, the suggested winner, the pick ledger `picks.jsonl`); the command drives the agents.
The Phòng điều hành reads it back through `live.agent_usage` (key "race").
"""
import json
import os
import re
import statistics
import subprocess
import unicodedata
from datetime import datetime, timedelta, timezone

from ctt import config

MODELS = ("haiku", "sonnet", "opus", "fable")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
NAMES = ("A", "B", "C")
RACE_STATUS = ("running", "judging", "picked", "cancelled")
ENTRANT_STATUS = ("pending", "running", "done", "failed")
TEST_PENALTY = 3.0

DEFAULTS = {"entrants": ["sonnet:medium", "opus:high"], "max_entrants": 3, "tie_points": 1.0}


def settings(policy):
    """[race] of policy.toml over the defaults; ValueError on a value the command could not follow."""
    raw = policy.get("race", {})
    out = dict(DEFAULTS)
    out["entrants"] = list(raw.get("entrants", DEFAULTS["entrants"]))
    maximum = raw.get("max_entrants", DEFAULTS["max_entrants"])
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 2 <= maximum <= len(NAMES):
        raise ValueError(f"[race] max_entrants = {maximum!r}: cần số nguyên từ 2 tới {len(NAMES)}")
    tie = raw.get("tie_points", DEFAULTS["tie_points"])
    if isinstance(tie, bool) or not isinstance(tie, (int, float)) or tie < 0:
        raise ValueError(f"[race] tie_points = {tie!r}: cần số ≥ 0")
    out["max_entrants"], out["tie_points"] = maximum, float(tie)
    parse_entrants(out["entrants"], maximum)
    return out


def parse_entrants(spec, max_entrants=len(NAMES)):
    """"sonnet:medium,opus:high" (or a list of such) → [(model, effort)]; ValueError on anything else."""
    items = spec if isinstance(spec, (list, tuple)) else str(spec).split(",")
    items = [str(item).strip() for item in items if str(item).strip()]
    out = []
    for item in items:
        model, _, effort = item.partition(":")
        if model not in MODELS or effort not in EFFORTS:
            raise ValueError(f"Đối thủ {item!r}: cần dạng model:effort, model {' | '.join(MODELS)}, "
                             f"effort {' | '.join(EFFORTS)}")
        if (model, effort) in out:
            raise ValueError(f"Đối thủ {item!r} trùng — mỗi đối thủ một cặp model:effort riêng")
        out.append((model, effort))
    if len(out) < 2:
        raise ValueError("Cuộc đua cần ít nhất 2 đối thủ")
    if len(out) > max_entrants:
        raise ValueError(f"Cuộc đua có {len(out)} đối thủ; trần là {max_entrants} ([race] max_entrants)")
    return out


def races_dir():
    return config.ctt_home() / "races"


def picks_path():
    return races_dir() / "picks.jsonl"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(race_id):
    if not re.fullmatch(r"[a-z0-9-]{1,80}", race_id):
        raise ValueError(f"Mã cuộc đua không hợp lệ: {race_id!r}")
    return races_dir() / f"{race_id}.json"


def load(race_id):
    path = _path(race_id)
    if not path.is_file():
        raise ValueError(f"Không thấy cuộc đua {race_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(race):
    races_dir().mkdir(parents=True, exist_ok=True)
    path = _path(race["id"])
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(race, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)
    return race


def _slug(title):
    flat = unicodedata.normalize("NFKD", title.lower().replace("đ", "d"))
    text = re.sub(r"[^a-z0-9]+", "-", flat.encode("ascii", "ignore").decode()).strip("-")
    return text[:40] or "race"


def _git(cwd, *args):
    """Stdout of `git -C cwd ...`, or None when git is missing / the command fails."""
    try:
        done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def head_sha(cwd):
    out = _git(cwd, "rev-parse", "HEAD") if cwd else None
    return out.strip() or None if out else None


def check_clean(cwd):
    """ValueError unless cwd is a git repo with a clean tree: each entrant's worktree starts from HEAD, so
    uncommitted work would be missing from every entrant and the diffs would not be comparable."""
    if not head_sha(cwd):
        raise ValueError(f"{cwd} không phải repo git (hoặc chưa có commit) — cuộc đua cần git worktree")
    dirty = _git(cwd, "status", "--porcelain")
    if dirty is None:
        raise ValueError(f"Không đọc được `git status` ở {cwd}")
    if dirty.strip():
        raise ValueError(f"{cwd} còn thay đổi chưa commit — commit hoặc stash trước khi đua "
                         "(worktree của các đối thủ bắt đầu từ HEAD)")


def start(title, task, entrants, session_id=None, cwd=None, now=None):
    """Open a race; `entrants` is already checked ([(model, effort)] — see parse_entrants)."""
    now = now or _now()
    base = f"{now[:10]}-{_slug(title)}"
    race_id, n = base, 2
    while (races_dir() / f"{race_id}.json").exists():
        race_id, n = f"{base}-{n}", n + 1
    return save({
        "id": race_id, "title": title.strip(), "task": task, "cwd": cwd, "base": head_sha(cwd),
        "session_id": session_id, "started_at": now, "status": "running",
        "entrants": [{"name": NAMES[i], "model": model, "effort": effort, "agent_id": None, "worktree": None,
                      "branch": None, "status": "pending", "tests": None, "review": None, "diff": None,
                      "tokens": None, "usd": None, "note": None} for i, (model, effort) in enumerate(entrants)],
        "winner": None, "picked_at": None, "note": None})


def brief_lines(race):
    """One instruction line per entrant: what the command tells that subagent."""
    return "\n".join(
        f"{e['name']} · model {e['model']} · effort {e['effort']}: làm đúng yêu cầu trong worktree riêng "
        f"(Agent isolation worktree), chạy test của project, báo lại nhánh + đường dẫn worktree, không commit lên "
        f"nhánh chính, không biết có đối thủ khác" for e in race["entrants"])


def _entrant(race, name):
    entrant = next((e for e in race["entrants"] if e["name"] == name), None)
    if entrant is None:
        raise ValueError(f"Cuộc đua không có đối thủ {name!r} ({', '.join(e['name'] for e in race['entrants'])})")
    return entrant


def _open(race):
    if race["status"] in ("picked", "cancelled"):
        raise ValueError(f"Cuộc đua {race['id']} đã {'chọn xong' if race['status'] == 'picked' else 'huỷ'}")


def set_entry(race_id, name, agent_id=None, worktree=None, branch=None, status=None, note=None):
    race = load(race_id)
    _open(race)
    entrant = _entrant(race, name)
    if status is not None and status not in ENTRANT_STATUS:
        raise ValueError(f"Trạng thái {status!r}: chỉ nhận {' | '.join(ENTRANT_STATUS)}")
    if agent_id is not None and agent_id.startswith("agent-"):
        agent_id = agent_id[len("agent-"):]
    for key, value in (("agent_id", agent_id), ("worktree", worktree), ("branch", branch), ("status", status),
                       ("note", note)):
        if value is not None:
            entrant[key] = value
    return save(race)


def score(race_id, name, tests_pass=None, tests_fail=None, review=None, notes=None):
    race = load(race_id)
    _open(race)
    entrant = _entrant(race, name)
    if review is not None and not 0 <= review <= 10:
        raise ValueError(f"Điểm review {review}: cần từ 0 tới 10")
    for label, value in (("--tests-pass", tests_pass), ("--tests-fail", tests_fail)):
        if value is not None and value < 0:
            raise ValueError(f"{label} {value}: cần số ≥ 0")
    if tests_pass is not None or tests_fail is not None:
        tests = entrant["tests"] or {"pass": 0, "fail": 0}
        entrant["tests"] = {"pass": tests["pass"] if tests_pass is None else tests_pass,
                            "fail": tests["fail"] if tests_fail is None else tests_fail}
    if review is not None or notes is not None:
        old = entrant["review"] or {"score": None, "notes": ""}
        entrant["review"] = {"score": float(review) if review is not None else old["score"],
                             "notes": notes if notes is not None else old["notes"]}
    if race["status"] == "running":
        race["status"] = "judging"
    return save(race)


def quality(entrant):
    """Review score, minus 3 when a test failed; None while unreviewed."""
    review = entrant.get("review")
    if not review or review.get("score") is None:
        return None
    failed = (entrant.get("tests") or {}).get("fail", 0) > 0
    return round(review["score"] - (TEST_PENALTY if failed else 0.0), 2)


def suggest(race, tie_points=None):
    """(name, reason) of the entrant to propose: the best quality; among those within tie_points of it, the
    cheapest. (None, reason) when nobody is scored."""
    tie = DEFAULTS["tie_points"] if tie_points is None else tie_points
    scored = [(e, quality(e)) for e in race["entrants"] if quality(e) is not None]
    if not scored:
        return None, "chưa entrant nào có điểm review"
    best = max(q for _, q in scored)
    close = [(e, q) for e, q in scored if best - q <= tie]
    if len(close) == 1:
        e, q = close[0]
        return e["name"], f"điểm cao nhất ({q:g})"
    pick = min(close, key=lambda item: (item[0]["usd"] is None, item[0]["usd"] or 0, -item[1]))[0]
    names = "/".join(e["name"] for e, _ in close)
    if pick["usd"] is None:
        return pick["name"], f"{names} hoà điểm (chênh ≤ {tie:g}), chưa có chi phí để so"
    return pick["name"], f"{names} hoà điểm (chênh ≤ {tie:g}) → bản rẻ hơn (${pick['usd']:.2f})"


def _live(entrant, spend, key):
    live_spend = (spend or {}).get(entrant.get("agent_id") or "")
    return live_spend[key] if live_spend else entrant.get(key)


def brief(race, tie_points=None, spend=None):
    """What the pane shows. `spend` {agent_id: {tokens, usd}} (live figures) wins over the saved ones."""
    if not race:
        return None
    return {"id": race["id"], "title": race["title"], "status": race["status"],
            "entrants": [{"name": e["name"], "model": e["model"], "effort": e["effort"], "status": e["status"],
                          "tokens": _live(e, spend, "tokens"), "usd": _live(e, spend, "usd"), "quality": quality(e),
                          "tests_pass": (e.get("tests") or {}).get("pass"),
                          "tests_fail": (e.get("tests") or {}).get("fail")} for e in race["entrants"]],
            "winner": race.get("winner"), "suggested": suggest(race, tie_points)[0]}


def for_session(session_id, now=None, recent_minutes=10):
    """The newest running / judging race of a session; else one picked or cancelled within the last
    `recent_minutes` (so the pane can show the result); None when it has neither."""
    folder = races_dir()
    if not session_id or not folder.is_dir():
        return None
    now = now or datetime.now(timezone.utc)
    found, ended = [], []
    for path in folder.glob("*.json"):
        try:
            race = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if race.get("session_id") != session_id:
            continue
        if race.get("status") in ("running", "judging"):
            found.append(race)
        elif race.get("status") in ("picked", "cancelled") and _recent(race.get("picked_at"), now, recent_minutes):
            ended.append(race)

    def newest(item):
        return item.get("started_at", "")

    return max(found, key=newest, default=None) or max(ended, key=newest, default=None)


def _recent(stamp, now, minutes):
    try:
        at = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return False
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return timedelta(0) <= now - at <= timedelta(minutes=minutes)


_STAT = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")


def parse_shortstat(text):
    """`git diff --shortstat` output → {files, insertions, deletions}; zeros for an empty diff, None if unreadable."""
    if text is None:
        return None
    if not text.strip():
        return {"files": 0, "insertions": 0, "deletions": 0}
    found = _STAT.search(text)
    if not found:
        return None
    return {"files": int(found.group(1)), "insertions": int(found.group(2) or 0), "deletions": int(found.group(3) or 0)}


def diff_of(race, entrant):
    """The entrant's change against the race's base: base...branch, else the worktree against base."""
    base = race.get("base")
    where = entrant.get("worktree")  # never the main checkout: its diff would not be this entrant's
    if not base or not where:
        return None
    if entrant.get("branch"):
        out = parse_shortstat(_git(where, "diff", "--shortstat", f"{base}...{entrant['branch']}"))
        if out is not None:
            return out
    return parse_shortstat(_git(where, "diff", "--shortstat", base))


def agent_spend(projects_dir, session_id, agent_id, prices):
    """(tokens, usd) of one subagent's transcript, or None when the session / agent has none."""
    from ctt import live
    from ctt.estimate import ledger
    path = live.session_files(projects_dir, session_id).get(agent_id) if session_id and agent_id else None
    if path is None:
        return None
    parts, usd, _, _ = ledger.usage_parts_since(path, 0, prices, None, include_sidechain=True)
    return sum(parts.values()), round(usd, 4)


def report(race_id, projects_dir, prices, tie_points=None):
    """Fill tokens / usd / diff of each entrant, keep them in the race file, and return
    {race, rows, suggested, reason}. A figure that cannot be read stays as it was (null)."""
    race = load(race_id)
    for entrant in race["entrants"]:
        try:
            spend = agent_spend(projects_dir, race.get("session_id"), entrant.get("agent_id"), prices)
        except OSError:
            spend = None
        if spend:
            entrant["tokens"], entrant["usd"] = spend
        diff = diff_of(race, entrant)
        if diff is not None:
            entrant["diff"] = diff
    save(race)
    name, reason = suggest(race, tie_points)
    rows = [{**e, "quality": quality(e)} for e in race["entrants"]]
    return {"race": race, "rows": rows, "suggested": name, "reason": reason}


def report_text(result):
    race = result["race"]
    header = ("Đối thủ", "Model", "Trạng thái", "Test", "Điểm", "Diff", "Token", "$")
    table = [header]
    for row in result["rows"]:
        tests = row.get("tests")
        diff = row.get("diff")
        table.append((
            row["name"], f"{row['model']}:{row['effort']}", row["status"],
            f"{tests['pass']}✓ {tests['fail']}✗" if tests else "—",
            f"{row['quality']:g}" if row["quality"] is not None else "—",
            f"{diff['files']}f +{diff['insertions']}/-{diff['deletions']}" if diff else "—",
            f"{row['tokens']:,}" if row.get("tokens") is not None else "—",
            f"{row['usd']:.2f}" if row.get("usd") is not None else "—"))
    widths = [max(len(line[i]) for line in table) for i in range(len(header))]
    lines = [f"Cuộc đua {race['id']} · {race['title']} · {race['status']}"]
    lines += ["  " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)) for line in table]
    if result["suggested"]:
        lines.append(f"Đề xuất: {result['suggested']} ({result['reason']})")
    else:
        lines.append(f"Chưa đề xuất: {result['reason']}")
    return "\n".join(lines)


def pick(race_id, winner, note=None, now=None):
    race = load(race_id)
    _open(race)
    chosen = _entrant(race, winner)
    now = now or _now()
    race.update(status="picked", winner=winner, picked_at=now)
    if note:
        race["note"] = note
    save(race)
    record = {"race": race["id"], "title": race["title"], "at": now,
              "winner": {"model": chosen["model"], "effort": chosen["effort"]},
              "entrants": [{"model": e["model"], "effort": e["effort"], "quality": quality(e), "usd": e.get("usd"),
                            "tokens": e.get("tokens"), "tests": e.get("tests")} for e in race["entrants"]]}
    with picks_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return race


def cancel(race_id, note=None):
    race = load(race_id)
    _open(race)
    race["status"] = "cancelled"
    race["picked_at"] = _now()  # when it ended: the pane shows the result for a few minutes
    if note:
        race["note"] = note
    return save(race)


def stats(days=90, now=None):
    """Per model:effort from the ledger: races, wins, win rate, median usd, median quality."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    table = {}
    try:
        lines = picks_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            record = json.loads(line)
            at = datetime.fromisoformat(record["at"])
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            win = record.get("winner") or {}
            entrants = [e for e in record.get("entrants", []) if e["model"] and e["effort"]]
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if at < since:
            continue
        for e in entrants:
            key = f"{e['model']}:{e['effort']}"
            row = table.setdefault(key, {"races": 0, "wins": 0, "usd": [], "quality": []})
            row["races"] += 1
            row["wins"] += (e["model"], e["effort"]) == (win.get("model"), win.get("effort"))
            if e.get("usd") is not None:
                row["usd"].append(e["usd"])
            if e.get("quality") is not None:
                row["quality"].append(e["quality"])
    return [{"entrant": key, "races": row["races"], "wins": row["wins"],
             "win_rate": round(row["wins"] / row["races"], 3),
             "median_usd": round(statistics.median(row["usd"]), 4) if row["usd"] else None,
             "median_quality": round(statistics.median(row["quality"]), 2) if row["quality"] else None}
            for key, row in sorted(table.items())]
