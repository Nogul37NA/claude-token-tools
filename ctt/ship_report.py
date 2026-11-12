"""`ctt ship-report`: how the /token-tools:ship runs went — results, estimate vs actual, where they got stuck.

Reads the runs in <ctt home>/ship/, the estimate ledger, the transcripts (cost of the run's session and of the child
sessions it opened, role refusals, sessions that could not start) and the plugin's shared store (which session opened
which). Writes one self-contained HTML page next to the other reports.
"""
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ctt import config, ship
from ctt.estimate import ledger
from ctt.live import session_files

ROLE_RE = re.compile(r"Vai (\w+) (?:không|chỉ)[^\"\\]{0,140}")
MODE_TEXT = {"main": "phiên chính", "subagent": "subagent", "session": "phiên con"}
RESULT_TEXT = {"ok": "đạt", "partial": "đạt một phần", "failed": "không đạt", None: "đang chạy"}


def _when(text):
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def store_children(store_dir=None):
    """{parent session id: [child session ids]} from the summaries the plugin keeps in its shared store."""
    folder = Path(store_dir) if store_dir else Path.home() / ".claude" / "plugins" / "store"
    out = {}
    for path in folder.glob("token-tools_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key, value in data.items():
            if key.startswith("session:") and isinstance(value, dict) and value.get("parent"):
                out.setdefault(value["parent"], []).append(value.get("id") or key[len("session:"):])
    return out


def friction(projects_dir, session_ids):
    """Role refusals by role, and sessions that could not start (reason → count), read from the transcripts."""
    roles, refused = {}, {}
    for session_id in session_ids:
        for path in session_files(projects_dir, session_id).values():
            starts = set()
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                content = (entry.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if block.get("type") == "tool_use" and str(block.get("name", "")).endswith("start_session"):
                        starts.add(block.get("id"))
                    if block.get("type") != "tool_result":
                        continue
                    text = json.dumps(block.get("content"), ensure_ascii=False)
                    for match in ROLE_RE.finditer(text):
                        roles[match.group(1)] = roles.get(match.group(1), 0) + 1
                    if block.get("tool_use_id") in starts and block.get("is_error"):
                        reason = ("chế độ Auto chặn" if re.search(r"auto mode", text, re.I)
                                  else "chưa tin cậy thư mục" if re.search(r"trust", text, re.I)
                                  else "đường dẫn cwd viết khác" if re.search(r"symbolic link|another spelling", text, re.I)
                                  else "lý do khác")
                        refused[reason] = refused.get(reason, 0) + 1
    return roles, refused


def collect(days, projects_dir, prices, scan, store_dir=None, now=None):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    estimates = ledger.load()
    children = store_children(store_dir)
    runs = []
    for path in sorted(ship.runs_dir().glob("*.json")) if ship.runs_dir().is_dir() else []:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        started = _when(run.get("started_at"))
        if not started or started < since:
            continue
        if not run.get("session_id"):
            # Started with a work folder other than the session's (0.17.1+): the session is the one that ran
            # `ship-stage <id>`; kept on the run once found.
            found = next((use.session_id for use in scan.tool_uses
                          if run["id"] in json.dumps(use.input, ensure_ascii=False)), None)
            if found:
                run["session_id"] = found
                ship.save(run)
        # The child sessions: kept on the run once seen, since the store forgets summaries after a day.
        kids = sorted(set(run.get("children", [])) | set(children.get(run.get("session_id"), [])))
        if kids != run.get("children", []) and run.get("session_id"):
            run["children"] = kids
            ship.save(run)
        ended = _when(run.get("finished_at")) or max((_when(h.get("at")) for h in run.get("history", [])), default=started)
        sessions = [s for s in [run.get("session_id"), *kids] if s]
        tokens, usd, usd_kids = 0, 0.0, 0.0
        for request in scan.requests:
            if request.session_id in sessions and started <= request.timestamp <= ended + timedelta(minutes=5):
                tokens += request.total
                cost = prices.cost(request)[0]
                usd += cost
                if request.session_id != run.get("session_id"):
                    usd_kids += cost
        record = estimates.get(run.get("estimate_id") or "")
        est = record["estimate"] if record else None
        roles, refused = friction(projects_dir, sessions)
        plan = run.get("plan", [])
        runs.append({
            "id": run["id"], "title": run["title"], "started": started, "ended": ended,
            "minutes": max(0, round((ended - started).total_seconds() / 60)),
            "stage": run.get("stage", 0), "stage_name": ship.STAGES[run.get("stage", 0)], "result": run.get("result"),
            "plan": plan, "modes": {mode: sum(1 for s in plan if s.get("mode") == mode) for mode in ship.MODES},
            "failed_steps": [s["name"] for s in plan if s.get("status") == "failed"],
            "children": kids, "tokens": tokens, "usd": usd, "usd_children": usd_kids,
            "p50": est["tokens"]["p50"] if est else None, "p90": est["tokens"]["p90"] if est else None,
            "usd_p50": est["usd"]["p50"] if est else None,
            "ratio": tokens / est["tokens"]["p50"] if est and est["tokens"]["p50"] else None,
            "within_p90": tokens <= est["tokens"]["p90"] if est else None,
            "notes": [h["note"] for h in run.get("history", []) if h.get("note")],
            "roles": roles, "refused": refused,
        })
    runs.sort(key=lambda r: r["started"], reverse=True)
    return {"days": days, "at": now, "runs": runs, "totals": totals(runs), "advice": advice(runs)}


def _median(values):
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def totals(runs):
    done = [r for r in runs if r["result"]]
    measured = [r for r in runs if r["ratio"] is not None and r["tokens"]]
    roles, refused = {}, {}
    for run in runs:
        for key, n in run["roles"].items():
            roles[key] = roles.get(key, 0) + n
        for key, n in run["refused"].items():
            refused[key] = refused.get(key, 0) + n
    return {
        "runs": len(runs), "done": len(done),
        "ok": sum(r["result"] == "ok" for r in done), "partial": sum(r["result"] == "partial" for r in done),
        "failed": sum(r["result"] == "failed" for r in done),
        "usd": sum(r["usd"] for r in runs), "tokens": sum(r["tokens"] for r in runs),
        "median_ratio": _median([r["ratio"] for r in measured]),
        "within_p90": sum(bool(r["within_p90"]) for r in measured), "measured": len(measured),
        "median_minutes": _median([r["minutes"] for r in done]),
        "modes": {mode: sum(r["modes"][mode] for r in runs) for mode in ship.MODES},
        "roles": dict(sorted(roles.items(), key=lambda kv: -kv[1])),
        "refused": dict(sorted(refused.items(), key=lambda kv: -kv[1])),
    }


def advice(runs):
    """Plain suggestions for [workflow] and the estimate, from what the runs show."""
    t = totals(runs)
    out = []
    if t["measured"] >= 3 and t["median_ratio"] and t["median_ratio"] > 1.5:
        out.append(f"Thực tế gấp {t['median_ratio']:.1f}× P50 (trung vị): chạy /token-tools:token-calibrate để ước lượng sát hơn.")
    if t["measured"] >= 3 and t["median_ratio"] and t["median_ratio"] < 0.5:
        out.append(f"Thực tế chỉ {t['median_ratio']:.1f}× P50: phiếu đang ước dư, ngân sách duyệt rộng hơn cần.")
    with_kids = [r for r in runs if r["children"] and r["tokens"]]
    without = [r for r in runs if not r["children"] and r["tokens"]]
    if len(with_kids) >= 2 and len(without) >= 2:
        a = _median([r["usd"] for r in with_kids])
        b = _median([r["usd"] for r in without])
        if a and b and a > 1.5 * b:
            out.append(f"Lượt có phiên con tốn ${a:.2f} (trung vị) so với ${b:.2f} không có: cân nhắc tăng "
                       "[workflow] session_min_tokens.")
    if t["refused"]:
        top = next(iter(t["refused"]))
        out.append(f"Phiên con không mở được {sum(t['refused'].values())} lần (nhiều nhất: {top}).")
    for role, n in list(t["roles"].items())[:2]:
        if n >= 3:
            out.append(f"Vai {role} bị chặn {n} lần: xem lại quyền của vai trên Phòng điều hành (Quyền hạn).")
    done = [r for r in runs if r["result"]]
    if len(done) >= 3 and t["failed"] + t["partial"] > len(done) / 2:
        out.append("Hơn nửa số lượt không đạt trọn: tiêu chí xong có thể quá rộng, hoặc [workflow] fix_rounds quá thấp.")
    return out


def _money(value):
    return "—" if value is None else f"${value:,.2f}"


def _tok(value):
    if value is None:
        return "—"
    return f"{value / 1e6:.1f}M" if value >= 1e6 else f"{value / 1e3:.0f}k"


def text(report):
    t = report["totals"]
    lines = [f"Lượt ship {report['days']} ngày qua: {t['runs']} lượt, {t['done']} đã xong "
             f"({t['ok']} đạt · {t['partial']} một phần · {t['failed']} không đạt) · {_money(t['usd'])}"]
    if t["measured"]:
        lines.append(f"Thực tế / P50 trung vị ×{t['median_ratio']:.2f} · {t['within_p90']}/{t['measured']} trong P90")
    for run in report["runs"]:
        lines.append(f"  {run['started']:%d/%m} {RESULT_TEXT.get(run['result'], run['result']):<13} "
                     f"{_money(run['usd']):>8}  {run['title'][:50]}")
    lines += [f"• {line}" for line in report["advice"]]
    return "\n".join(lines)


def _tip(text):
    """The attribute the page's script reads to show a tooltip on hover or tap (no library, no network)."""
    return f'data-tip="{html.escape(text, quote=True)}" tabindex="0"'


def _labels(rows):
    same_day = len({r["started"].date() for r in rows}) == 1
    return (lambda r: f'{r["started"]:%H:%M}') if same_day else (lambda r: f'{r["started"]:%d/%m}')


def _frame(width, height, left, bottom, top_value, unit):
    """Axis line, gridlines at quarters, and their labels."""
    parts = [f'<line x1="{left}" y1="{bottom}" x2="{width - 4}" y2="{bottom}" stroke="var(--line)"/>']
    for q in (0.25, 0.5, 0.75, 1):
        y = bottom - q * (bottom - 16)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - 4}" y2="{y:.1f}" stroke="var(--line)" '
                     f'stroke-dasharray="2 4" opacity=".7"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{unit(top_value * q)}</text>')
    parts.append(f'<text x="{left - 6}" y="{bottom + 4}" text-anchor="end">0</text>')
    return parts


def _svg(width, height, label, parts):
    return (f'<div class="chart"><svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(label)}">'
            f'{"".join(parts)}</svg></div>')


def _chart_estimate(runs):
    """Estimate (P50–P90 band) vs actual tokens, one column per run, oldest left."""
    rows = [r for r in reversed(runs) if r["p50"] is not None or r["tokens"]][-16:]
    if not rows:
        return '<p class="muted">Chưa có lượt nào có phiếu hoặc số thực tế.</p>'
    top = max(max(r["p90"] or 0, r["tokens"]) for r in rows) or 1
    width, height, left, bottom = 720, 230, 52, 190
    step = (width - left - 10) / len(rows)
    y = lambda v: bottom - (v / top) * (bottom - 16)  # noqa: E731
    label = _labels(rows)
    parts = _frame(width, height, left, bottom, top, _tok)
    for i, r in enumerate(rows):
        x = left + i * step + step / 2
        bar = max(10, min(30, step * 0.5))
        estimate = (f"P50 {_tok(r['p50'])} · P90 {_tok(r['p90'])}" if r["p50"] is not None else "không có phiếu")
        verdict = ("" if r["ratio"] is None else
                   f" · ×{r['ratio']:.2f} P50 · {'trong' if r['within_p90'] else 'VƯỢT'} P90")
        tip = f"{r['title']} ({r['started']:%d/%m %H:%M})\nPhiếu: {estimate}\nThực tế: {_tok(r['tokens'])}{verdict}"
        group = [f'<g class="hit" {_tip(tip)}>',
                 f'<rect x="{x - step / 2 + 2:.1f}" y="8" width="{step - 4:.1f}" height="{bottom - 8}" fill="transparent"/>']
        if r["p50"] is not None:
            group.append(f'<rect x="{x - bar / 2 - 4:.1f}" y="{y(r["p90"]):.1f}" width="{bar + 8:.1f}" '
                         f'height="{max(1, y(r["p50"]) - y(r["p90"])):.1f}" fill="var(--accent-soft)" rx="3"/>')
            group.append(f'<line x1="{x - bar / 2 - 4:.1f}" x2="{x + bar / 2 + 4:.1f}" y1="{y(r["p50"]):.1f}" '
                         f'y2="{y(r["p50"]):.1f}" stroke="var(--accent)" stroke-width="2"/>')
        over = r["within_p90"] is False
        group.append(f'<rect class="col" x="{x - bar / 4:.1f}" y="{y(r["tokens"]):.1f}" width="{bar / 2:.1f}" '
                     f'height="{max(0, bottom - y(r["tokens"])):.1f}" fill="var({"--crit" if over else "--ok"})" rx="2"/>')
        group.append(f'<text x="{x:.1f}" y="{bottom + 16}" text-anchor="middle">{label(r)}</text></g>')
        parts += group
    return (_svg(width, height, "Phiếu ước lượng so với thực tế", parts) +
            '<p class="muted small">Dải xanh nhạt: P50–P90 của phiếu (vạch đậm là P50) · cột: token thực tế '
            '(đỏ khi vượt P90). Rê chuột hoặc chạm vào một lượt để xem số.</p>')


def _chart_cost(runs):
    """$ per run, stacked: the run's own session, then its child sessions."""
    rows = [r for r in reversed(runs) if r["usd"]][-16:]
    if not rows:
        return '<p class="muted">Chưa có chi phí nào được ghi.</p>'
    top = max(r["usd"] for r in rows) or 1
    width, height, left, bottom = 720, 210, 52, 170
    step = (width - left - 10) / len(rows)
    h = lambda v: (v / top) * (bottom - 16)  # noqa: E731
    label = _labels(rows)
    parts = _frame(width, height, left, bottom, top, lambda v: f"${v:.2f}" if v < 10 else f"${v:.0f}")
    for i, r in enumerate(rows):
        x = left + i * step + step / 2
        bar = max(12, min(34, step * 0.55))
        own = r["usd"] - r["usd_children"]
        tip = (f"{r['title']} ({r['started']:%d/%m %H:%M})\nTổng {_money(r['usd'])} · {_tok(r['tokens'])} token\n"
               f"Phiên chính {_money(own)}" + (f" · {len(r['children'])} phiên con {_money(r['usd_children'])}"
                                                if r["children"] else "") + f"\n{r['minutes']} phút")
        parts += [f'<g class="hit" {_tip(tip)}>',
                  f'<rect x="{x - step / 2 + 2:.1f}" y="8" width="{step - 4:.1f}" height="{bottom - 8}" fill="transparent"/>',
                  f'<rect class="col" x="{x - bar / 2:.1f}" y="{bottom - h(own):.1f}" width="{bar:.1f}" '
                  f'height="{h(own):.1f}" fill="var(--accent)" rx="2"/>']
        if r["usd_children"]:
            parts.append(f'<rect class="col" x="{x - bar / 2:.1f}" y="{bottom - h(r["usd"]):.1f}" width="{bar:.1f}" '
                         f'height="{h(r["usd_children"]):.1f}" fill="var(--budget)" rx="2"/>')
        parts.append(f'<text x="{x:.1f}" y="{bottom + 16}" text-anchor="middle">{label(r)}</text></g>')
    return (_svg(width, height, "Chi phí từng lượt", parts) +
            '<p class="muted small"><span class="sw" style="background:var(--accent)"></span>phiên chạy lệnh '
            '<span class="sw" style="background:var(--budget)"></span>phiên con · giá API quy đổi.</p>')


def _chart_bars(items, color, empty):
    """Horizontal bars, one per (label, count, tip) — for steps by mode and for where runs got stuck."""
    if not items:
        return f'<p class="muted">{html.escape(empty)}</p>'
    top = max(n for _, n, _ in items) or 1
    rows = []
    for name, n, tip in items:
        rows.append(f'<div class="hrow hit" {_tip(tip)}><span class="hl">{html.escape(name)}</span>'
                    f'<span class="ht"><span class="hb" style="width:{max(3, n * 100 / top):.1f}%;background:var({color})">'
                    f'</span></span><span class="hn mono">{n}</span></div>')
    return f'<div class="hbars">{"".join(rows)}</div>'


TIP_JS = """
(() => {
  const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; document.body.appendChild(tip);
  let pinned = null;
  const show = (el, x, y) => {
    tip.textContent = el.dataset.tip; tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight, pad = 12;
    let left = x + pad, top = y + pad;
    if (left + w > innerWidth - 8) left = Math.max(8, x - w - pad);
    if (top + h > innerHeight - 8) top = Math.max(8, y - h - pad);
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
    document.querySelectorAll('.hit.on').forEach(n => n.classList.remove('on')); el.classList.add('on');
  };
  const hide = () => { tip.hidden = true; document.querySelectorAll('.hit.on').forEach(n => n.classList.remove('on')); };
  document.addEventListener('pointermove', ev => {
    if (pinned || ev.pointerType === 'touch') return;
    const el = ev.target.closest('[data-tip]'); el ? show(el, ev.clientX, ev.clientY) : hide();
  });
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-tip]');
    if (!el || el === pinned) { pinned = null; hide(); return; }
    pinned = el; show(el, ev.clientX, ev.clientY);
  });
  document.addEventListener('focusin', ev => {
    const el = ev.target.closest('[data-tip]'); if (!el) return;
    const r = el.getBoundingClientRect(); show(el, r.left + r.width / 2, r.top);
  });
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape') { pinned = null; hide(); } });
  addEventListener('scroll', () => { if (!pinned) hide(); }, { passive: true });
})();
"""


STYLE_EXTRA = """
.small { font-size:12.5px; } details { border-top:1px solid var(--line); padding:8px 0; }
details summary { cursor:pointer; list-style:none; display:grid; grid-template-columns:minmax(104px,auto) 1fr auto; gap:10px; align-items:baseline; }
details summary::-webkit-details-marker { display:none; } details[open] summary { font-weight:600; }
.res { white-space:nowrap; text-align:center; font:500 12px/1 "IBM Plex Mono",Consolas,monospace; padding:4px 8px; border-radius:6px; }
.r-ok { background:var(--ok-soft); color:var(--ok); } .r-partial { background:var(--warn-soft); color:var(--warn); }
.r-failed { background:var(--crit-soft); color:var(--crit); } .r-none { background:var(--code-bg); color:var(--muted); }
.steps { display:flex; flex-direction:column; gap:4px; margin:8px 0 0 114px; font-size:13.5px; }
.tip { position:fixed; z-index:10; max-width:320px; white-space:pre-line; pointer-events:none; background:var(--ink);
  color:var(--bg); font:12.5px/1.45 "Be Vietnam Pro","Segoe UI",system-ui,sans-serif; padding:8px 10px; border-radius:8px;
  box-shadow:0 4px 16px rgba(0,0,0,.18); }
.hit { cursor:pointer; outline:none; } .hit.on .col, .hit:focus .col { opacity:.75; } .hit.on rect[fill="transparent"] { fill:var(--code-bg); }
.hbars { display:flex; flex-direction:column; gap:6px; } .hrow { display:grid; grid-template-columns:minmax(120px,32%) 1fr 36px; gap:10px; align-items:center; padding:2px 4px; border-radius:6px; }
.hrow.on, .hrow:focus { background:var(--code-bg); } .hl { font-size:13.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ht { height:12px; background:var(--code-bg); border-radius:6px; overflow:hidden; } .hb { display:block; height:100%; border-radius:6px; }
.hn { text-align:right; font-size:13px; } .sw { display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 4px 0 8px; vertical-align:-1px; }
.grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; }
@media (max-width:600px) { details summary { grid-template-columns:1fr; } .steps { margin-left:0; } }
"""


def page(report):
    from ctt.estimate.render import STYLE
    e = html.escape
    t = report["totals"]
    done = t["done"] or 1
    kpis = [
        (str(t["runs"]), "lượt", f"{t['done']} đã xong"),
        (f"{t['ok'] * 100 // done}%" if t["done"] else "—", "đạt trọn", f"{t['partial']} một phần · {t['failed']} không đạt"),
        (_money(t["usd"]), "tổng chi phí quy đổi", f"{_tok(t['tokens'])} token, gồm phiên con"),
        (f"×{t['median_ratio']:.2f}" if t["median_ratio"] else "—", "thực tế / P50 (trung vị)",
         f"{t['within_p90']}/{t['measured']} lượt trong P90" if t["measured"] else "chưa có lượt nào có phiếu"),
        (f"{t['median_minutes']:.0f} phút" if t["median_minutes"] is not None else "—", "thời gian một lượt (trung vị)", ""),
    ]
    kpi_html = "".join(f'<div class="kpi"><span class="v">{e(v)}</span><span class="l">{e(l)}</span>'
                       f'<span class="s">{e(s)}</span></div>' for v, l, s in kpis)
    modes = t["modes"]
    all_steps = sum(modes.values())
    by_role = {}
    for r in report["runs"]:
        for s in r["plan"]:
            key = (s.get("role") or "—", s.get("mode"))
            by_role[key] = by_role.get(key, 0) + 1
    mode_items = [(MODE_TEXT[m], modes[m], f"{MODE_TEXT[m]}: {modes[m]} / {all_steps} bước "
                   f"({modes[m] * 100 // (all_steps or 1)}%)\n" + ", ".join(
                       f"{role} {n}" for (role, mode), n in sorted(by_role.items()) if mode == m))
                  for m in ship.MODES if modes[m]]
    mode_html = _chart_bars(mode_items, "--accent", "Chưa có bảng chia việc nào.")
    run_rows = []
    for r in report["runs"]:
        cls = {"ok": "r-ok", "partial": "r-partial", "failed": "r-failed"}.get(r["result"], "r-none")
        status = RESULT_TEXT.get(r["result"], r["result"]) if r["result"] else f"bước {r['stage']} · {r['stage_name']}"
        steps = "".join(
            f'<div><span class="mono muted">[{e(s.get("status", ""))}]</span> {e(s.get("name", ""))} · '
            f'{e(s.get("role") or "—")} · {e(MODE_TEXT.get(s.get("mode"), s.get("mode", "")))}'
            f'{" · " + e(s["note"]) if s.get("note") else ""}</div>' for s in r["plan"]) or \
            '<div class="muted">Chưa có bảng chia việc.</div>'
        extra = []
        if r["p50"] is not None:
            extra.append(f"Phiếu P50 {_tok(r['p50'])} / P90 {_tok(r['p90'])} ({_money(r['usd_p50'])} P50) · thực tế "
                         f"{_tok(r['tokens'])}" + (f" (×{r['ratio']:.2f})" if r["ratio"] is not None else ""))
        if r["children"]:
            extra.append(f"{len(r['children'])} phiên con: chi phí đã cộng vào lượt")
        if r["roles"]:
            extra.append("Bị chặn theo vai: " + ", ".join(f"{k} {v}" for k, v in r["roles"].items()))
        if r["refused"]:
            extra.append("Phiên con không mở được: " + ", ".join(f"{k} {v}" for k, v in r["refused"].items()))
        extra += [f"Ghi chú: {n}" for n in r["notes"]]
        extra_html = "".join(f'<div class="muted">{e(x)}</div>' for x in extra)
        run_rows.append(
            f'<details><summary><span class="res {cls}">{e(status)}</span>'
            f'<span>{e(r["title"])} <span class="muted small">· {r["started"]:%d/%m %H:%M} · {r["minutes"]} phút</span></span>'
            f'<span class="mono">{_money(r["usd"])}</span></summary>'
            f'<div class="steps">{steps}{extra_html}</div></details>')
    runs_html = "".join(run_rows) or '<p class="muted">Chưa có lượt /token-tools:ship nào trong khoảng này.</p>'

    stuck = [(f"Vai {k} bị chặn", v, f"Vai {k} bị chặn {v} lần theo quyền của vai\nSửa ở Phòng điều hành → Quyền hạn")
             for k, v in t["roles"].items()]
    stuck += [(f"Phiên con: {k}", v, f"Phiên con không mở được vì {k}: {v} lần") for k, v in t["refused"].items()]
    failed = {}
    for r in report["runs"]:
        for s in r["failed_steps"]:
            failed.setdefault(s, []).append(r["title"])
    stuck += [(f"Bước lỗi: {s}", len(titles), "Lỗi ở: " + "; ".join(titles)) for s, titles in list(failed.items())[:8]]
    friction_html = _chart_bars(stuck, "--crit", "Không thấy chỗ vướng nào.")
    advice_html = ("".join(f'<div class="note">{e(x)}</div>' for x in report["advice"]) if report["advice"]
                   else '<p class="muted">Chưa đủ số liệu để gợi ý (cần vài lượt đã xong có phiếu).</p>')

    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Báo cáo ship</title><style>{STYLE}{STYLE_EXTRA}</style></head><body><div class="wrap">
<header><h1>Báo cáo /token-tools:ship</h1><p class="muted">{report['days']} ngày đến {report['at']:%d/%m/%Y %H:%M} UTC ·
chi phí là giá API quy đổi từ transcript local, gồm cả phiên con của mỗi lượt</p></header>
<section class="kpis">{kpi_html}</section>
<section class="box"><h2>Phiếu so với thực tế</h2>{_chart_estimate(report['runs'])}</section>
<section class="box"><h2>Chi phí từng lượt</h2>{_chart_cost(report['runs'])}</section>
<div class="grid2"><section class="box"><h2>Cách chạy các bước</h2>{mode_html}</section>
<section class="box"><h2>Chỗ hay vướng</h2>{friction_html}</section></div>
<section class="box"><h2>Các lượt</h2><div>{runs_html}</div></section>
<section class="box"><h2>Gợi ý</h2>{advice_html}</section>
</div><script>{TIP_JS}</script></body></html>"""


def write(report, out_dir=None):
    folder = Path(out_dir) if out_dir else config.reports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"ship-{report['at']:%Y-%m-%d}.html"
    path.write_text(page(report), encoding="utf-8")
    return path
