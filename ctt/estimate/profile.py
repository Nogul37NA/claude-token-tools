"""Task profile: what Claude extracted from the inputs and the user confirmed. Plain JSON."""
import json
from dataclasses import dataclass, field
from datetime import datetime

KINDS = ("qa", "investigation", "change_existing", "new_feature", "product")
SCOPE_KEYS = ("screens", "apis", "entities", "migrations", "integrations", "impacted_classes", "files_estimate")
SOURCE_TYPES = ("text", "jira", "file", "wiki", "figma", "html_mockup", "image", "code")


class ProfileError(ValueError):
    """Raised with every problem found, one per line, so the user can fix the profile in one pass."""


@dataclass
class Feature:
    name: str
    kind: str
    scope: dict
    risks: list = field(default_factory=list)


@dataclass
class Profile:
    id: str
    title: str
    kind: str
    scope: dict
    sources: list = field(default_factory=list)
    features: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    unknowns: list = field(default_factory=list)
    repo: str = ""
    branch: str = ""

    @property
    def sources_complete(self):
        return all(source.get("complete", True) for source in self.sources)


def _scope_errors(scope, where):
    errors = []
    if not isinstance(scope, dict):
        return [f"{where}.scope phải là object"]
    for key, value in scope.items():
        if key not in SCOPE_KEYS:
            errors.append(f"{where}.scope.{key}: không biết trường này (dùng: {', '.join(SCOPE_KEYS)})")
        elif value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            errors.append(f"{where}.scope.{key}: phải là số nguyên ≥ 0 (đang là {value!r})")
    return errors


def parse(data, known_risks):
    """Profile from a dict; raises ProfileError listing every problem."""
    errors = []
    if not isinstance(data, dict):
        raise ProfileError("Hồ sơ phải là một object JSON")
    kind = data.get("kind")
    if kind not in KINDS:
        errors.append(f"kind: phải là một trong {', '.join(KINDS)} (đang là {kind!r})")
    if not str(data.get("title", "")).strip():
        errors.append("title: không được trống")
    scope = data.get("scope") or {}
    errors += _scope_errors(scope, "profile")
    for name in ("risks", "unknowns", "sources", "features"):
        if not isinstance(data.get(name, []), list):
            errors.append(f"{name}: phải là mảng")
    for code in data.get("risks", []) if isinstance(data.get("risks", []), list) else []:
        if code not in known_risks:
            errors.append(f"risks: không biết rủi ro '{code}' (có: {', '.join(sorted(known_risks))})")
    for index, source in enumerate(data.get("sources", []) if isinstance(data.get("sources", []), list) else []):
        if not isinstance(source, dict) or source.get("type") not in SOURCE_TYPES:
            errors.append(f"sources[{index}].type: phải là một trong {', '.join(SOURCE_TYPES)}")

    features = []
    raw_features = data.get("features", []) if isinstance(data.get("features", []), list) else []
    for index, raw in enumerate(raw_features):
        where = f"features[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: phải là object")
            continue
        if raw.get("kind") not in ("change_existing", "new_feature"):
            errors.append(f"{where}.kind: phải là change_existing hoặc new_feature")
        errors += _scope_errors(raw.get("scope") or {}, where)
        for code in raw.get("risks", []):
            if code not in known_risks:
                errors.append(f"{where}.risks: không biết rủi ro '{code}'")
        features.append(Feature(str(raw.get("name") or f"Tính năng {index + 1}"), raw.get("kind"),
                                raw.get("scope") or {}, list(raw.get("risks", []))))
    if kind == "product" and not features:
        errors.append("features: task loại product cần ít nhất một tính năng")
    if errors:
        raise ProfileError("\n".join(errors))

    return Profile(
        id=str(data.get("id") or datetime.now().strftime("est-%Y%m%d-%H%M%S")),
        title=str(data["title"]).strip(), kind=kind, scope=dict(scope),
        sources=list(data.get("sources", [])), features=features, risks=list(data.get("risks", [])),
        unknowns=[str(item) for item in data.get("unknowns", [])],
        repo=str(data.get("repo", "")), branch=str(data.get("branch", "")))


def load(path, known_risks):
    with open(path, encoding="utf-8") as handle:
        return parse(json.load(handle), known_risks)
