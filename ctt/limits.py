"""Relate API-equivalent spend to subscription plan limits (5-hour / weekly windows).

On a subscription plan the real budget is "% of the 5-hour and weekly windows", not dollars. Each snapshot pairs
the percent the app shows with the API-equivalent cost ctt computes from local transcripts over the same window,
giving "$ per 1%". Usage outside this machine (claude.ai chat, other computers) also fills the windows, so a local
figure is a lower bound of the true "$ per 1%".
"""
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

WINDOW_LENGTH = {"5h": timedelta(hours=5), "week": timedelta(days=7)}


@dataclass
class Snapshot:
    taken_at: str
    window: str
    percent_used: float
    window_start: str
    window_end: str
    api_equivalent_usd: float
    tokens: int
    usd_per_percent: float


def window_spend(scan, prices, start, end):
    """API-equivalent USD and tokens of all requests in [start, end)."""
    usd = 0.0
    tokens = 0
    for request in scan.requests:
        if start <= request.timestamp < end:
            usd += prices.cost(request)[0]
            tokens += request.total
    return usd, tokens


def snapshot(scan, prices, window, percent_used, resets_at, now=None):
    """resets_at: aware datetime when the window resets. The window is [resets_at - length, now]."""
    now = now or datetime.now(timezone.utc)
    start = resets_at - WINDOW_LENGTH[window]
    usd, tokens = window_spend(scan, prices, start, now)
    return Snapshot(
        taken_at=now.isoformat(timespec="seconds"), window=window, percent_used=percent_used,
        window_start=start.isoformat(timespec="seconds"), window_end=now.isoformat(timespec="seconds"),
        api_equivalent_usd=round(usd, 4), tokens=tokens,
        usd_per_percent=round(usd / percent_used, 4) if percent_used else 0.0)


def append(path, snapshots):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for item in snapshots:
            handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")


def load(path):
    if not path.exists():
        return []
    return [Snapshot(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def median_usd_per_percent(snapshots, window="week"):
    """(median $ per 1%, number of usable snapshots) or (None, 0)."""
    values = sorted(s.usd_per_percent for s in snapshots if s.window == window and s.percent_used >= 5)
    return (values[len(values) // 2], len(values)) if values else (None, 0)


def summary(snapshots):
    """Median $ per 1% per window (snapshots with < 5% used are too noisy and skipped)."""
    lines = []
    for window in WINDOW_LENGTH:
        values = sorted(s.usd_per_percent for s in snapshots if s.window == window and s.percent_used >= 5)
        if values:
            median = values[len(values) // 2]
            lines.append(f"{window:<5} {len(values)} mốc · trung vị ${median:.2f} / 1% "
                         f"(khoảng ${values[0]:.2f}–${values[-1]:.2f}) → 100% ≈ ${median * 100:,.0f} API quy đổi")
        else:
            lines.append(f"{window:<5} chưa có mốc nào ≥ 5%")
    return lines
