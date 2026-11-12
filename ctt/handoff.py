"""Hand-over notes: hand a long session's state to a fresh one instead of re-reading a huge context every call.

`facts` gathers what git knows (so Claude does not have to recall it); Claude writes the note from its own
context following TEMPLATE; `save` checks the size and writes <ctt_home>/handoffs/<date>-<slug>.md.
"""
import math
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

from ctt import config

MAX_TOKENS = 2000
SECTIONS = ("## Trạng thái", "## Quyết định đã chốt", "## Việc cần làm tiếp", "## File liên quan")
TEMPLATE = """# Bàn giao: <chủ đề> (từ phiên <id ngắn>, <ngày>)

## Trạng thái
- Repo, nhánh, commit cuối, thay đổi chưa commit (từ handoff-facts), việc vừa xong.

## Quyết định đã chốt
- Điều đã thử/đã chọn và lý do — để phiên mới không làm lại.

## Việc cần làm tiếp
1. Theo thứ tự, mỗi việc đủ rõ để bắt tay làm.

## File liên quan
`đường/dẫn` · ...
"""


def handoffs_dir():
    return config.ctt_home() / "handoffs"


def estimate_tokens(text):
    """Rough and on the high side: Vietnamese with diacritics runs ~2.5 characters per token."""
    return math.ceil(len(text) / 2.5)


def slugify(topic):
    """`Token Tools: lệnh handoff` → `token-tools-lenh-handoff`."""
    plain = unicodedata.normalize("NFKD", topic.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch)).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
    return slug[:50].rstrip("-") or "ban-giao"


def missing_sections(text):
    return [section for section in SECTIONS if section not in text]


def save(topic, text, today=None, folder=None):
    """Write the note; never overwrites (adds -2, -3…). Returns (path, tokens). Raises ValueError when too long
    or a template section is missing."""
    tokens = estimate_tokens(text)
    if tokens > MAX_TOKENS:
        raise ValueError(f"bản bàn giao ~{tokens} token, quá giới hạn {MAX_TOKENS} — rút gọn rồi lưu lại")
    missing = missing_sections(text)
    if missing:
        raise ValueError(f"thiếu mục: {', '.join(missing)}")
    folder = Path(folder or handoffs_dir())
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{(today or date.today()).isoformat()}-{slugify(topic)}"
    path = folder / f"{stem}.md"
    number = 2
    while path.exists():
        path = folder / f"{stem}-{number}.md"
        number += 1
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path, tokens


def _git(cwd, *args):
    try:
        ran = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # rstrip only: `status --porcelain` lines start with a meaningful space (" M file" = changed, not staged).
    return ran.stdout.rstrip() if ran.returncode == 0 else None


def facts(cwd):
    """What the repo says about itself; `repo` is None outside a git work tree."""
    root = _git(cwd, "rev-parse", "--show-toplevel")
    result = {"cwd": str(cwd), "repo": root, "handoffs_dir": str(handoffs_dir()), "max_tokens": MAX_TOKENS,
              "template": TEMPLATE}
    if not root:
        return result
    status = _git(cwd, "status", "--porcelain") or ""
    staged = [line[3:] for line in status.splitlines() if line[:1] not in (" ", "?")]
    unstaged = [line[3:] for line in status.splitlines() if line[1:2] not in (" ", "") and line[:2] != "??"]
    result.update({
        "branch": _git(cwd, "branch", "--show-current") or "(detached)",
        "last_commits": (_git(cwd, "log", "--oneline", "-5") or "").splitlines(),
        "staged": staged,
        "unstaged": unstaged,
        "untracked": [line[3:] for line in status.splitlines() if line[:2] == "??"],
        "remotes": (_git(cwd, "remote") or "").splitlines(),
    })
    return result
