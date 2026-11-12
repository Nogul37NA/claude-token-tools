"""Which agents a session can call, and at which level: the project's (`<cwd>/.claude/agents`), the user's
(`~/.claude/agents`, replaced by a project agent of the same name) and this plugin's (`agents/`, called as
`token-tools:<name>`). For the control-room pane: a level label on each card, and the agents of each role.
"""
import re
from pathlib import Path

from ctt.config import REPO_ROOT

PLUGIN = "token-tools"
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.S)


def frontmatter(path):
    """{key: value} of an agent file's frontmatter (plain `key: value` lines); empty when it has none."""
    match = FRONTMATTER.match(Path(path).read_text(encoding="utf-8"))
    fields = {}
    for line in (match.group(1).splitlines() if match else []):
        key, sep, value = line.partition(":")
        if sep and key.strip() and not line.startswith((" ", "\t")):
            fields[key.strip()] = value.strip().strip("'\"")
    return fields


def _folder(folder, level, prefix=""):
    found = []
    for path in sorted(Path(folder).glob("*.md")) if Path(folder).is_dir() else []:
        fields = frontmatter(path)
        name = fields.get("name") or path.stem
        found.append({"name": name, "call": f"{prefix}{name}", "level": level, "model": fields.get("model"),
                      "path": str(path)})
    return found


def list_agents(cwd, policy, user_dir=None, plugin_dir=None):
    """Every callable agent with its level ('project' | 'user' | 'plugin') and role (None: no role)."""
    project = _folder(Path(cwd) / ".claude" / "agents", "project")
    taken = {agent["name"] for agent in project}
    user = [agent for agent in _folder(user_dir or Path.home() / ".claude" / "agents", "user")
            if agent["name"] not in taken]  # a project agent of the same name replaces the user's
    plugin = _folder(plugin_dir or REPO_ROOT / "agents", "plugin", f"{PLUGIN}:")
    roles = policy.get("roles", {})
    mapping = policy.get("agent_role", {})
    for agent in project + user + plugin:
        role = mapping.get(agent["name"], agent["name"])
        agent["role"] = role if role in roles else None
    return project + user + plugin
