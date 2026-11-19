"""MCP servers of a session (for the control-room pane): configured servers + per-session call stats.

Servers come from ~/.claude.json (user / local), <cwd>/.mcp.json (project), installed plugins, the needs-auth cache,
and tool names seen in the session's transcripts (mcp__<server>__<tool>). Every source is read defensively.
"""
import json
import os
import re
from pathlib import Path

from ctt import config

CHARS_PER_TOKEN = 4
_TOOL = re.compile(r"^mcp__(.+?)__(.+)$")
_USE_ID = re.compile(r'"tool_use_id"\s*:\s*"([^"]+)"')


def claude_home():
    return Path(os.environ.get("CTT_CLAUDE_HOME") or Path.home() / ".claude")


def claude_json_path():
    return Path(os.environ.get("CTT_CLAUDE_JSON") or Path.home() / ".claude.json")


def prefix_of(name):
    """How Claude Code spells a server inside tool names."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _servers(table):
    return {k: v for k, v in table.items() if isinstance(v, dict)} if isinstance(table, dict) else {}


def _type(spec):
    kind = str(spec.get("type") or "").lower()
    if kind in ("stdio", "http", "sse"):
        return kind
    if spec.get("command"):
        return "stdio"
    if spec.get("url"):
        return "http"
    return "unknown"


def _same_path(left, right):
    def norm(text):
        return os.path.normcase(os.path.normpath(str(text))).replace("\\", "/").rstrip("/")
    return norm(left) == norm(right)


def _settings_layers(home, cwd):
    """Settings files, lowest priority first: user, project, project-local."""
    return [_load(home / "settings.json"), _load(cwd / ".claude" / "settings.json"),
            _load(cwd / ".claude" / "settings.local.json")]


def _plugin_servers(home, cwd, layers):
    """{'plugin:<plugin>:<key>': spec} of enabled installed plugins that apply to cwd."""
    installed = _load(home / "plugins" / "installed_plugins.json").get("plugins")
    enabled = {}
    for layer in layers:
        if isinstance(layer.get("enabledPlugins"), dict):
            enabled.update(layer["enabledPlugins"])
    found = {}
    for key, installs in (installed.items() if isinstance(installed, dict) else ()):
        if enabled.get(key) is False:
            continue
        plugin = key.split("@")[0]
        entry = next((i for i in installs if isinstance(i, dict) and i.get("installPath")
                      and (not i.get("projectPath") or _same_path(i["projectPath"], cwd))), None) \
            if isinstance(installs, list) else None
        if not entry:
            continue
        root = Path(entry["installPath"])
        mcp_file = _load(root / ".mcp.json")
        specs = _servers(mcp_file.get("mcpServers", mcp_file))
        manifest = _load(root / ".claude-plugin" / "plugin.json") or _load(root / "plugin.json")
        declared = manifest.get("mcpServers")
        for item in declared if isinstance(declared, list) else [declared]:
            if isinstance(item, str):
                loaded = _load(root / item)
                specs.update(_servers(loaded.get("mcpServers", loaded)))
            elif isinstance(item, dict):
                specs.update(_servers(item.get("mcpServers", item)))
        for name, spec in specs.items():
            found[f"plugin:{plugin}:{name}"] = spec
    return found


def _stats(files):
    """{prefix: {calls, errors, result_tokens, last_at, tools}} from the session's transcripts."""
    stats = {}
    for path in files:
        pending = {}  # tool_use_id -> prefix, so a result can be tied back to its server
        seen = set()
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if '"mcp__' in line and '"tool_use"' in line:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    message = entry.get("message") if isinstance(entry, dict) else None
                    blocks = message.get("content") if isinstance(message, dict) else None
                    for block in blocks if isinstance(blocks, list) else ():
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        match = _TOOL.match(str(block.get("name") or ""))
                        if not match or block.get("id") in seen:
                            continue
                        seen.add(block.get("id"))
                        pending[block.get("id")] = match.group(1)
                        item = stats.setdefault(match.group(1), {"calls": 0, "errors": 0, "result_tokens": 0,
                                                                 "last_at": None, "tools": {}})
                        item["calls"] += 1
                        item["tools"][match.group(2)] = item["tools"].get(match.group(2), 0) + 1
                        stamp = entry.get("timestamp")
                        if isinstance(stamp, str) and (item["last_at"] is None or stamp > item["last_at"]):
                            item["last_at"] = stamp
                elif pending and '"tool_result"' in line:
                    ids = [i for i in _USE_ID.findall(line) if i in pending]
                    if not ids:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    message = entry.get("message") if isinstance(entry, dict) else None
                    blocks = message.get("content") if isinstance(message, dict) else None
                    for block in blocks if isinstance(blocks, list) else ():
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        prefix = pending.pop(block.get("tool_use_id"), None)
                        if prefix is None:
                            continue
                        item = stats[prefix]
                        if block.get("is_error"):
                            item["errors"] += 1
                        content = block.get("content")
                        text = content if isinstance(content, str) else "".join(
                            part.get("text", "") for part in content
                            if isinstance(part, dict) and isinstance(part.get("text"), str)) \
                            if isinstance(content, list) else ""
                        item["result_tokens"] += round(len(text) / CHARS_PER_TOKEN)
    return stats


def list_servers(cwd, projects_dir=None, session_id=None):
    """{'servers': [...]} in the shape the pane expects; never raises."""
    try:
        return _list(Path(cwd), projects_dir or config.projects_dir(), session_id)
    except Exception as error:  # the pane must always get JSON
        return {"servers": [], "error": f"{type(error).__name__}: {error}"}


def _list(cwd, projects_dir, session_id):
    home = claude_home()
    servers = {}  # name -> {source, type}

    def add(name, source, kind):
        servers.setdefault(name, {"source": source, "type": kind})

    user_config = _load(claude_json_path())
    for name, spec in _servers(user_config.get("mcpServers")).items():
        add(name, "user", _type(spec))
    project_config = {}
    for key, value in (user_config.get("projects") or {}).items() if isinstance(user_config.get("projects"), dict) else ():
        if isinstance(value, dict) and _same_path(key, cwd):
            project_config = value
            break
    for name, spec in _servers(project_config.get("mcpServers")).items():
        add(name, "local", _type(spec))
    project_file = _load(cwd / ".mcp.json")
    for name, spec in _servers(project_file.get("mcpServers")).items():
        add(name, "project", _type(spec))
    layers = _settings_layers(home, cwd)
    for name, spec in _plugin_servers(home, cwd, layers).items():
        add(name, "plugin", _type(spec))

    disabled = set()
    for key in ("disabledMcpjsonServers", "disabledMcpServers"):
        if isinstance(project_config.get(key), list):
            disabled.update(str(i) for i in project_config[key])
    needs_auth = set(_load(home / "mcp-needs-auth-cache.json"))
    for name in needs_auth:
        if name.startswith("claude.ai "):
            add(name, "claudeai", "http")
        elif name.startswith("plugin:"):
            add(name, "plugin", "unknown")

    stats = {}
    if session_id:
        from ctt import live
        stats = _stats(list(live.session_files(projects_dir, session_id).values()))
    by_prefix = {}
    for name in servers:
        by_prefix.setdefault(prefix_of(name), name)  # same spelling twice: the first keeps it
    merged = {}  # real name -> stats
    for prefix, item in stats.items():
        if prefix.startswith("ccd_"):
            continue
        name = by_prefix.get(prefix)
        if name is None:
            if prefix.startswith("claude_ai_"):
                name, source = "claude.ai " + prefix[len("claude_ai_"):].replace("_", " "), "claudeai"
            else:
                name, source = prefix, "plugin" if prefix.startswith("plugin_") else "unknown"
            add(name, source, "http" if source == "claudeai" else "unknown")
        if name in merged:
            total = merged[name]
            for key in ("calls", "errors", "result_tokens"):
                total[key] += item[key]
            for tool, count in item["tools"].items():
                total["tools"][tool] = total["tools"].get(tool, 0) + count
            total["last_at"] = max(filter(None, (total["last_at"], item["last_at"])), default=None)
        else:
            merged[name] = {**item, "tools": dict(item["tools"])}

    approved = set()
    approve_all = False
    for layer in layers + [project_config]:
        if isinstance(layer.get("enabledMcpjsonServers"), list):
            approved.update(str(i) for i in layer["enabledMcpjsonServers"])
        if layer.get("enableAllProjectMcpServers") is True:
            approve_all = True
    project_names = set(_servers(project_file.get("mcpServers")))

    result = []
    for name, info in servers.items():
        if prefix_of(name).startswith("ccd_"):
            continue
        item = merged.get(name) or {"calls": 0, "errors": 0, "result_tokens": 0, "last_at": None, "tools": {}}
        status = "disabled" if name in disabled else "needs_auth" if name in needs_auth else "configured"
        if status == "configured" and info["source"] == "project" and name in project_names \
                and not approve_all and name not in approved:
            status = "unapproved"
        result.append({"name": name, **info, "status": status, **item})
    result.sort(key=lambda s: (-s["calls"], s["name"].lower()))
    return {"servers": result}


def describe(data):
    rows = data["servers"]
    lines = [f"{'Server':<34}{'Nguồn':<9}{'Loại':<9}{'Trạng thái':<12}{'Gọi':>5}{'Lỗi':>5}{'Token KQ':>10}"]
    for s in rows:
        lines.append(f"{s['name'][:33]:<34}{s['source']:<9}{s['type']:<9}{s['status']:<12}"
                     f"{s['calls']:>5}{s['errors']:>5}{s['result_tokens']:>10}")
    if not rows:
        lines.append("(không có MCP server nào)")
    if data.get("error"):
        lines.append(f"Lỗi: {data['error']}")
    return "\n".join(lines)
