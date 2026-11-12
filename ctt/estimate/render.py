"""Estimate as a short Markdown card (chat) and a self-contained HTML page (opened locally, theme-aware)."""
import html
import math

from ctt.estimate.engine import LEVEL_NAMES

CONFIDENCE_NAMES = {"high": "Cao", "medium": "Trung bình", "low": "Thấp"}


def mtok(value):
    return f"{value / 1e6:,.1f}M" if value >= 1e5 else f"{value / 1e3:,.0f}k"


def pct(value):
    return f"{value:.1f}%"


def text_card(est):
    weekly = est["weekly_percent"]
    lines = [f"## Phiếu ước lượng — {est['title']}",
             f"`{est['id']}` · rủi ro **{LEVEL_NAMES[est['risk_level']]}** ({est['risk_points']} điểm) · "
             f"tin cậy **{CONFIDENCE_NAMES[est['confidence']]}**"
             + (f" ({'; '.join(est['confidence_reasons'])})" if est["confidence_reasons"] else ""),
             "", "| | P50 | P90 | Ngân sách |", "|---|---|---|---|",
             f"| Token | {mtok(est['tokens']['p50'])} | {mtok(est['tokens']['p90'])} | {mtok(est['tokens']['budget'])} |",
             f"| $ quy đổi | {est['usd']['p50']:.2f} | {est['usd']['p90']:.2f} | {est['usd']['budget']:.2f} |"]
    if weekly:
        lines.append(f"| % hạn mức tuần | {pct(weekly['p50'])} | {pct(weekly['p90'])} | {pct(weekly['budget'])} |")
    for part in est["parts"]:
        lines += ["", f"**{part['name']}** — {part['label_name']}, ~{part['files_estimate']} file",
                  "", "| Bước | Model | Token P50 → P90 | $ P50 → P90 |", "|---|---|---|---|"]
        for step in part["steps"]:
            lines.append(f"| {step['name']} | {step['model']} · {step['effort']} | {mtok(step['p50_tokens'])} → "
                         f"{mtok(step['p90_tokens'])} | {step['p50_usd']:.2f} → {step['p90_usd']:.2f} |")
        lines.append("")
        if part["risks"]:
            lines.append("Rủi ro: " + ", ".join(f"{r['name']} (+{r['points']})" for r in part["risks"]))
        if part["gates"]:
            lines.append("Cổng kiểm tra: " + "; ".join(part["gates"]))
    budget = est["tokens"]["budget"]
    lines += ["", "Điểm báo: " + " · ".join(f"{int(c * 100)}% ({mtok(budget * c)})" for c in est["checkpoints"])]
    if est["unknowns"]:
        lines += ["", "Câu hỏi mở:"] + [f"- {q}" for q in est["unknowns"]]
    lines += ["", "Giả định:"] + [f"- {a}" for a in est["assumptions"]]
    return "\n".join(lines)


def _nice_max(value):
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def range_svg(est):
    """Token scale with the P50–P90 band, checkpoints and budget, drawn to scale."""
    left, right = 40, 760
    top = _nice_max(est["tokens"]["budget"] * 1.02)
    x = lambda v: left + (right - left) * v / top  # noqa: E731
    p50, p90, budget = est["tokens"]["p50"], est["tokens"]["p90"], est["tokens"]["budget"]
    ticks = "".join(
        f'<line x1="{x(top * i / 4):.1f}" y1="64" x2="{x(top * i / 4):.1f}" y2="76"/>' for i in range(5))
    tick_labels = "".join(
        f'<text x="{x(top * i / 4):.1f}" y="94" text-anchor="middle">{mtok(top * i / 4) if i else "0"}</text>'
        for i in range(5))
    marks = "".join(
        f'<line x1="{x(budget * c):.1f}" y1="50" x2="{x(budget * c):.1f}" y2="90" stroke="var(--budget)" '
        f'stroke-width="1.5" stroke-dasharray="4 3"/><text x="{x(budget * c):.1f}" y="112" text-anchor="middle">'
        f'báo {int(c * 100)}%</text>' for c in est["checkpoints"] if c < 1)
    return f'''<svg viewBox="0 0 800 120" role="img" aria-label="Thang token: P50 {mtok(p50)}, P90 {mtok(p90)}, ngân sách {mtok(budget)}">
<g stroke="var(--line)" stroke-width="1"><line x1="{left}" y1="70" x2="{right}" y2="70"/>{ticks}</g>{tick_labels}
<rect x="{x(p50):.1f}" y="56" width="{max(1, x(p90) - x(p50)):.1f}" height="28" rx="6" fill="var(--accent-soft)"/>
<line x1="{x(p50):.1f}" y1="40" x2="{x(p50):.1f}" y2="84" stroke="var(--accent)" stroke-width="2"/>
<text x="{x(p50):.1f}" y="34" text-anchor="middle" class="strong">P50 {mtok(p50)}</text>
<line x1="{x(p90):.1f}" y1="40" x2="{x(p90):.1f}" y2="84" stroke="var(--accent)" stroke-width="2"/>
<text x="{x(p90):.1f}" y="34" text-anchor="middle" class="strong">P90 {mtok(p90)}</text>{marks}
<line x1="{x(budget):.1f}" y1="40" x2="{x(budget):.1f}" y2="90" stroke="var(--budget)" stroke-width="2.5"/>
<text x="{x(budget):.1f}" y="18" text-anchor="end" class="strong" style="fill:var(--budget)">Ngân sách {mtok(budget)}</text>
</svg>'''


def weekly_svg(weekly, used=None):
    left, right = 40, 760
    x = lambda v: left + (right - left) * min(v, 100) / 100  # noqa: E731
    base = used or 0
    used_bar = (f'<rect x="{left}" y="18" width="{x(base) - left:.1f}" height="22" rx="5" fill="var(--muted)" opacity="0.45"/>'
                f'<text x="{x(base):.1f}" y="58" text-anchor="middle">{base:.0f}% đã dùng</text>') if used else ""
    return f'''<svg viewBox="0 0 800 70" role="img" aria-label="Hạn mức tuần: task thêm {pct(weekly['p50'])} đến {pct(weekly['p90'])}">
<rect x="{left}" y="18" width="{right - left}" height="22" rx="5" fill="var(--code-bg)"/>{used_bar}
<rect x="{x(base):.1f}" y="18" width="{x(base + weekly['p50']) - x(base):.1f}" height="22" fill="var(--accent)"/>
<rect x="{x(base + weekly['p50']):.1f}" y="18" width="{x(base + weekly['p90']) - x(base + weekly['p50']):.1f}" height="22" fill="var(--accent)" opacity="0.4"/>
<text x="{min(x(base + weekly['p90']) + 6, 700):.1f}" y="12" class="strong">+{pct(weekly['p50'])} → +{pct(weekly['p90'])}</text>
<text x="{left}" y="58">0%</text><text x="{right}" y="58" text-anchor="end">100%</text></svg>'''


STYLE = """
:root { --bg:#f5f7fa; --surface:#fff; --ink:#16202b; --muted:#5a6575; --line:#dce2ea; --accent:#1f5fbf; --accent-soft:#e4ecfa;
  --budget:#a8661b; --budget-soft:#f6ead9; --ok:#2e7d55; --ok-soft:#e1f1e8; --warn:#a8661b; --warn-soft:#f6ead9;
  --crit:#c0392b; --crit-soft:#f8e1de; --code-bg:#eef2f7; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f141b; --surface:#161d27; --ink:#e5eaf1; --muted:#97a2b2; --line:#29333f;
  --accent:#78a6ff; --accent-soft:#1b2940; --budget:#e2a656; --budget-soft:#3a2c19; --ok:#63c08e; --ok-soft:#173326;
  --warn:#e2a656; --warn-soft:#3a2c19; --crit:#f07a6c; --crit-soft:#3d1f1c; --code-bg:#1c2430; color-scheme: dark; } }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.6 "Be Vietnam Pro","Segoe UI",system-ui,sans-serif; }
.wrap { max-width:900px; margin:0 auto; padding:28px max(16px, env(safe-area-inset-left, 0px)) 48px; display:flex; flex-direction:column; gap:20px; }
h1 { font-size:22px; margin:0; } h2 { font-size:16px; margin:0; }
.muted { color:var(--muted); } .mono { font-family:"IBM Plex Mono",Consolas,monospace; }
.chips { display:flex; flex-wrap:wrap; gap:8px; }
.pill { font:500 12px/1 "IBM Plex Mono",Consolas,monospace; padding:5px 9px; border-radius:6px; background:var(--code-bg); color:var(--muted); }
.low { background:var(--ok-soft); color:var(--ok); } .mid { background:var(--warn-soft); color:var(--warn); } .high { background:var(--crit-soft); color:var(--crit); }
.opus { background:var(--accent-soft); color:var(--accent); } .sonnet { background:var(--ok-soft); color:var(--ok); }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }
.kpi { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 14px; display:flex; flex-direction:column; gap:4px; }
.kpi .v { font:600 19px/1.2 "IBM Plex Mono",Consolas,monospace; font-variant-numeric:tabular-nums; }
.kpi .l, .kpi .s { font-size:12.5px; color:var(--muted); }
.box { background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:16px; display:flex; flex-direction:column; gap:10px; min-width:0; }
.chart { overflow-x:auto; } .chart svg { display:block; width:100%; min-width:560px; height:auto; }
.chart text { font:11px "IBM Plex Mono",Consolas,monospace; fill:var(--muted); } .chart .strong { fill:var(--ink); font-weight:500; }
.tbl { overflow-x:auto; } table { border-collapse:collapse; width:100%; font-size:14px; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-size:12px; color:var(--muted); font-weight:600; } td.n { text-align:right; font-family:"IBM Plex Mono",Consolas,monospace; font-variant-numeric:tabular-nums; white-space:nowrap; }
ul { margin:0; padding-left:20px; } .note { border-left:3px solid var(--budget); background:var(--budget-soft); padding:10px 12px; border-radius:0 8px 8px 0; }
"""


def html_page(est, weekly_used=None):
    e = html.escape
    weekly = est["weekly_percent"]
    kpis = [("Token P50 → P90", f"{mtok(est['tokens']['p50'])} → {mtok(est['tokens']['p90'])}", ""),
            ("Chi phí quy đổi", f"${est['usd']['p50']:.2f} → ${est['usd']['p90']:.2f}", "Theo model từng bước"),
            ("Ngân sách cấp", mtok(est["tokens"]["budget"]), f"≈ ${est['usd']['budget']:.2f}")]
    if weekly:
        kpis.insert(2, ("Hạn mức tuần", f"{pct(weekly['p50'])} → {pct(weekly['p90'])}",
                        f"${weekly['usd_per_percent']:.2f} / 1% (cận dưới)"))
    kpi_html = "".join(f'<div class="kpi"><span class="l">{e(l)}</span><span class="v">{e(v)}</span>'
                       f'<span class="s">{e(s)}</span></div>' for l, v, s in kpis)
    parts_html = ""
    for part in est["parts"]:
        rows = "".join(
            f'<tr><td>{e(s["name"])}</td><td><span class="pill {e(s["model"])}">{e(s["model"])} · {e(s["effort"])}</span></td>'
            f'<td class="n">{mtok(s["p50_tokens"])}</td><td class="n">{mtok(s["p90_tokens"])}</td>'
            f'<td class="n">{s["p50_usd"]:.2f} → {s["p90_usd"]:.2f}</td></tr>' for s in part["steps"])
        risks = "".join(f'<span class="pill {part["risk_level"]}">{e(r["name"])} +{r["points"]}</span>' for r in part["risks"])
        gates = "".join(f"<li>{e(g)}</li>" for g in part["gates"])
        parts_html += (f'<div class="box"><h2>{e(part["name"])}</h2><p class="muted" style="margin:0">{e(part["label_name"])} · '
                       f'~{part["files_estimate"]} file · {part["band"]["samples"]} phiên mẫu ({e(part["band"]["source"])})</p>'
                       f'<div class="tbl"><table><thead><tr><th>Bước</th><th>Model</th><th>Token P50</th><th>Token P90</th>'
                       f'<th>$ P50 → P90</th></tr></thead><tbody>{rows}</tbody></table></div>'
                       + (f'<div class="chips">{risks}</div>' if risks else "")
                       + (f"<ul>{gates}</ul>" if gates else "") + "</div>")
    budget = est["tokens"]["budget"]
    stop = next((c for c in est["checkpoints"] if c >= 0.8), 0.8)
    unknowns = "".join(f"<li>{e(q)}</li>" for q in est["unknowns"])
    sources = "".join(f'<li>{e(s.get("type", ""))}: {e(str(s.get("ref", "")))}'
                      + (" — <em>chưa đọc hết</em>" if s.get("complete") is False else "") + "</li>" for s in est["sources"])
    assumptions = "".join(f"<li>{e(a)}</li>" for a in est["assumptions"])
    level = est["risk_level"]
    return f'''<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phiếu {e(est["id"])}</title><style>{STYLE}</style></head><body><div class="wrap">
<div style="display:flex;flex-direction:column;gap:8px"><span class="muted mono" style="font-size:12px">{e(est["id"])} · {e(est["created_at"])}</span>
<h1>{e(est["title"])}</h1><div class="chips"><span class="pill {level}">Rủi ro {LEVEL_NAMES[level].lower()} · {est["risk_points"]} điểm</span>
<span class="pill">Tin cậy: {CONFIDENCE_NAMES[est["confidence"]].lower()}</span></div></div>
<div class="kpis">{kpi_html}</div>
<div class="box"><div class="chart">{range_svg(est)}</div></div>
{f'<div class="box"><h2>Hạn mức tuần nếu làm task này</h2><div class="chart">{weekly_svg(weekly, weekly_used)}</div></div>' if weekly else ""}
{parts_html}
<div class="note"><strong>Điểm dừng:</strong> qua {int(stop * 100)}% ngân sách ({mtok(budget * stop)} token) mà chưa xong phần chính thì dừng và hỏi: tóm tắt, mở phiên mới, hoặc nâng model.</div>
{f'<div class="box"><h2>Câu hỏi mở</h2><ul>{unknowns}</ul></div>' if unknowns else ""}
<div class="box"><h2>Nguồn đã đọc</h2><ul>{sources or "<li>Chỉ có mô tả</li>"}</ul></div>
<div class="box"><h2>Giả định</h2><ul>{assumptions}</ul></div>
</div></body></html>'''
