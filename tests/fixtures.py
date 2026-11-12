"""Build synthetic Claude Code transcripts in a temp folder (never real user data)."""
import json
from pathlib import Path


def user(session, text, cwd="C:\\work\\demo", branch="main", meta=False):
    return {"type": "user", "sessionId": session, "cwd": cwd, "gitBranch": branch, "isMeta": meta,
            "timestamp": "2026-11-10T01:00:00Z", "message": {"role": "user", "content": text}}


def assistant(session, message_id, request_id, timestamp, model="claude-opus-5-5", content=None, usage=None,
              cwd="C:\\work\\demo"):
    message = {"id": message_id, "model": model, "role": "assistant", "content": content or []}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "sessionId": session, "requestId": request_id, "timestamp": timestamp,
            "cwd": cwd, "message": message}


def usage(input_tokens=0, output=0, read=0, write=0, write_5m=None, write_1h=None):
    data = {"input_tokens": input_tokens, "output_tokens": output, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write}
    if write_5m is not None or write_1h is not None:
        data["cache_creation"] = {"ephemeral_5m_input_tokens": write_5m or 0,
                                  "ephemeral_1h_input_tokens": write_1h or 0}
    return data


def tool(tool_id, name, **tool_input):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def write_jsonl(path, entries, extra_lines=()):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(entry, ensure_ascii=False) for entry in entries] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_projects(root):
    """Two sessions:
    s1 (demo, 2 days): msg1 split over two lines (thinking + Edit .java) → 1 request; msg2 with 5m/1h breakdown;
        a subagent request; a corrupt line; a custom title; an Edit to a memory-bank .md (ignored); a migration Edit.
    s2 (other, 1 day): one request with an unknown model, no edits.
    """
    root = Path(root)
    s1 = "s1"
    write_jsonl(root / "C--work-demo" / "s1.jsonl", [
        user(s1, "<command-name>ignored</command-name>"),
        user(s1, "Sửa lỗi   danh sách\nđơn hàng"),
        assistant(s1, "m1", "r1", "2026-11-09T02:00:00Z", content=[{"type": "thinking", "thinking": ""}],
                  usage=usage(input_tokens=10, output=100, read=1000, write=500)),
        assistant(s1, "m1", "r1", "2026-11-09T02:00:00Z",
                  content=[tool("t1", "Edit", file_path="C:\\work\\demo\\src\\Order.java")],
                  usage=usage(input_tokens=10, output=100, read=1000, write=500)),
        assistant(s1, "m2", "r2", "2026-11-10T03:00:00Z",
                  content=[tool("t2", "Write", file_path="C:/work/demo/ai-rules/memory-bank/x.md"),
                           tool("t3", "Edit", file_path="C:/work/demo/src/main/resources/db/migration/V1__a.sql"),
                           tool("t4", "Bash", command="cmd /c compile.bat service"),
                           tool("t5", "Bash", command="git commit -m x")],
                  usage=usage(output=50, read=2000, write=300, write_5m=100, write_1h=200)),
        {"type": "custom-title", "customTitle": "Đơn hàng lỗi", "sessionId": s1},
    ], extra_lines=["{not json"])
    write_jsonl(root / "C--work-demo" / "s1" / "subagents" / "agent-a.jsonl", [
        assistant(s1, "m3", "r3", "2026-11-10T03:05:00Z", model="claude-sonnet-5-5",
                  usage=usage(output=20, read=400)),
    ])
    write_jsonl(root / "C--work-other" / "s2.jsonl", [
        {"type": "custom-title", "customTitle": "Hỏi nhanh", "sessionId": "s2"},  # no cwd on title lines
        user("s2", "hỏi nhanh", cwd="C:\\work\\other\\src\\main\\java\\com"),   # session cd'ed into a subfolder
        assistant("s2", "m4", "r4", "2026-11-10T05:00:00Z", model="claude-unknown-9", cwd="C:\\work\\other",
                  usage=usage(output=1_000_000)),
    ])
    return root


PRICES = {
    "fallback": {"model": "claude-opus-5-5"},
    "models": {
        "claude-opus-5-5": {"input": 4, "output": 20, "cache_read": 0.2, "cache_write_5m": 5, "cache_write_1h": 8},
        "claude-sonnet-5-5": {"input": 2, "output": 10, "cache_read": 0.1, "cache_write_5m": 2.5,
                              "cache_write_1h": 4},
        "claude-haiku-4-5": {"input": 1, "output": 5, "cache_read": 0.1, "cache_write_5m": 1.25,
                             "cache_write_1h": 2},
    },
}
