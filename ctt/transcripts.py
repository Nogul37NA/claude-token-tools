"""Read Claude Code session transcripts (<projects>/<folder>/*.jsonl, subagents under <session>/subagents/)."""
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Request:
    """One API request (an assistant message), counted once even when split over several lines."""
    session_id: str
    is_subagent: bool
    timestamp: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int

    @property
    def cache_write(self):
        return self.cache_write_5m + self.cache_write_1h

    @property
    def context(self):
        return self.input_tokens + self.cache_read + self.cache_write

    @property
    def total(self):
        return self.context + self.output_tokens


@dataclass(frozen=True)
class ToolUse:
    session_id: str
    is_subagent: bool
    name: str
    input: dict


@dataclass
class SessionMeta:
    project: str = ""
    folder: str = ""
    title: str = ""
    first_prompt: str = ""
    branch: str = ""


@dataclass
class Scan:
    requests: list = field(default_factory=list)
    tool_uses: list = field(default_factory=list)
    sessions: dict = field(default_factory=dict)
    folder_cwds: dict = field(default_factory=lambda: defaultdict(set))
    files: int = 0
    bad_lines: int = 0


def encode_folder(path_text):
    """Claude Code names a project's transcript folder after its launch path, every non-alphanumeric char → '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", path_text)


def launch_folder_name(folder, cwds):
    """Project name for a transcript folder: the cwd whose encoded form is the folder name (the folder the session
    was opened in, not a subfolder it later cd'ed into); else the shortest cwd seen; else the folder name."""
    for cwd in cwds:
        if encode_folder(cwd).lower() == folder.lower():
            return Path(cwd).name
    return Path(min(cwds, key=len)).name if cwds else folder


def _first_text(content):
    if isinstance(content, str):
        return content
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "")
    return ""


def _number(value):
    return value if isinstance(value, int) and value > 0 else 0


def _cache_writes(usage):
    """Split cache writes by TTL; a total without breakdown is counted as 1h (Claude Code's default)."""
    total = _number(usage.get("cache_creation_input_tokens"))
    breakdown = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    write_5m = _number(breakdown.get("ephemeral_5m_input_tokens"))
    write_1h = _number(breakdown.get("ephemeral_1h_input_tokens"))
    write_1h += max(0, total - write_5m - write_1h)
    return write_5m, write_1h


def scan(root):
    """Parse every transcript under root. Malformed lines are counted, never fatal."""
    from ctt.config import long_path
    root = long_path(root)
    result = Scan()
    seen_requests = set()
    seen_tools = set()
    for path in sorted(root.rglob("*.jsonl")):
        relative = path.relative_to(root).parts
        is_subagent = "subagents" in relative
        result.files += 1
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    result.bad_lines += 1
                    continue
                if not isinstance(entry, dict):
                    result.bad_lines += 1
                    continue
                _read_entry(entry, relative[0], is_subagent, result, seen_requests, seen_tools)
    names = {folder: launch_folder_name(folder, cwds) for folder, cwds in result.folder_cwds.items()}
    for meta in result.sessions.values():
        meta.project = names.get(meta.folder, meta.folder)
    return result


def _read_entry(entry, folder, is_subagent, result, seen_requests, seen_tools):
    session_id = entry.get("sessionId")
    if not session_id:
        return
    meta = result.sessions.setdefault(session_id, SessionMeta())
    meta.folder = meta.folder or folder
    if isinstance(entry.get("cwd"), str) and entry["cwd"]:
        result.folder_cwds[folder].add(entry["cwd"])
    if entry.get("gitBranch") and not meta.branch:
        meta.branch = entry["gitBranch"]

    kind = entry.get("type")
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    if kind == "custom-title":
        if entry.get("customTitle"):
            meta.title = entry["customTitle"]
        return
    if kind == "user":
        if not is_subagent and not meta.first_prompt and not entry.get("isMeta"):
            text = _first_text(message.get("content")).strip()
            if text and not text.startswith("<"):
                meta.first_prompt = " ".join(text.split())[:100]
        return
    if kind != "assistant":
        return

    for block in message.get("content") if isinstance(message.get("content"), list) else []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        block_id = block.get("id")
        if block_id:
            if block_id in seen_tools:
                continue
            seen_tools.add(block_id)
        tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
        result.tool_uses.append(ToolUse(session_id, is_subagent, block.get("name", ""), tool_input))

    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    message_id, request_id = message.get("id"), entry.get("requestId")
    if message_id or request_id:
        if (message_id, request_id) in seen_requests:
            return
        seen_requests.add((message_id, request_id))
    stamp = entry.get("timestamp")
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone()
    except ValueError:
        result.bad_lines += 1
        return
    write_5m, write_1h = _cache_writes(usage)
    result.requests.append(Request(
        session_id=session_id,
        is_subagent=is_subagent,
        timestamp=when,
        model=message.get("model") or "",
        input_tokens=_number(usage.get("input_tokens")),
        output_tokens=_number(usage.get("output_tokens")),
        cache_read=_number(usage.get("cache_read_input_tokens")),
        cache_write_5m=write_5m,
        cache_write_1h=write_1h,
    ))
