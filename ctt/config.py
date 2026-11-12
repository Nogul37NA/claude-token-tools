"""Paths and layered TOML config (defaults shipped in the repo, overridden per user)."""
import os
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_DIR = REPO_ROOT / "defaults"


def ctt_home():
    """Per-user folder for the user's own policy, prices and reports."""
    return Path(os.environ.get("CTT_HOME") or Path.home() / ".claude" / "claude-token-tools")


def projects_dir():
    """Where Claude Code keeps session transcripts."""
    return Path(os.environ.get("CTT_CLAUDE_PROJECTS") or Path.home() / ".claude" / "projects")


def reports_dir():
    return ctt_home() / "reports"


def load_toml(path):
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def deep_merge(base, override):
    """Merge override into base; nested tables merge, anything else is replaced."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_layered(file_name, explicit=None):
    """defaults/<file_name>, then the user's copy in ctt_home() (or an explicit file) on top."""
    data = load_toml(DEFAULTS_DIR / file_name)
    override = Path(explicit) if explicit else ctt_home() / file_name
    if override.exists():
        data = deep_merge(data, load_toml(override))
    return data
