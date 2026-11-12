"""Text reports and CSV exports."""
import csv
from collections import defaultdict
from datetime import date, timedelta

from ctt.sessions import percentile


def mtok(value):
    return f"{value / 1e6:,.1f}M" if value >= 1e5 else f"{value / 1e3:,.0f}k"


def usage_report(scan, sessions, prices, days, out_dir, today=None):
    """Tokens and API-equivalent cost by day and project (requests inside the period), plus top sessions."""
    today = today or date.today()
    since = today - timedelta(days=days - 1)
    by_day = defaultdict(lambda: defaultdict(float))
    day_sessions = defaultdict(set)
    by_project = defaultdict(lambda: [0, 0.0])
    for request in scan.requests:
        day = request.timestamp.date()
        if day < since:
            continue
        usd = prices.cost(request)[0]
        row = by_day[day]
        row["output"] += request.output_tokens
        row["write"] += request.cache_write
        row["read"] += request.cache_read
        row["total"] += request.total
        row["cost"] += usd
        day_sessions[day].add(request.session_id)
        meta = scan.sessions.get(request.session_id)
        project = by_project[meta.project if meta else "?"]
        project[0] += request.total
        project[1] += usd

    def total_of(key):
        return sum(row[key] for row in by_day.values())

    lines = [f"=== THEO NGÀY ({since} → {today}) — chi phí là giá API quy đổi ===",
             f"{'Ngày':<12}{'Phiên':>6}{'Output':>9}{'Ghi cache':>11}{'Đọc cache':>11}{'Tổng':>10}{'$':>10}"]
    for day in sorted(by_day):
        row = by_day[day]
        lines.append(f"{day!s:<12}{len(day_sessions[day]):>6}{mtok(row['output']):>9}{mtok(row['write']):>11}"
                     f"{mtok(row['read']):>11}{mtok(row['total']):>10}{row['cost']:>10,.2f}")
    lines.append(f"{'TỔNG':<18}{mtok(total_of('output')):>9}{mtok(total_of('write')):>11}{mtok(total_of('read')):>11}"
                 f"{mtok(total_of('total')):>10}{total_of('cost'):>10,.2f}")

    lines += ["", "=== THEO PROJECT (trong kỳ) ==="]
    for project, (tokens, cost) in sorted(by_project.items(), key=lambda item: -item[1][0]):
        if tokens:
            lines.append(f"{project:<40}{mtok(tokens):>10}  ${cost:,.2f}")

    lines += ["", "=== TOP 15 PHIÊN (cả phiên, kể cả phần trước kỳ) ==="]
    for session in sorted(sessions, key=lambda s: -s.total)[:15]:
        lines.append(f"{mtok(session.total):>9} ${session.cost:>8.2f} {session.last:%Y-%m-%d} "
                     f"[{session.project[:18]}] {session.title[:70]}")

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "usage_by_day.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["day", "sessions", "output", "cache_write", "cache_read", "total", "cost_usd"])
        for day in sorted(by_day):
            row = by_day[day]
            writer.writerow([day, len(day_sessions[day]), int(row["output"]), int(row["write"]), int(row["read"]),
                             int(row["total"]), round(row["cost"], 4)])
    lines.append(f"\nCSV: {out_dir / 'usage_by_day.csv'}")
    return "\n".join(lines)


def session_rows(sessions):
    return [{
        "session_id": s.session_id, "project": s.project, "branch": s.branch, "title": s.title, "label": s.label,
        "multi_day": s.multi_day, "active_days": len(s.days), "requests": s.requests,
        "subagent_share": round(s.subagent_requests / s.requests, 2) if s.requests else 0,
        "avg_context": s.avg_context, "max_context": s.context_max, "files_edited": len(s.files),
        "migration": s.migration, "builds": s.builds, "commits": s.commits, "total_tokens": s.total,
        "read": s.read, "write": s.write, "output": s.output, "cost_usd": round(s.cost, 2),
        "unknown_model_requests": s.unknown_model_requests, "main_model": s.main_model,
        "first": f"{s.first:%Y-%m-%d %H:%M}", "last": f"{s.last:%Y-%m-%d %H:%M}",
    } for s in sessions]


def calibration_report(sessions, since, out_dir):
    """P50/P90 tokens and cost per task label."""
    groups = defaultdict(list)
    for session in sessions:
        groups[session.label].append(session)
    lines = [f"=== {len(sessions)} phiên hoạt động từ {since} (giá API quy đổi) ===", "",
             f"{'Loại task':<52}{'N':>4}{'Token P50':>11}{'P90':>9}{'$ P50':>8}{'$ P90':>8}{'Lượt P50':>10}"
             f"{'Ctx TB P50':>12}{'Nhiều ngày':>12}"]
    for name in sorted(groups):
        items = groups[name]
        tokens = [s.total for s in items]
        costs = [s.cost for s in items]
        lines.append(f"{name:<52}{len(items):>4}{mtok(percentile(tokens, .5)):>11}{mtok(percentile(tokens, .9)):>9}"
                     f"{percentile(costs, .5):>8.2f}{percentile(costs, .9):>8.2f}"
                     f"{percentile([s.requests for s in items], .5):>10}"
                     f"{percentile([s.avg_context for s in items], .5) // 1000:>10}k"
                     f"{sum(s.multi_day for s in items):>10}/{len(items)}")
    lines.append(f"\nTổng quy đổi: ${sum(s.cost for s in sessions):,.2f}")
    unknown = sum(s.unknown_model_requests for s in sessions)
    if unknown:
        lines.append(f"⚠ {unknown} request dùng model không có trong bảng giá — đang tính theo giá fallback")

    lines += ["", "=== Phiên có sửa code: 1 ngày vs nhiều ngày ==="]
    for multi in (False, True):
        subset = [s for s in sessions if s.multi_day == multi and s.files]
        if subset:
            lines.append(f"{'Nhiều ngày' if multi else 'Trong 1 ngày':<14} N={len(subset):<3} "
                         f"token P50 {mtok(percentile([s.total for s in subset], .5))}  "
                         f"P90 {mtok(percentile([s.total for s in subset], .9))}  "
                         f"$ P50 {percentile([s.cost for s in subset], .5):.2f}")

    lines += ["", "=== Top 10 phiên tốn nhất ==="]
    for s in sorted(sessions, key=lambda s: -s.cost)[:10]:
        lines.append(f"${s.cost:>8.2f} {mtok(s.total):>7} {s.requests:>5} lượt ctx~{s.avg_context // 1000}k "
                     f"{len(s.files):>3} file {len(s.days)}d [{s.project[:14]}] {s.label[:2]} {s.title[:60]}")

    rows = session_rows(sorted(sessions, key=lambda s: s.last, reverse=True))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "calibration_sessions.csv"
    with out_file.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["session_id"])
        writer.writeheader()
        writer.writerows(rows)
    lines.append(f"\nCSV: {out_file}")
    return "\n".join(lines)
