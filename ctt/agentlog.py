"""One agent's transcript as short lines for the control-room pane's "Xem log" (the app's own transcript switch
needs a main view beside the pane, which a maximized or separate pane window has not).

Lines: the task it was given, what it said, each tool call with the first line of its result (errors marked),
and its final report. Only the tail of the file is read, so the main thread's large transcript stays quick.
"""
import json
import os

from ctt.live import session_files

TAIL_BYTES = 2_000_000
CLIP = 300
HANDBACK = "SubagentHandback"
# Internal lines that say nothing about the work: the launch receipt of a background agent, and messages relayed
# between sessions (their content is the other session's report, shown on that agent's own card).
NOISE = ("Async agent launched successfully", "Another Claude session sent a message")
INPUT_KEYS = ("file_path", "command", "pattern", "url", "path", "query", "description", "prompt", "notebook_path")


def _clip(text, limit=CLIP):
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _tail_lines(path, tail_bytes):
    from ctt.config import long_path
    path = long_path(path)
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        handle.seek(max(0, size - tail_bytes))
        data = handle.read()
    lines = data.split(b"\n")
    if size > tail_bytes:
        lines = lines[1:]  # the first one is cut in the middle
    return [line for line in lines if line.strip()]


def _summary(name, payload):
    if not isinstance(payload, dict):
        return name
    for key in INPUT_KEYS:
        if isinstance(payload.get(key), str) and payload[key].strip():
            return f"{name} {_clip(payload[key], 160)}"
    return name


def _result_text(content):
    if isinstance(content, list):
        content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    for line in str(content or "").splitlines():
        if line.strip():
            return _clip(line)
    return "(trống)"


def _noise(text):
    return str(text).lstrip().startswith(NOISE)


def entries(path, tail_bytes=TAIL_BYTES):
    """[{at, kind, text, error}] in order; at: the row's ISO time; kind: task | say | call | result | answer."""
    out = []
    calls = set()  # ids of calls seen: a result whose call lies before the tail is dropped
    for raw in _tail_lines(path, tail_bytes):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if row.get("type") not in ("user", "assistant"):
            continue
        content = (row.get("message") or {}).get("content")
        if isinstance(content, str):
            if row["type"] == "user" and not content.lstrip().startswith("<") and not _noise(content):
                out.append({"at": row.get("timestamp"), "kind": "task" if not out else "user", "text": _clip(content), "error": False})
            continue
        for block in content or []:
            kind = block.get("type")
            if kind == "text" and row["type"] == "assistant" and block.get("text", "").strip():
                out.append({"at": row.get("timestamp"), "kind": "say", "text": _clip(block["text"]), "error": False})
            elif kind == "tool_use":
                if block.get("name") == HANDBACK:
                    message = (block.get("input") or {}).get("message", "")
                    out.append({"at": row.get("timestamp"), "kind": "answer", "text": _clip(message, 600), "error": False})
                else:
                    calls.add(block.get("id"))
                    out.append({"at": row.get("timestamp"), "kind": "call", "text": _summary(block.get("name", "?"), block.get("input")),
                                "error": False})
            elif kind == "tool_result" and block.get("tool_use_id") in calls:
                if _noise(_result_text(block.get("content"))):
                    continue
                out.append({"at": row.get("timestamp"), "kind": "result", "text": _result_text(block.get("content")),
                            "error": bool(block.get("is_error"))})
    return out


def agent_log(projects_dir, session_id, agent_id, last=30):
    """{agent, path, lines} with the last `last` lines; `error` instead when the transcript is not found."""
    path = session_files(projects_dir, session_id).get(agent_id)
    if path is None:
        return {"agent": agent_id, "path": None, "lines": [], "error": "không tìm thấy transcript của agent này"}
    return {"agent": agent_id, "path": str(path), "lines": entries(path)[-last:]}
