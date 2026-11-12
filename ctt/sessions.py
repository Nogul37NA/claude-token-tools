"""Per-session aggregates and task-type labels."""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

EDIT_TOOLS = re.compile(r"^(Edit|Write|MultiEdit|NotebookEdit)$|serena__(replace|insert|rename|safe_delete)")
SOURCE_EXT = (".java", ".sql", ".xml", ".yml", ".yaml", ".properties", ".ts", ".tsx", ".js", ".mjs", ".jsx",
              ".vue", ".py", ".html", ".css", ".scss", ".json", ".sh", ".bat", ".ps1")
NON_SOURCE_HINTS = ("memory-bank", "/.claude/projects", "scratchpad", "/temp/")
BUILD_COMMAND = re.compile(r"compile\.bat|mvn(\.cmd)?\s|gradle|npm run build")
COMMIT_COMMAND = re.compile(r"git\s+commit")

LABEL_QA = "1. Hỏi đáp ngắn (không sửa code)"
LABEL_INVESTIGATION = "2. Điều tra / phân tích / vận hành (không sửa code)"
LABEL_SMALL = "3. Sửa nhỏ (1–3 file)"
LABEL_MEDIUM = "4. Tính năng / sửa vừa (4–10 file)"
LABEL_LARGE = "5. Thay đổi lớn (>10 file)"
QA_MAX_REQUESTS = 30
LABEL_CODES = {LABEL_QA: "qa", LABEL_INVESTIGATION: "investigation", LABEL_SMALL: "small",
               LABEL_MEDIUM: "medium", LABEL_LARGE: "large"}
CODE_LABELS = {code: text for text, code in LABEL_CODES.items()}


@dataclass
class SessionStats:
    session_id: str
    project: str = ""
    title: str = ""
    branch: str = ""
    first: datetime = None
    last: datetime = None
    days: set = field(default_factory=set)
    requests: int = 0
    subagent_requests: int = 0
    context_sum: int = 0
    context_max: int = 0
    input: int = 0
    output: int = 0
    read: int = 0
    write: int = 0
    cost: float = 0.0
    unknown_model_requests: int = 0
    models: dict = field(default_factory=lambda: defaultdict(int))
    files: set = field(default_factory=set)
    migration: bool = False
    builds: int = 0
    commits: int = 0

    @property
    def total(self):
        return self.input + self.read + self.write + self.output

    @property
    def avg_context(self):
        return self.context_sum // self.requests if self.requests else 0

    @property
    def multi_day(self):
        return len(self.days) > 1

    @property
    def main_model(self):
        return max(self.models, key=self.models.get) if self.models else ""

    @property
    def label(self):
        return label(len(self.files), self.requests)


def label(files_edited, requests):
    if files_edited == 0:
        return LABEL_QA if requests < QA_MAX_REQUESTS else LABEL_INVESTIGATION
    if files_edited <= 3:
        return LABEL_SMALL
    if files_edited <= 10:
        return LABEL_MEDIUM
    return LABEL_LARGE


def edited_source_file(tool):
    """Normalized path of a source file the tool call edits, or None."""
    if not EDIT_TOOLS.search(tool.name):
        return None
    raw = tool.input.get("file_path") or tool.input.get("relative_path") or ""
    path = str(raw).replace("\\", "/").lower()
    if not path.endswith(SOURCE_EXT) or any(hint in path for hint in NON_SOURCE_HINTS):
        return None
    return path


def aggregate(scan, prices, since=None):
    """SessionStats for every session with at least one request whose last activity is on/after `since` (a date)."""
    stats = {}
    for request in scan.requests:
        session = stats.get(request.session_id)
        if session is None:
            meta = scan.sessions.get(request.session_id)
            session = stats[request.session_id] = SessionStats(
                session_id=request.session_id,
                project=meta.project if meta else "",
                title=(meta.title or meta.first_prompt) if meta else "",
                branch=meta.branch if meta else "")
        session.first = min(session.first or request.timestamp, request.timestamp)
        session.last = max(session.last or request.timestamp, request.timestamp)
        session.days.add(request.timestamp.date())
        session.requests += 1
        session.subagent_requests += 1 if request.is_subagent else 0
        session.context_sum += request.context
        session.context_max = max(session.context_max, request.context)
        session.input += request.input_tokens
        session.read += request.cache_read
        session.write += request.cache_write
        session.output += request.output_tokens
        session.models[request.model] += 1
        usd, known = prices.cost(request)
        session.cost += usd
        if not known and request.total:
            session.unknown_model_requests += 1

    for tool in scan.tool_uses:
        session = stats.get(tool.session_id)
        if session is None:
            continue
        path = edited_source_file(tool)
        if path:
            session.files.add(path)
            session.migration = session.migration or "db/migration" in path
        command = str(tool.input.get("command", ""))
        session.builds += 1 if BUILD_COMMAND.search(command) else 0
        session.commits += 1 if COMMIT_COMMAND.search(command) else 0

    return [s for s in stats.values() if since is None or s.last.date() >= since]


def percentile(values, q):
    """Nearest-rank percentile; 0 for an empty list."""
    ordered = sorted(values)
    if not ordered:
        return 0
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]
