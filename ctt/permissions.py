"""Role permission profiles for subagents: permissions.toml merged with the agent → role map of policy.toml.

The checking itself runs in the control-room mod (`hooks/permissions.ts`) at `tool.check`, on every tool call;
this module loads, validates and hands the merged profile over as JSON, and writes the changes the pane makes
into the user's own override file.
"""
import json
import re
import shutil

from ctt.config import ctt_home, load_layered, load_toml
from ctt.policy import load_policy

OUTSIDE = ("deny", "ask")
LISTS = ("write", "ask_write", "bash")


def load_permissions(explicit=None, policy_file=None):
    """{common, always_ask, roles, agent_role}. Raises ValueError on a profile the mod could not apply."""
    data = load_layered("permissions.toml", explicit)
    policy = load_policy(policy_file)
    roles = {}
    for name, role in data.get("roles", {}).items():
        outside = role.get("outside", "deny")
        if outside not in OUTSIDE:
            raise ValueError(f"[roles.{name}] outside = {outside!r}: chỉ nhận {' | '.join(OUTSIDE)}")
        roles[name] = {
            "write": list(role.get("write", [])),
            "ask_write": list(role.get("ask_write", [])),
            "bash": list(role.get("bash", [])),
            "outside": outside,
            "enabled": bool(role.get("enabled", True)),
        }
    always_ask = list(data.get("always_ask", {}).get("bash", []))
    for pattern in always_ask:
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f"[always_ask] bash {pattern!r}: {error}") from error
    budget = policy.get("budget", {})
    caps = {}
    for name, cap in budget.get("roles", {}).items():
        if not isinstance(cap, (int, float)) or cap <= 0:
            raise ValueError(f"[budget.roles] {name} = {cap!r}: cần số token dương")
        caps[name] = int(cap)
    sessions = policy.get("sessions", {})
    limits = {}
    for key, fallback in (("max_depth", 2), ("max_children", 4)):
        value = sessions.get(key, fallback)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"[sessions] {key} = {value!r}: cần số nguyên ≥ 0")
        limits[key] = value
    return {
        "common": {"bash": list(data.get("common", {}).get("bash", []))},
        "always_ask": {"bash": always_ask},
        "roles": roles,
        "agent_role": dict(policy.get("agent_role", {})),
        # M4: per-role token caps when the session has no approved estimate, and the role models of policy.toml.
        "budget": {"roles": caps, "enforce_model": bool(budget.get("enforce_model", False)),
                   "raise_share": float(budget.get("raise_share", 0.5))},
        "role_models": {name: spec.get("model") for name, spec in policy.get("roles", {}).items()
                        if isinstance(spec, dict) and spec.get("model")},
        # 0.16: how deep sessions may open sessions, and how many children one may have open at once.
        "sessions": limits,
    }


def describe(profile):
    """The merged profile as a short table for the terminal."""
    lines = []
    for name, role in profile["roles"].items():
        agents = sorted(agent for agent, mapped in profile["agent_role"].items() if mapped == name and agent != name)
        off = "" if role.get("enabled", True) else " — ĐÃ TẮT"
        lines.append(f"{name}{' (' + ', '.join(agents) + ')' if agents else ''}{off}")
        lines.append(f"  ghi: {', '.join(role['write']) or 'không'}"
                     f"{' · hỏi: ' + ', '.join(role['ask_write']) if role['ask_write'] else ''}")
        lines.append(f"  bash: {', '.join(role['bash']) or 'chỉ lệnh chung'} · ngoài quyền: "
                     f"{'chặn' if role['outside'] == 'deny' else 'hỏi'}")
    lines.append(f"lệnh chung: {', '.join(profile['common']['bash'])}")
    lines.append(f"luôn hỏi: {' | '.join(profile['always_ask']['bash'])}")
    return "\n".join(lines)


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Literal strings keep regexes and Windows paths as typed; what a literal cannot hold goes as JSON, whose
        # escapes TOML's basic strings share.
        if "'" not in value and "\n" not in value:
            return f"'{value}'"
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ValueError(f"không ghi được giá trị {value!r} vào TOML")


def dump_toml(data, prefix=""):
    """A small TOML writer for what the override file holds: tables of strings, booleans and string lists."""
    blocks = []
    scalars = [f"{key} = {_toml_value(value)}" for key, value in data.items() if not isinstance(value, dict)]
    if scalars:
        blocks.append((f"[{prefix}]\n" if prefix else "") + "\n".join(scalars))
    for key, value in data.items():
        if isinstance(value, dict):
            inner = dump_toml(value, f"{prefix}.{key}" if prefix else key)
            if inner:
                blocks.append(inner)
    return "\n\n".join(blocks)


HEADER = ("# Quyền theo vai — bản riêng của máy này (ghi đè defaults/permissions.toml).\n"
          "# Khung Phòng điều hành ghi lại file này khi bạn sửa quyền trên khung; bản trước: permissions.toml.bak\n\n")


def set_role(role, *, outside=None, enabled=None, add=None, remove=None, reset=False, path=None):
    """Change one role in the user's override file and return the merged profile.

    add / remove: (list name, value), list name one of write | ask_write | bash. reset drops the role's override.
    """
    path = path or ctt_home() / "permissions.toml"
    override = load_toml(path) if path.exists() else {}
    merged = load_permissions(path if path.exists() else None)
    if role not in merged["roles"]:
        raise ValueError(f"không có vai {role!r}")
    roles = override.setdefault("roles", {})
    if reset:
        roles.pop(role, None)
    else:
        current = dict(merged["roles"][role])
        if outside is not None:
            if outside not in OUTSIDE:
                raise ValueError(f"outside = {outside!r}: chỉ nhận {' | '.join(OUTSIDE)}")
            current["outside"] = outside
        if enabled is not None:
            current["enabled"] = bool(enabled)
        for change, adding in ((add, True), (remove, False)):
            if change is None:
                continue
            name, value = change
            value = value.strip()
            if name not in LISTS:
                raise ValueError(f"danh sách {name!r}: chỉ nhận {' | '.join(LISTS)}")
            if not value:
                raise ValueError("giá trị trống")
            items = [item for item in current[name] if item != value]
            current[name] = items + [value] if adding else items
        roles[role] = current
    if not roles:
        override.pop("roles")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    path.write_text(HEADER + dump_toml(override) + "\n", encoding="utf-8")
    return load_permissions(path)
