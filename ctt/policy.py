"""Model policy: write `model:`/`effort:` into agent frontmatter and `"model"` into settings.json."""
import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ctt.config import load_layered

MANAGED_KEYS = ("model", "effort")


def load_policy(explicit=None):
    return load_layered("policy.toml", explicit)


@dataclass
class SyncResult:
    agents_changed: int = 0
    agents_unchanged: int = 0
    agents_skipped: int = 0
    settings_changed: int = 0


def _roots(policy, extra_roots=()):
    return [Path(root) for root in [*policy.get("scan", {}).get("roots", []), *extra_roots]]


def agent_dirs(policy, extra_roots=(), user_agents=None, cwd=None):
    """(project name, agents folder) pairs. A root may itself be a project or hold projects one level down.
    With no roots configured, only the current folder is considered."""
    roots = _roots(policy, extra_roots) or [Path(cwd or Path.cwd())]
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        candidates = [root, *sorted(p for p in root.iterdir() if p.is_dir())]
        for project in candidates:
            agents = project / ".claude" / "agents"
            if agents.is_dir() and agents.resolve() not in seen:
                seen.add(agents.resolve())
                yield project.name, agents
    if policy.get("scan", {}).get("include_user_agents"):
        user_agents = Path(user_agents) if user_agents else Path.home() / ".claude" / "agents"
        if user_agents.is_dir() and user_agents.resolve() not in seen:
            yield "~user", user_agents


def target_for(policy, project, agent_name):
    """(role, {model, effort}) for an agent, with project overrides applied; (None, None) if unmapped."""
    role = policy.get("agent_role", {}).get(agent_name)
    if role is None and agent_name in policy.get("roles", {}):
        role = agent_name
    if role is None or role not in policy.get("roles", {}):
        return None, None
    settings = dict(policy["roles"][role])
    override = policy.get("projects", {}).get(project, {}).get("roles", {}).get(role)
    if override:
        settings.update(override)
    return role, settings


def rewrite_frontmatter(text, settings):
    """Text with model/effort set right after `description:`; None if the file has no frontmatter."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None
    managed = re.compile(rf"^({'|'.join(MANAGED_KEYS)})\s*:")
    header = [line for line in lines[1:end] if not managed.match(line)]
    insert_at = next((i + 1 for i, line in enumerate(header) if line.startswith("description:")), len(header))
    header[insert_at:insert_at] = [f"{key}: {settings[key]}" for key in MANAGED_KEYS if settings.get(key)]
    return newline.join([lines[0], *header, *lines[end:]])


def session_targets(policy, user_settings, extra_roots=()):
    """(label, settings.json path, model) for the main-session model: user default, then project overrides."""
    default = policy.get("session", {}).get("default")
    if default:
        yield "~user", Path(user_settings), default
    roots = _roots(policy, extra_roots)
    for project, override in policy.get("projects", {}).items():
        model = override.get("session", {}).get("default")
        if not model:
            continue
        folder = next((root / project for root in roots if (root / project).is_dir()), None)
        if folder is None:
            folder = next((root for root in roots if root.name == project and root.is_dir()), None)
        if folder is not None:
            yield project, folder / ".claude" / "settings.json", model


def _diff(original, updated):
    lines = difflib.unified_diff(original.splitlines(), updated.splitlines(), lineterm="", n=1)
    return "\n".join(list(lines)[2:])


def sync(policy, apply=False, user_settings=None, user_agents=None, extra_roots=(), cwd=None, out=print,
         agents_only=False):
    """Bring agents and settings in line with policy. Dry run unless apply=True; agents_only leaves settings.json."""
    result = SyncResult()
    user_settings = user_settings or Path.home() / ".claude" / "settings.json"

    for label, path, model in ([] if agents_only else session_targets(policy, user_settings, extra_roots)):
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        data = json.loads(original) if original.strip() else {}
        if data.get("model") == model:
            continue
        data["model"] = model
        updated = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        result.settings_changed += 1
        out(f"--- session {label}: {path}\n{_diff(original, updated)}")
        if apply:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(updated, encoding="utf-8")

    for project, folder in agent_dirs(policy, extra_roots, user_agents, cwd):
        for path in sorted(folder.glob("*.md")):
            role, settings = target_for(policy, project, path.stem)
            if role is None:
                out(f"[skip] {project}/{path.name}: chưa gán vai trong [agent_role]")
                result.agents_skipped += 1
                continue
            original = path.read_text(encoding="utf-8")
            updated = rewrite_frontmatter(original, settings)
            if updated is None:
                out(f"[skip] {project}/{path.name}: không có frontmatter")
                result.agents_skipped += 1
                continue
            if updated == original:
                result.agents_unchanged += 1
                continue
            result.agents_changed += 1
            out(f"--- {project}/{path.name}  (vai: {role})\n{_diff(original, updated)}")
            if apply:
                path.write_text(updated, encoding="utf-8", newline="")
    return result
