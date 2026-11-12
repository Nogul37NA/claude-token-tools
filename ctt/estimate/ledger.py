"""Estimate ledger (append-only JSONL of events), session lookup, incremental usage and estimate-vs-actual check."""
import json
from datetime import datetime, timezone
from pathlib import Path

from ctt import config
from ctt.sessions import percentile
from ctt.transcripts import encode_folder


def ledger_path():
    return config.ctt_home() / "estimates.jsonl"


def append(event, path=None):
    path = path or ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def load(path=None):
    """id → record {estimate, session_id, transcript_path, start_offset, status, approved_at}."""
    path = path or ledger_path()
    records = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "created":
            est = event["estimate"]
            records[est["id"]] = {"estimate": est, "session_id": event.get("session_id"),
                                  "transcript_path": event.get("transcript_path"),
                                  "start_offset": event.get("start_offset", 0), "status": "proposed"}
        elif event.get("event") == "approved" and event.get("id") in records:
            records[event["id"]]["status"] = "approved"
            records[event["id"]]["approved_at"] = event.get("at")
    return records


def approved_for_session(session_id, path=None):
    """Latest approved estimate tied to a session, or None."""
    matches = [r for r in load(path).values() if r["session_id"] == session_id and r["status"] == "approved"]
    return max(matches, key=lambda r: r.get("approved_at") or "", default=None)


def current_session(projects_dir, cwd):
    """(session_id, transcript path, size) of the newest top-level transcript of the project opened at cwd."""
    projects_dir = Path(projects_dir)
    wanted = encode_folder(str(cwd)).lower()
    folder = next((p for p in projects_dir.iterdir() if p.is_dir() and p.name.lower() == wanted), None) \
        if projects_dir.is_dir() else None
    if folder is None:
        return None
    newest = max(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, default=None)
    if newest is None:
        return None
    return newest.stem, str(newest), newest.stat().st_size


PARTS = ("input", "output", "cache_read", "cache_write")


def usage_since(transcript_path, offset, prices, last_key=None, include_sidechain=False):
    """Tokens and USD of requests written after byte `offset` (main thread only unless include_sidechain). Returns (tokens, usd, new_offset, last_key).
    Only whole lines are consumed, so a line being written is picked up next time."""
    parts, usd, offset, last_key = usage_parts_since(transcript_path, offset, prices, last_key, include_sidechain)
    return sum(parts.values()), usd, offset, last_key


def usage_parts_since(transcript_path, offset, prices, last_key=None, include_sidechain=False):
    """Like usage_since, with tokens split into PARTS: {input, output, cache_read, cache_write}."""
    parts = dict.fromkeys(PARTS, 0)
    usd = 0.0
    from ctt.transcripts import Request  # local import keeps hook start-up light
    with open(transcript_path, "rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
    end = chunk.rfind(b"\n") + 1
    for raw in chunk[:end].splitlines():
        if b'"usage"' not in raw:
            continue
        try:
            entry = json.loads(raw.decode("utf-8", errors="ignore"))
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        usage = message.get("usage")
        skip_sidechain = entry.get("isSidechain") and not include_sidechain
        if entry.get("type") != "assistant" or skip_sidechain or not isinstance(usage, dict):
            continue
        key = f"{message.get('id')}|{entry.get('requestId')}"
        if key == last_key:
            continue
        last_key = key
        write = usage.get("cache_creation_input_tokens") or 0
        breakdown = usage.get("cache_creation") or {}
        write_5m = breakdown.get("ephemeral_5m_input_tokens") or 0
        request = Request("", False, None, message.get("model") or "", usage.get("input_tokens") or 0,
                          usage.get("output_tokens") or 0, usage.get("cache_read_input_tokens") or 0,
                          write_5m, max(0, write - write_5m))
        parts["input"] += request.input_tokens
        parts["output"] += request.output_tokens
        parts["cache_read"] += request.cache_read
        parts["cache_write"] += request.cache_write
        usd += prices.cost(request)[0]
    return parts, usd, offset + end, last_key


def check(records, scan, prices):
    """Actual vs estimate for every record with a session. Uses the full scan (subagents included)."""
    rows = []
    for record in records.values():
        est = record["estimate"]
        if not record["session_id"]:
            continue
        since = datetime.fromisoformat(est["created_at"])
        tokens = 0
        usd = 0.0
        for request in scan.requests:
            if request.session_id == record["session_id"] and request.timestamp >= since:
                tokens += request.total
                usd += prices.cost(request)[0]
        rows.append({"id": est["id"], "title": est["title"], "status": record["status"],
                     "p50": est["tokens"]["p50"], "p90": est["tokens"]["p90"], "actual": tokens, "actual_usd": usd,
                     "ratio": tokens / est["tokens"]["p50"] if est["tokens"]["p50"] else 0,
                     "within_p90": tokens <= est["tokens"]["p90"]})
    return rows


def check_summary(rows, min_tokens=1):
    measured = [r for r in rows if r["actual"] >= min_tokens]
    if not measured:
        return "Chưa có phiếu nào có dữ liệu thực tế."
    within = sum(r["within_p90"] for r in measured)
    median_ratio = percentile([r["ratio"] for r in measured], .5)
    return (f"{len(measured)} phiếu có thực tế · {within}/{len(measured)} ({within / len(measured):.0%}) ≤ P90 "
            f"(mục tiêu ≥ 70%) · trung vị thực tế / P50 = {median_ratio:.2f}")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
