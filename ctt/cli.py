"""Command line: report | calibrate | sync."""
import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from ctt import __version__, config, limits
from ctt.policy import load_policy, sync
from ctt.pricing import load_prices
from ctt.reports import calibration_report, usage_report
from ctt.sessions import aggregate
from ctt.transcripts import scan


def _load(args):
    data = scan(args.projects_dir)
    if data.bad_lines:
        print(f"⚠ Bỏ qua {data.bad_lines} dòng transcript không đọc được", file=sys.stderr)
    return data, load_prices(args.prices)


def cmd_report(args):
    data, prices = _load(args)
    since = date.today() - timedelta(days=args.days - 1)
    print(usage_report(data, aggregate(data, prices, since), prices, args.days, Path(args.out)))


def cmd_calibrate(args):
    data, prices = _load(args)
    since = date.today() - timedelta(days=args.days - 1)
    print(calibration_report(aggregate(data, prices, since), since, Path(args.out)))


def cmd_sync(args):
    result = sync(load_policy(args.policy), apply=args.apply, user_settings=args.user_settings,
                  user_agents=args.user_agents, extra_roots=args.root, agents_only=args.agents_only)
    mode = "ĐÃ GHI" if args.apply else "DRY RUN — chưa ghi, thêm --apply để ghi"
    print(f"\n{mode}: agent {result.agents_changed} đổi, {result.agents_unchanged} đã đúng, "
          f"{result.agents_skipped} bỏ qua; settings {result.settings_changed} đổi")


def cmd_init(args):
    """Create or update the user's policy file with the folders that hold their projects."""
    path = config.ctt_home() / "policy.toml"
    roots = [str(Path(root).expanduser().resolve()) for root in args.root]
    missing = [root for root in roots if not Path(root).is_dir()]
    if missing:
        sys.exit(f"Không thấy thư mục: {', '.join(missing)}")
    roots_line = "roots = [" + ", ".join(f"'{root}'" for root in roots) + "]"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^roots\s*=.*$", text):
            text = re.sub(r"(?m)^roots\s*=.*$", lambda _: roots_line, text, count=1)
        else:
            text = text.rstrip("\n") + f"\n\n[scan]\n{roots_line}\n"
    else:
        text = ("# Policy riêng của máy này — ghi đè lên defaults/policy.toml của claude-token-tools.\n"
                f"[scan]\n{roots_line}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"Đã ghi {path}\n{roots_line}")


def cmd_estimate(args):
    from ctt.estimate import engine, ledger, profile as profile_module, render  # noqa: F811
    risks = engine.load_risks()
    try:
        task = profile_module.load(args.profile, risks)
    except profile_module.ProfileError as error:
        sys.exit(f"Hồ sơ task chưa hợp lệ:\n{error}")
    settings = engine.load_settings()
    data, prices = _load(args)
    since = date.today() - timedelta(days=settings["days"] - 1)
    usd_per_percent, _ = limits.median_usd_per_percent(limits.load(config.ctt_home() / "plan_snapshots.jsonl"))
    est = engine.estimate(task, aggregate(data, prices, since), prices, load_policy(), settings, risks, usd_per_percent)
    if not args.no_save:  # a re-run of the same profile gets its own id, so approving one never hits another
        existing = ledger.load()
        base, suffix = est["id"], 2
        while est["id"] in existing:
            est["id"] = f"{base}-{suffix}"
            suffix += 1

    session = None
    if args.session == "auto":
        session = ledger.current_session(args.projects_dir, Path(args.cwd))
    elif args.session != "none":
        path = next(Path(args.projects_dir).glob(f"*/{args.session}.jsonl"), None)
        session = (args.session, str(path), path.stat().st_size) if path else None
    print(render.text_card(est))
    out_dir = config.ctt_home() / "estimates"
    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / f"{est['id']}.html"
    page.write_text(render.html_page(est, args.week_used), encoding="utf-8")
    if not args.no_save:
        ledger.append({"event": "created", "estimate": est,
                       "session_id": session[0] if session else None,
                       "transcript_path": session[1] if session else None,
                       "start_offset": session[2] if session else 0})
    print(f"\nTrang phiếu: {page}")
    print(f"Gắn với phiên: {session[0] if session else 'không'}"
          + ("" if args.no_save else f" · duyệt ngân sách: ctt estimate-approve {est['id']}"))


def cmd_estimate_approve(args):
    from ctt.estimate import ledger
    records = ledger.load()
    if args.id not in records:
        sys.exit(f"Không thấy phiếu {args.id}")
    ledger.append({"event": "approved", "id": args.id, "at": ledger.now_iso()})
    record = records[args.id]
    print(f"Đã duyệt {args.id}: ngân sách {record['estimate']['tokens']['budget'] / 1e6:.1f}M token"
          + (f", hook theo dõi phiên {record['session_id']}" if record["session_id"] else " (không gắn phiên nào)"))


def cmd_estimate_check(args):
    from ctt.estimate import ledger
    records = ledger.load()
    if args.id:
        records = {k: v for k, v in records.items() if k == args.id}
    data, prices = _load(args)
    rows = ledger.check(records, data, prices)
    print(f"{'Phiếu':<22}{'Trạng thái':<11}{'P50':>9}{'P90':>9}{'Thực tế':>10}{'/P50':>7}  ≤P90  Tên")
    for row in rows:
        print(f"{row['id']:<22}{row['status']:<11}{row['p50'] / 1e6:>8.1f}M{row['p90'] / 1e6:>8.1f}M"
              f"{row['actual'] / 1e6:>9.1f}M{row['ratio']:>7.2f}  {'✓' if row['within_p90'] else '✗':^4}  {row['title'][:50]}")
    print("\n" + ledger.check_summary(rows))


def cmd_estimate_backtest(args):
    from ctt.estimate import engine
    settings = engine.load_settings()
    data, prices = _load(args)
    since = date.today() - timedelta(days=args.days - 1)
    rows = engine.backtest(aggregate(data, prices, since), prices, load_policy(), settings, engine.load_risks())
    print(f"{'File':>5} {'Nhiều ngày':<11}{'Thực tế':>9}{'P50':>9}{'P90':>9}  ≤P90 ≤NS  Phiên")
    for row in sorted(rows, key=lambda r: r["actual"], reverse=True):
        print(f"{row['files']:>5} {'có' if row['multi_day'] else '':<11}{row['actual'] / 1e6:>8.1f}M{row['p50'] / 1e6:>8.1f}M"
              f"{row['p90'] / 1e6:>8.1f}M  {'✓' if row['within_p90'] else '✗':^4}{'✓' if row['within_budget'] else '✗':^4} "
              f"{row['title'][:45]}")
    for name, subset in (("Tất cả", rows), ("Trong 1 ngày", [r for r in rows if not r["multi_day"]]),
                         ("Nhiều ngày", [r for r in rows if r["multi_day"]])):
        if subset:
            hit = sum(r["within_p90"] for r in subset)
            budget_hit = sum(r["within_budget"] for r in subset)
            print(f"{name:<13} N={len(subset):<3} ≤ P90: {hit}/{len(subset)} ({hit / len(subset):.0%}) · "
                  f"≤ ngân sách: {budget_hit}/{len(subset)} ({budget_hit / len(subset):.0%})")


def cmd_agent_usage(args):
    import json
    from ctt import live
    print(json.dumps(live.agent_usage(args.projects_dir, args.session, load_prices(args.prices)), ensure_ascii=False))


def cmd_agents(args):
    import json
    from ctt import agents
    found = agents.list_agents(args.cwd, load_policy(args.policy))
    if args.json:
        print(json.dumps({"agents": found}, ensure_ascii=False))
        return
    for agent in found:
        print(f"{agent['call']:<28} {agent['level']:<8} vai {agent['role'] or '—':<10} {agent['model'] or 'inherit'}")


def cmd_delegation(args):
    import json
    from ctt import delegation
    prices = load_prices(args.prices)
    if args.json:
        print(json.dumps(delegation.thresholds(args.projects_dir, prices, load_policy(args.policy), args.session),
                         ensure_ascii=False))
        return
    print(delegation.report(delegation.runs(args.projects_dir, prices, args.days), prices, args.days, args.main_model))


def cmd_agent_log(args):
    import json
    from ctt import agentlog
    print(json.dumps(agentlog.agent_log(args.projects_dir, args.session, args.agent, args.last), ensure_ascii=False))


def cmd_permissions(args):
    import json
    from ctt import permissions
    profile = permissions.load_permissions(args.permissions, args.policy)
    print(json.dumps(profile, ensure_ascii=False) if args.json else permissions.describe(profile))


def cmd_permissions_set(args):
    import json
    from ctt import permissions
    try:
        profile = permissions.set_role(
            args.role, outside=args.outside, mcp=args.mcp,
            enabled=None if args.enabled is None else args.enabled == "true",
            add=tuple(args.add) if args.add else None, remove=tuple(args.remove) if args.remove else None,
            reset=args.reset)
    except ValueError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(profile, ensure_ascii=False))
    return 0


def cmd_handoff_facts(args):
    import json
    from ctt import handoff
    print(json.dumps(handoff.facts(args.cwd), ensure_ascii=False, indent=1))


def cmd_handoff_save(args):
    from ctt import handoff
    text = Path(args.file).read_text(encoding="utf-8") if args.file != "-" else sys.stdin.read()
    try:
        path, tokens = handoff.save(args.topic, text)
    except ValueError as error:
        print(f"✗ Chưa lưu: {error}", file=sys.stderr)
        raise SystemExit(2)
    print(f"✓ Đã lưu bản bàn giao (~{tokens} token): {path}")


def _ship_out(run, as_json):
    import json
    from ctt import ship
    print(json.dumps(run, ensure_ascii=False) if as_json else ship.summary(run))


def _ship_limits(args):
    from ctt import ship
    from ctt.permissions import load_permissions
    policy = load_policy(getattr(args, "policy", None))
    limits = ship.settings(policy)
    limits.update(load_permissions(policy_file=getattr(args, "policy", None))["sessions"])
    return limits


def _ship_fail(error):
    print(f"✗ {error}", file=sys.stderr)
    raise SystemExit(2)


def cmd_ship_start(args):
    import json
    from ctt import ship
    try:
        limits = _ship_limits(args)
    except ValueError as error:
        _ship_fail(error)
    run = ship.start(args.title, _ship_session(args), args.cwd)
    print(json.dumps({"run": run, "workflow": limits, "stages": list(ship.STAGES)}, ensure_ascii=False, indent=1))


def _ship_session(args):
    """--session auto | none | <id> → the session id, or None."""
    from ctt.estimate import ledger
    if args.session == "none":
        return None
    if args.session != "auto":
        return args.session
    found = ledger.current_session(args.projects_dir, Path(args.cwd))
    if not found:
        # The work folder is not the session's own (0.17.1+): the session running this is the transcript
        # written to in the last two minutes, newest first.
        import time
        recent = [p for p in Path(args.projects_dir).glob("*/*.jsonl") if time.time() - p.stat().st_mtime < 120]
        newest = max(recent, key=lambda p: p.stat().st_mtime, default=None)
        found = (newest.stem,) if newest else None
    return found[0] if found else None


def cmd_ship_stage(args):
    import json
    from ctt import ship
    try:
        plan = None
        if args.plan:
            data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            plan = data.get("steps") if isinstance(data, dict) else data
        run = ship.set_stage(args.id, args.stage, args.note, args.estimate, plan, _ship_limits(args), cwd=args.cwd)
    except (ValueError, OSError) as error:
        _ship_fail(error)
    _ship_out(run, args.json)


def cmd_ship_step(args):
    from ctt import ship
    try:
        escalation = ship.escalation_policy(load_policy(args.policy))
        run = ship.set_step(args.id, args.name, args.status, args.note, args.attempt, args.file, args.tests,
                            escalation=escalation)
    except ValueError as error:
        _ship_fail(error)
    _ship_out(run, args.json)
    advice = next(s.get("escalate") for s in run["plan"] if s["name"] == args.name)
    if args.status == "failed" and advice and not args.json:
        if advice.get("exhausted"):
            print(f"‼ Bước {args.name} lỗi {advice['failures']} lần và đã hết thang nâng model ([escalation] ladder): "
                  "dừng bước này, ghi thành việc mở")
        else:
            print(f"↑ Bước {args.name} lỗi {advice['failures']} lần: chạy lại với model {advice['model']}, "
                  f"effort {advice['effort']} (tham số model / effort khi gọi agent)")
    if args.status == "running" and not args.json:
        code = next(s["attempt"] for s in run["plan"] if s["name"] == args.name)
        print(f"Mã lần thử của bước {args.name}: {code} — báo xong / lỗi phải kèm --attempt {code}")


def cmd_ship_next(args):
    import json
    from ctt import ship
    try:
        nxt = ship.next_steps(ship.load(args.id))
    except ValueError as error:
        _ship_fail(error)
    if args.json:
        print(json.dumps(nxt, ensure_ascii=False))
        return
    print("Chạy được ngay: " + (", ".join(f"{s['name']} ({s.get('role') or '—'}, {s['mode']})" for s in nxt["ready"]) or "—"))
    if nxt["running"]:
        print("Đang chạy: " + ", ".join(f"{s['name']} [{s['attempt'] or 'không mã'}]" for s in nxt["running"]))
    if nxt["waiting"]:
        print("Còn chờ: " + "; ".join(f"{s['name']} ← {', '.join(s['on'])}" for s in nxt["waiting"]))
    if nxt["gated"]:
        print("Chờ quyết định của người dùng: " + "; ".join(f"{s['name']} ({', '.join(s['gates'])})" for s in nxt["gated"]))
    for gate in nxt["decided"]:
        print("Đã quyết định: " + ship.gate_text(gate))
    if nxt["failed"]:
        print("Lỗi: " + ", ".join(f"{s['name']} ({s['attempts']} lần thử)" for s in nxt["failed"]))
    if nxt["all_over"]:
        print("Mọi bước đã xong.")


def cmd_ship_gate(args):
    import json
    from ctt import ship
    try:
        if args.resolve:
            if args.answer is None:
                raise ValueError("--resolve cần --answer")
            run, gate = ship.gate_resolve(args.id, args.resolve, args.answer)
        elif args.step:
            run, gate = ship.gate_create(args.id, args.step, args.question, args.option)
        else:
            run, gate = ship.load(args.id), None
    except ValueError as error:
        _ship_fail(error)
    if args.json:
        print(json.dumps({"gate": gate, "gates": run.get("gates", [])}, ensure_ascii=False))
        return
    if gate is None:
        print("\n".join(ship.gate_text(g) for g in run.get("gates", [])) or "Lượt không có cổng quyết định nào.")
    elif args.resolve:
        print(f"Đã quyết định: {ship.gate_text(gate)} — bước {gate['step']} chạy được lại")
    else:
        print(f"Chờ quyết định: {ship.gate_text(gate)}\nBước {gate['step']} dừng tới khi có "
              f"`ship-gate {args.id} --resolve {gate['id']} --answer …`; các bước khác chạy tiếp.")


def cmd_ship_resume(args):
    import json
    from ctt import ship
    if not args.id:
        runs = ship.unfinished()
        if args.json:
            print(json.dumps([ship.brief(run) for run in runs], ensure_ascii=False))
            return
        if not runs:
            print("Không có lượt nào đang dở.")
        for run in runs:
            print(f"{run['id']} · {run['title']} · bước {run['stage']} {ship.STAGES[run['stage']]} · {run.get('cwd') or '—'}")
        return
    try:
        run = ship.attach(args.id, _ship_session(args)) if not ship.load(args.id).get("finished_at") else ship.load(args.id)
    except ValueError as error:
        _ship_fail(error)
    if args.json:
        print(json.dumps({"run": run, "next": ship.next_steps(run)}, ensure_ascii=False))
        return
    print(ship.resume_text(run))


def cmd_ship_finish(args):
    from ctt import ship
    from ctt.estimate import ledger
    try:
        run = ship.load(args.id)
        check = None
        if run.get("estimate_id"):
            records = {k: v for k, v in ledger.load().items() if k == run["estimate_id"]}
            if records:
                data, prices = _load(args)
                check = next(iter(ledger.check(records, data, prices)), None)
        run = ship.finish(args.id, args.result, args.note, check)
    except ValueError as error:
        _ship_fail(error)
    _ship_out(run, args.json)


def cmd_ship_report(args):
    import json
    from ctt import ship_report
    data, prices = _load(args)
    report = ship_report.collect(args.days, args.projects_dir, prices, data, args.store_dir)
    path = ship_report.write(report, args.out)
    if args.json:
        print(json.dumps({"page": str(path), "totals": report["totals"], "advice": report["advice"]}, ensure_ascii=False))
        return
    print(ship_report.text(report))
    print(f"\nTrang báo cáo: {path}")


def _parse_time(text):
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.astimezone()


def cmd_limits(args):
    log = config.ctt_home() / "plan_snapshots.jsonl"
    taken = []
    if args.five_hour is not None or args.week is not None:
        data, prices = _load(args)
        for window, percent, resets in (("5h", args.five_hour, args.five_hour_reset), ("week", args.week, args.week_reset)):
            if percent is None:
                continue
            if not resets:
                sys.exit(f"Thiếu --{'five-hour' if window == '5h' else 'week'}-reset (thời điểm reset, ISO)")
            taken.append(limits.snapshot(data, prices, window, percent, _parse_time(resets)))
        limits.append(log, taken)
        for item in taken:
            print(f"Ghi mốc {item.window}: {item.percent_used}% ↔ ${item.api_equivalent_usd:.2f} API quy đổi "
                  f"({item.tokens / 1e6:.1f}M token) → ${item.usd_per_percent:.2f} / 1%")
    print("\n".join(limits.summary(limits.load(log))))
    print("Lưu ý: dùng claude.ai hay máy khác cũng tính vào hạn mức → $/1% ở đây là cận dưới.")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="ctt", description=f"claude-token-tools {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (("report", cmd_report, "token theo ngày / project / phiên"),
                                     ("calibrate", cmd_calibrate, "P50/P90 token và chi phí theo loại task")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--days", type=int, default=30)
        command.add_argument("--projects-dir", default=str(config.projects_dir()))
        command.add_argument("--prices", help="file prices.toml riêng (mặc định: defaults + ~/.claude/claude-token-tools)")
        command.add_argument("--out", default=str(config.reports_dir()))
        command.set_defaults(handler=handler)

    command = sub.add_parser("sync", help="áp policy model vào agent và settings.json")
    command.add_argument("--apply", action="store_true")
    command.add_argument("--policy", help="file policy.toml riêng (mặc định: defaults + ~/.claude/claude-token-tools)")
    command.add_argument("--root", action="append", default=[], help="thêm thư mục chứa project (lặp được)")
    command.add_argument("--user-settings", default=None)
    command.add_argument("--user-agents", default=None)
    command.add_argument("--agents-only", action="store_true", help="chỉ ghi model vào agent, không đổi model phiên chính")
    command.set_defaults(handler=cmd_sync)

    command = sub.add_parser("estimate", help="phiếu ước lượng từ hồ sơ task (JSON)")
    command.add_argument("--profile", required=True, help="file JSON hồ sơ task")
    command.add_argument("--session", default="auto", help="auto (phiên mới nhất của thư mục --cwd) | none | <session id>")
    command.add_argument("--cwd", default=str(Path.cwd()), help="thư mục project để tìm phiên hiện tại")
    command.add_argument("--week-used", type=float, help="%% hạn mức tuần đã dùng (để vẽ thanh hạn mức)")
    command.add_argument("--no-save", action="store_true", help="không ghi vào sổ phiếu")
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.add_argument("--prices")
    command.set_defaults(handler=cmd_estimate)

    command = sub.add_parser("estimate-approve", help="duyệt ngân sách của phiếu → hook bắt đầu theo dõi")
    command.add_argument("id")
    command.set_defaults(handler=cmd_estimate_approve)

    command = sub.add_parser("estimate-check", help="so phiếu với chi phí thực tế của phiên")
    command.add_argument("--id")
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.add_argument("--prices")
    command.set_defaults(handler=cmd_estimate_check)

    command = sub.add_parser("estimate-backtest", help="chạy ngược bộ ước lượng trên các phiên cũ (bỏ chính phiên đó ra)")
    command.add_argument("--days", type=int, default=60)
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.add_argument("--prices")
    command.set_defaults(handler=cmd_estimate_backtest)

    command = sub.add_parser("agent-usage", help="token và $ từng agent của một phiên (JSON, đọc tăng dần) — cho khung")
    command.add_argument("--session", required=True)
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.add_argument("--prices")
    command.set_defaults(handler=cmd_agent_usage)

    command = sub.add_parser("agents", help="agent gọi được trong một project, theo mức project / người dùng / plugin")
    command.add_argument("--cwd", default=str(Path.cwd()))
    command.add_argument("--json", action="store_true")
    command.add_argument("--policy")
    command.set_defaults(handler=cmd_agents)

    command = sub.add_parser("delegation", help="giao việc cho agent có lời không, và ngưỡng hòa vốn theo context")
    command.add_argument("--days", type=int, default=30)
    command.add_argument("--main-model", help="model phiên chính cho bảng hòa vốn, vd claude-fable-5-2")
    command.add_argument("--json", action="store_true", help="ngưỡng cho khung (đo được hoặc mặc định của policy)")
    command.add_argument("--session", help="với --json: lấy model phiên chính từ phiên này")
    command.add_argument("--policy", help="file policy.toml riêng (lấy [delegation])")
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.add_argument("--prices")
    command.set_defaults(handler=cmd_delegation)

    command = sub.add_parser("agent-log", help="log gọn của một agent (việc giao, lần gọi tool, kết quả) — cho khung")
    command.add_argument("--session", required=True)
    command.add_argument("--agent", required=True, help="id agent, hoặc main cho phiên chính")
    command.add_argument("--last", type=int, default=30)
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.set_defaults(handler=cmd_agent_log)

    command = sub.add_parser("permissions", help="hồ sơ quyền theo vai của agent (gộp mặc định + bản riêng) — cho khung")
    command.add_argument("--json", action="store_true", help="JSON cho mod thay vì bảng")
    command.add_argument("--permissions", help="file permissions.toml riêng")
    command.add_argument("--policy", help="file policy.toml riêng (lấy [agent_role])")
    command.set_defaults(handler=cmd_permissions)

    command = sub.add_parser("permissions-set", help="sửa quyền một vai trong bản riêng của máy (cho khung) → JSON")
    command.add_argument("--role", required=True)
    command.add_argument("--outside", choices=("deny", "ask"))
    command.add_argument("--mcp", choices=("allow", "ask", "deny"), help="tool MCP không phải chỉ đọc")
    command.add_argument("--enabled", choices=("true", "false"))
    command.add_argument("--add", nargs=2, metavar=("DANH_SÁCH", "GIÁ_TRỊ"), help="write | ask_write | bash | mcp_allow")
    command.add_argument("--remove", nargs=2, metavar=("DANH_SÁCH", "GIÁ_TRỊ"))
    command.add_argument("--reset", action="store_true", help="bỏ phần ghi đè của vai, về mặc định")
    command.set_defaults(handler=cmd_permissions_set)

    command = sub.add_parser("handoff-facts", help="trạng thái git + mẫu bản bàn giao (JSON) — cho /token-tools:handoff")
    command.add_argument("--cwd", default=str(Path.cwd()))
    command.set_defaults(handler=cmd_handoff_facts)

    command = sub.add_parser("handoff-save", help="kiểm tra (≤ 2k token, đủ mục) và lưu bản bàn giao")
    command.add_argument("--topic", required=True, help="chủ đề, thành tên file <ngày>-<chủ-đề>.md")
    command.add_argument("--file", required=True, help="file nháp bản bàn giao, hoặc - để đọc stdin")
    command.set_defaults(handler=cmd_handoff_save)

    command = sub.add_parser("ship-start", help="mở một lượt /token-tools:ship (JSON: lượt, [workflow], các bước)")
    command.add_argument("--title", required=True)
    command.add_argument("--cwd", default=str(Path.cwd()))
    command.add_argument("--session", default="auto", help="auto (phiên mới nhất của --cwd) | none | <session id>")
    command.add_argument("--policy")
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.set_defaults(handler=cmd_ship_start)

    command = sub.add_parser("ship-stage", help="chuyển lượt sang bước 0..6; --plan ghi bảng chia việc đã duyệt")
    command.add_argument("id")
    command.add_argument("--stage", type=int, required=True)
    command.add_argument("--note")
    command.add_argument("--estimate", help="mã phiếu ước lượng của lượt")
    command.add_argument("--cwd", help="thư mục làm việc của lượt khi khác thư mục phiên (tính là project cho quyền theo vai)")
    command.add_argument("--plan", help='file JSON {"steps": [{name, role, mode: main|subagent|session, files, done, tokens}]}')
    command.add_argument("--policy")
    command.add_argument("--json", action="store_true")
    command.set_defaults(handler=cmd_ship_stage)

    command = sub.add_parser("ship-step", help="trạng thái một bước của kế hoạch")
    command.add_argument("id")
    command.add_argument("--name", required=True)
    command.add_argument("--status", required=True, choices=("pending", "running", "done", "failed", "skipped"))
    command.add_argument("--note")
    command.add_argument("--attempt", help="mã lần thử nhận được lúc --status running; bắt buộc khi báo done / failed")
    command.add_argument("--file", action="append", help="file bước này đã đổi (lặp được) — ghi cùng done / failed")
    command.add_argument("--tests", help="kết quả test của bước, một dòng (vd '12 pass, 0 fail')")
    command.add_argument("--policy")
    command.add_argument("--json", action="store_true")
    command.set_defaults(handler=cmd_ship_step)

    command = sub.add_parser("ship-next", help="bước nào chạy được ngay, đang chạy, còn chờ bước nào, lỗi")
    command.add_argument("id")
    command.add_argument("--json", action="store_true")
    command.set_defaults(handler=cmd_ship_next)

    command = sub.add_parser("ship-gate", help="cổng quyết định: hỏi người dùng cho một bước, các bước khác chạy tiếp; "
                                               "không tham số: liệt kê")
    command.add_argument("id")
    command.add_argument("--step", help="mở cổng cho bước này (kèm --question)")
    command.add_argument("--question")
    command.add_argument("--option", action="append", help="một lựa chọn (lặp được, ít nhất 2); bỏ trống = trả lời tự do")
    command.add_argument("--resolve", metavar="MÃ_CỔNG", help="ghi quyết định cho cổng này (kèm --answer)")
    command.add_argument("--answer")
    command.add_argument("--json", action="store_true")
    command.set_defaults(handler=cmd_ship_gate)

    command = sub.add_parser("ship-resume", help="chạy tiếp một lượt dở ở phiên này; không có id: liệt kê các lượt dở")
    command.add_argument("id", nargs="?")
    command.add_argument("--cwd", default=str(Path.cwd()))
    command.add_argument("--session", default="auto", help="auto (phiên đang chạy lệnh) | none | <session id>")
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.add_argument("--json", action="store_true")
    command.set_defaults(handler=cmd_ship_resume)

    command = sub.add_parser("ship-finish", help="kết thúc lượt: so phiếu với thực tế, ghi kết quả")
    command.add_argument("id")
    command.add_argument("--result", required=True, choices=("ok", "partial", "failed"))
    command.add_argument("--note")
    command.add_argument("--json", action="store_true")
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.add_argument("--prices")
    command.set_defaults(handler=cmd_ship_finish)

    command = sub.add_parser("ship-report", help="báo cáo các lượt /token-tools:ship (bảng + trang HTML)")
    command.add_argument("--days", type=int, default=14)
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.add_argument("--prices")
    command.add_argument("--store-dir", help="thư mục kho chung của plugin (mặc định ~/.claude/plugins/store)")
    command.add_argument("--out", help="thư mục ghi trang (mặc định <ctt home>/reports)")
    command.add_argument("--json", action="store_true", help="JSON cho khung: đường dẫn trang, số tổng, gợi ý")
    command.set_defaults(handler=cmd_ship_report)

    command = sub.add_parser("init", help="khai các thư mục chứa project vào policy riêng của máy")
    command.add_argument("--root", action="append", required=True, help="thư mục chứa project (lặp được)")
    command.set_defaults(handler=cmd_init)

    command = sub.add_parser("limits", help="quy đổi chi phí sang %% hạn mức gói (5 giờ / tuần)")
    command.add_argument("--five-hour", type=float, help="%% đã dùng của hạn mức 5 giờ (app hiển thị)")
    command.add_argument("--five-hour-reset", help="thời điểm hạn mức 5 giờ reset, ISO (vd 2026-11-11T11:20:00Z)")
    command.add_argument("--week", type=float, help="%% đã dùng của hạn mức tuần (all models)")
    command.add_argument("--week-reset", help="thời điểm hạn mức tuần reset, ISO")
    command.add_argument("--projects-dir", default=str(config.projects_dir()))
    command.add_argument("--prices")
    command.set_defaults(handler=cmd_limits)

    args = parser.parse_args(argv)
    args.handler(args)
    return 0
