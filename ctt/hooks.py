"""Claude Code hooks: warn (never block) when a session gets expensive.

- UserPromptSubmit: context of the session has grown past a threshold (each tool call re-reads all of it),
  or the session sat idle longer than the 1-hour prompt cache, so this turn rewrites the whole context.
- PostToolUse: a tool returned a large output that every later turn will re-read.

Output follows the hook contract: {"systemMessage": ...} is shown to the user;
hookSpecificOutput.additionalContext is added for Claude. Any error → print {} and exit 0.
Switch off: env CTT_HOOKS=off, or [hooks] enabled = false in policy.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ctt import config

DEFAULTS = {
    "enabled": True,
    "context_warn_tokens": 200_000,
    "context_warn_step_tokens": 100_000,
    "idle_warn_minutes": 60,
    "tool_output_warn_tokens": 8_000,
    "chars_per_token": 3.0,
}
TAIL_BYTES = 512 * 1024


def settings():
    try:
        data = config.load_layered("policy.toml").get("hooks", {})
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **data}


def last_main_request(transcript_path):
    """(context_tokens, model, timestamp) of the latest main-thread assistant request, read from the file tail."""
    path = Path(transcript_path)
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - TAIL_BYTES))
        tail = handle.read().decode("utf-8", errors="ignore")
    for line in reversed(tail.splitlines()):
        if '"usage"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        usage = message.get("usage")
        if entry.get("type") != "assistant" or entry.get("isSidechain") or not isinstance(usage, dict):
            continue
        context = sum(usage.get(key) or 0 for key in
                      ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        when = datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00"))
        return context, message.get("model") or "", when
    return None


def _price(model):
    from ctt.pricing import load_prices
    return load_prices().lookup(model)[0]


def _state(session_id):
    path = config.ctt_home() / "hook_state" / f"{session_id}.json"
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path, {}


def _save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def user_prompt_submit(data, opts, now=None):
    now = now or datetime.now(timezone.utc)
    found = data.get("transcript_path") and last_main_request(data["transcript_path"])
    if not found:
        return {}
    context, model, last_at = found
    price = _price(model)
    messages = []

    idle_minutes = (now - last_at).total_seconds() / 60
    if idle_minutes >= opts["idle_warn_minutes"] and context:
        rewrite_usd = context * price.cache_write_1h / 1e6
        messages.append(
            f"⏱ Phiên đã nghỉ {idle_minutes:.0f} phút, quá thời hạn cache ({opts['idle_warn_minutes']} phút): lượt này ghi lại "
            f"~{context / 1000:.0f}k token cache (≈ ${rewrite_usd:.2f} giá API quy đổi). Nếu việc mới không cần context cũ, "
            f"mở phiên mới (kèm tóm tắt) sẽ rẻ hơn — gõ /token-tools:handoff để soạn bản bàn giao và mở phiên mới.")

    path, state = _state(data.get("session_id", "unknown"))
    before = json.dumps(state, sort_keys=True)
    if context >= opts["context_warn_tokens"]:
        step = max(1, int(opts["context_warn_step_tokens"]))
        bucket = int((context - opts["context_warn_tokens"]) // step)
        if bucket > state.get("context_bucket", -1):
            state["context_bucket"] = bucket
            per_turn = context * price.cache_read / 1e6
            messages.append(
                f"📦 Context phiên này ~{context / 1000:.0f}k token — mỗi lần gọi tool đọc lại toàn bộ (≈ ${per_turn:.3f}/lượt "
                f"giá API quy đổi). Nếu đã sang việc khác: /clear; muốn làm tiếp ở phiên mới gọn hơn: /token-tools:handoff "
                f"(soạn bản bàn giao ≤ 2k token rồi mở phiên mới); nếu vẫn cùng việc: /compact.")

    budget = budget_message(data, state)
    if budget:
        messages.append(budget[0])
    if json.dumps(state, sort_keys=True) != before:
        _save_state(path, state)
    result = {"systemMessage": "\n".join(messages)} if messages else {}
    if budget and budget[1] >= 0.8:  # Claude must act on these, not just the user
        result["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit",
                                        "additionalContext": f"[claude-token-tools] {budget[0]}"}
    return result


def budget_message(data, state, ledger_file=None):
    """Warn once per checkpoint when an approved estimate's budget is being used up. Reads only new transcript bytes."""
    from ctt.estimate import ledger
    from ctt.pricing import load_prices
    record = ledger.approved_for_session(data.get("session_id"), ledger_file)
    if not record or not data.get("transcript_path"):
        return None
    est = record["estimate"]
    track = state.get("budget") or {}
    if track.get("id") != est["id"]:
        track = {"id": est["id"], "offset": record.get("start_offset") or 0, "tokens": 0, "usd": 0.0,
                 "last_key": None, "warned": []}
    tokens, usd, track["offset"], track["last_key"] = ledger.usage_since(
        data["transcript_path"], track["offset"], load_prices(), track["last_key"])
    track["tokens"] += tokens
    track["usd"] += usd
    state["budget"] = track
    budget = est["tokens"]["budget"]
    share = track["tokens"] / budget if budget else 0
    crossed = [c for c in est["checkpoints"] if share >= c and c not in track["warned"]]
    if not crossed:
        return None
    track["warned"] += crossed
    level = max(crossed)
    advice = ("Đã hết ngân sách: dừng lại, tóm tắt tiến độ và hỏi người dùng có cấp thêm, mở phiên mới hay nâng model."
              if level >= 1 else "Qua 80%: nếu phần chính chưa xong, cân nhắc dừng hỏi người dùng." if level >= 0.8
              else "Còn nửa ngân sách.")
    return (f"💰 Phiếu {est['id']}: đã dùng {track['tokens'] / 1e6:.1f}M / {budget / 1e6:.1f}M token "
            f"({share:.0%} ngân sách, ≈ ${track['usd']:.2f}). {advice}", level)


# File-writing tools echo the file back to the hook, but Claude only sees a one-line confirmation.
SILENT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def visible_text(tool, response):
    """The part of a tool response that lands in Claude's context (best effort by tool)."""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        if tool in ("Bash", "PowerShell"):
            return f"{response.get('stdout', '')}{response.get('stderr', '')}"
        file_info = response.get("file")
        if tool == "Read" and isinstance(file_info, dict) and isinstance(file_info.get("content"), str):
            return file_info["content"]
    return json.dumps(response, ensure_ascii=False)


def post_tool_use(data, opts):
    tool = data.get("tool_name", "tool")
    response = data.get("tool_response", data.get("tool_output"))
    if response is None or tool in SILENT_TOOLS or tool.endswith("__replace_symbol_body"):
        return {}
    text = visible_text(tool, response)
    tokens = len(text) / opts["chars_per_token"]
    if tokens < opts["tool_output_warn_tokens"]:
        return {}
    return {
        "systemMessage": f"📄 {tool} vừa trả ~{tokens / 1000:.0f}k token output — sẽ bị đọc lại ở mọi lượt sau trong phiên.",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (f"[claude-token-tools] Output của {tool} vừa rồi ~{tokens / 1000:.0f}k token và sẽ nằm trong "
                                  "context suốt phiên. Lần sau hãy lọc trước khi đọc: grep/Select-String chỉ lấy dòng cần, "
                                  "tail/head, Read với offset/limit, build chỉ lấy dòng ERROR/BUILD."),
        },
    }


HANDLERS = {"user-prompt-submit": user_prompt_submit, "post-tool-use": post_tool_use}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    result = {}
    try:
        if os.environ.get("CTT_HOOKS", "").lower() not in ("off", "0", "false") and argv and argv[0] in HANDLERS:
            opts = settings()
            if opts.get("enabled", True):
                data = json.loads(sys.stdin.read() or "{}")
                result = HANDLERS[argv[0]](data, opts)
    except Exception as error:  # a hook must never break the session
        print(f"claude-token-tools hook: {error}", file=sys.stderr)
        result = {}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0
