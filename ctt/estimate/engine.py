"""Turn a confirmed task profile into an estimate. Deterministic: same profile + same data → same numbers."""
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from ctt.config import load_layered
from ctt.sessions import LABEL_CODES, percentile

LABEL_ORDER = ("qa", "investigation", "small", "medium", "large")
LABEL_NAMES = {"qa": "Hỏi đáp ngắn", "investigation": "Điều tra / phân tích", "small": "Sửa nhỏ (1–3 file)",
               "medium": "Tính năng / sửa vừa (4–10 file)", "large": "Thay đổi lớn (>10 file)"}
GATE_TEXT = {
    "developer_opus": "Bước code dùng Opus · high",
    "reviewer_opus": "Reviewer Opus bắt buộc",
    "tests_required": "Test bắt buộc",
    "regression_tests": "Test hồi quy các màn dùng chung",
    "human_gate": "Người duyệt trước commit",
    "separate_commit": "Migration commit riêng",
    "split_clarify": "Tách giai đoạn làm rõ yêu cầu, ước lượng lại sau",
}
LEVEL_NAMES = {"low": "Thấp", "mid": "Vừa", "high": "Cao"}


def load_settings(explicit=None):
    return load_layered("estimate.toml", explicit)


def load_risks(explicit=None):
    return load_layered("risks.toml", explicit)


@dataclass
class Band:
    p50_tokens: float
    p90_tokens: float
    p50_usd: float
    p90_usd: float
    samples: int
    reference_model: str
    source: str  # "calibration" | "fallback"


def calibration_bands(sessions, settings):
    """P50/P90 per task group from real sessions; groups with too few sessions use [fallback]."""
    groups = {code: [] for code in LABEL_ORDER}
    for session in sessions:
        groups[LABEL_CODES[session.label]].append(session)
    fallback = settings["fallback"]
    bands = {}
    for code, items in groups.items():
        if len(items) >= settings["min_samples"]:
            reference = Counter(s.main_model for s in items).most_common(1)[0][0]
            bands[code] = Band(percentile([s.total for s in items], .5), percentile([s.total for s in items], .9),
                               percentile([s.cost for s in items], .5), percentile([s.cost for s in items], .9),
                               len(items), reference, "calibration")
        else:
            spec = fallback[code]
            bands[code] = Band(spec["p50_tokens"], spec["p90_tokens"], spec["p50_usd"], spec["p90_usd"],
                               len(items), fallback["model"], "fallback")
    return bands


def files_estimate(kind, scope, factors):
    if scope.get("files_estimate") is not None:
        return scope["files_estimate"]
    get = lambda key: scope.get(key) or 0  # noqa: E731
    by_contract = (get("apis") * factors["per_api"] + get("entities") * factors["per_entity"]
                   + get("migrations") * factors["per_migration"])
    if kind == "change_existing":
        return max(round(get("impacted_classes") * factors["impacted_factor"]), by_contract)
    if kind == "new_feature":
        return get("screens") * factors["per_screen"] + by_contract
    return 0


def label_code(kind, files):
    if kind in ("qa", "investigation"):
        return kind
    if files <= 3:
        return "small"
    return "medium" if files <= 10 else "large"


def detect_risks(explicit, scope, branch="", unknowns=()):
    """Explicit risks plus those the profile itself shows; order kept, no duplicates."""
    found = list(explicit)
    auto = []
    if (scope.get("impacted_classes") or 0) > 15:
        auto.append("wide_impact")
    if (scope.get("migrations") or 0) > 0:
        auto.append("flyway")
    if (scope.get("integrations") or 0) > 0:
        auto.append("external_dependency")
    if "prod" in (branch or "").lower():
        auto.append("prod_hotfix")
    if len(unknowns) >= 3:
        auto.append("unclear_requirements")
    for code in auto:
        if code not in found:
            found.append(code)
    return found


def risk_level(points, levels):
    if points <= levels["low_max"]:
        return "low"
    return "mid" if points <= levels["mid_max"] else "high"


def _steps_for(kind, settings):
    key = "qa" if kind == "qa" else "investigation" if kind == "investigation" else "change"
    return settings["steps"][key]


def _choose(role, policy, difficulty_high, level, effects):
    chosen = dict(policy.get("roles", {}).get(role) or {"model": "opus", "effort": "medium"})
    if role == "developer":
        if "developer_opus" in effects or (difficulty_high and level != "low"):
            chosen = {"model": "opus", "effort": "high"}
        elif level != "low":
            chosen["effort"] = "high"
    if role == "reviewer" and "reviewer_opus" in effects:
        chosen["model"] = "opus"
    return chosen


def estimate_part(name, kind, scope, risk_codes, ctx):
    """One task or one feature of a product."""
    settings, bands, catalog = ctx["settings"], ctx["bands"], ctx["risks"]
    files = files_estimate(kind, scope, settings["files"])
    code = label_code(kind, files)
    band = bands[code]
    items = [(c, catalog[c]["name"], catalog[c]["points"]) for c in risk_codes]
    points = sum(p for _, _, p in items)
    level = risk_level(points, settings["risk_levels"])
    multiplier = settings["risk_levels"]["p90_multiplier"][level]
    effects = set()
    for c in risk_codes:
        multiplier *= catalog[c].get("p90_extra", 1.0)
        effects.update(catalog[c].get("effects", []))
    if level != "low":
        effects.add("human_gate")
    difficulty_high = code in ("medium", "large", "investigation")

    reference_price = ctx["prices"].lookup(band.reference_model)[0]
    p90_tokens = band.p90_tokens * multiplier
    p90_usd_ref = band.p90_usd * multiplier
    steps = []
    for role, share in _steps_for(kind, settings).items():
        chosen = _choose(role, ctx["policy"], difficulty_high, level, effects)
        model_id = settings["model_ids"].get(chosen["model"], chosen["model"])
        ratio = ctx["prices"].lookup(model_id)[0].input / reference_price.input
        steps.append({
            "role": role, "name": settings["step_names"].get(role, role), "share": share,
            "model": chosen["model"], "effort": chosen.get("effort", ""),
            "p50_tokens": band.p50_tokens * share, "p90_tokens": p90_tokens * share,
            "p50_usd": band.p50_usd * share * ratio, "p90_usd": p90_usd_ref * share * ratio,
        })
    return {
        "name": name, "kind": kind, "files_estimate": files, "label": code, "label_name": LABEL_NAMES[code],
        "band": {"samples": band.samples, "source": band.source, "reference_model": band.reference_model},
        "risks": [{"code": c, "name": n, "points": p} for c, n, p in items], "risk_points": points,
        "risk_level": level, "p90_multiplier": round(multiplier, 4),
        "gates": [GATE_TEXT[e] for e in GATE_TEXT if e in effects],
        "steps": steps,
        "p50_tokens": sum(s["p50_tokens"] for s in steps), "p90_tokens": sum(s["p90_tokens"] for s in steps),
        "p50_usd": sum(s["p50_usd"] for s in steps), "p90_usd": sum(s["p90_usd"] for s in steps),
    }


def combine(parts, key):
    """(P50, P90) of a sum of independent parts: P50 adds up; the spread above P50 adds in quadrature."""
    p50 = sum(part[f"p50_{key}"] for part in parts)
    spread = math.sqrt(sum((part[f"p90_{key}"] - part[f"p50_{key}"]) ** 2 for part in parts))
    return p50, p50 + spread


def backtest(sessions, prices, policy, settings, risks):
    """Leave-one-out check of the estimator on past sessions that edited code, given their true file count.
    It measures the bands and risk factors, not how well a profile is extracted from a ticket."""
    from ctt.estimate.profile import Profile
    rows = []
    for session in sessions:
        if not session.files:
            continue
        others = [s for s in sessions if s is not session]
        scope = {"files_estimate": len(session.files), "migrations": 1 if session.migration else 0}
        task = Profile(id=session.session_id[:8], title=session.title or session.session_id[:8], kind="change_existing",
                       scope=scope, branch=session.branch)
        est = estimate(task, others, prices, policy, settings, risks)
        rows.append({"title": task.title, "files": len(session.files), "multi_day": session.multi_day,
                     "actual": session.total, "p50": est["tokens"]["p50"], "p90": est["tokens"]["p90"],
                     "budget": est["tokens"]["budget"], "within_p90": session.total <= est["tokens"]["p90"],
                     "within_budget": session.total <= est["tokens"]["budget"], "risk_level": est["risk_level"]})
    return rows


def estimate(profile, sessions, prices, policy, settings, risks, usd_per_percent=None, now=None):
    """Full estimate as a JSON-ready dict."""
    ctx = {"settings": settings, "bands": calibration_bands(sessions, settings), "risks": risks,
           "prices": prices, "policy": policy}
    top_risks = detect_risks(profile.risks, profile.scope, profile.branch, profile.unknowns)
    if profile.kind == "product":
        parts = [estimate_part(f.name, f.kind, f.scope,
                               detect_risks(f.risks + top_risks, f.scope), ctx) for f in profile.features]
    else:
        parts = [estimate_part(profile.title, profile.kind, profile.scope, top_risks, ctx)]

    p50_tokens, p90_tokens = combine(parts, "tokens")
    p50_usd, p90_usd = combine(parts, "usd")
    factor = settings["budget_factor"]
    weekly = None
    if usd_per_percent:
        weekly = {"p50": p50_usd / usd_per_percent, "p90": p90_usd / usd_per_percent,
                  "budget": p90_usd * factor / usd_per_percent, "usd_per_percent": usd_per_percent}

    min_samples = min(part["band"]["samples"] for part in parts)
    reasons = []
    if any(part["band"]["source"] == "fallback" for part in parts):
        reasons.append(f"có nhóm dưới {settings['min_samples']} phiên → dùng số mặc định")
    if not profile.sources_complete:
        reasons.append("có nguồn chưa đọc hết")
    if len(profile.unknowns) >= 3:
        reasons.append(f"{len(profile.unknowns)} câu hỏi mở")
    if reasons:
        confidence = "low"
    elif min_samples >= settings["high_confidence_samples"] and len(profile.unknowns) <= 1:
        confidence = "high"
    else:
        confidence = "medium"
        reasons.append(f"nhóm có {min_samples} phiên mẫu" if min_samples < settings["high_confidence_samples"]
                       else f"{len(profile.unknowns)} câu hỏi mở")

    worst = max(parts, key=lambda part: part["risk_points"])
    assumptions = [
        f"{part['name']}: nhóm '{part['label_name']}' — "
        + (f"{part['band']['samples']} phiên hiệu chỉnh" if part["band"]["source"] == "calibration"
           else "số mặc định (thiếu phiên mẫu)")
        + f", giá tham chiếu {part['band']['reference_model']}" for part in parts]
    assumptions.append("Chi phí là giá API quy đổi; bước dùng model khác model tham chiếu được nhân theo tỉ lệ giá.")
    if weekly is None:
        assumptions.append("Chưa có mốc hạn mức (ctt limits) → chưa đổi ra % hạn mức tuần.")
    else:
        assumptions.append(f"% hạn mức tuần tính với ${usd_per_percent:.2f} / 1% (cận dưới).")
    if profile.kind == "investigation":
        assumptions.append("Task điều tra: phiếu chỉ tính giai đoạn chẩn đoán; ước lượng lại phần sửa khi có nguyên nhân.")

    return {
        "id": profile.id, "title": profile.title, "kind": profile.kind, "repo": profile.repo, "branch": profile.branch,
        "created_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "sources": profile.sources, "unknowns": profile.unknowns,
        "tokens": {"p50": p50_tokens, "p90": p90_tokens, "budget": p90_tokens * factor},
        "usd": {"p50": p50_usd, "p90": p90_usd, "budget": p90_usd * factor},
        "weekly_percent": weekly, "checkpoints": list(settings["checkpoints"]),
        "risk_level": worst["risk_level"], "risk_points": worst["risk_points"],
        "confidence": confidence, "confidence_reasons": reasons, "assumptions": assumptions,
        "parts": parts,
    }
