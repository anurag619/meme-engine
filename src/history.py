"""Usage history — 7-day rotation tracking for meme templates.

Persists to ``~/jarvis-workspace/meme-engine/history.json``. Every time we
render a meme through ``daily_trending`` (or any caller that opts in),
``record_use`` appends an entry. ``recently_used_ids`` returns the set of
template ids that should be cooldown-blocked.

Format on disk::

    {
      "entries": [
        {"date": "2026-05-09", "ts": 1715216400,
         "template_id": "181913649", "format": "comparison",
         "topic": "AI replacing developers", "source": "imgflip"},
        ...
      ]
    }

Old entries are kept indefinitely (the file will be tiny — handful of memes
per day). If it ever gets noisy, ``prune_older_than`` is the knob.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "history.json"

DEFAULT_COOLDOWN_DAYS = 7


def _empty() -> dict[str, Any]:
    return {"entries": []}


def load() -> dict[str, Any]:
    """Load the history file, returning an empty shell if it doesn't exist."""
    if not HISTORY_PATH.exists():
        return _empty()
    try:
        data = json.loads(HISTORY_PATH.read_text())
        if not isinstance(data, dict) or "entries" not in data:
            return _empty()
        return data
    except (OSError, json.JSONDecodeError):
        return _empty()


def save(data: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(data, indent=2))


def record_use(
    template_id: str,
    *,
    fmt: str | None = None,
    topic: str = "",
    source: str = "imgflip",
    on_demand: bool = False,
) -> None:
    """Append a usage record. Safe to call concurrently — last writer wins,
    which is fine for our once-a-day cron.

    ``on_demand=True`` is a no-op — callers who render memes outside the
    cron (manual test runs, ad-hoc deliveries, the variety test) pass this
    flag so their renders don't pollute the cooldown view. Only the daily
    brief should record usage.
    """
    if on_demand:
        return
    data = load()
    data["entries"].append({
        "date": date.today().isoformat(),
        "ts": int(time.time()),
        "template_id": str(template_id),
        "format": fmt or "",
        "topic": topic or "",
        "source": source,
    })
    save(data)


def last_used_timestamps() -> dict[str, float]:
    """Return ``{template_id: unix_ts}`` of the most-recent use per template.

    Used by the matcher to pick the **least-recently-used** template from a
    fresh bucket — even after the 7-day cooldown clears, we still prefer the
    template we haven't touched in the longest time. Templates never used
    are absent from the dict (callers should treat absence as ``0``).
    """
    data = load()
    latest: dict[str, float] = {}
    for e in data.get("entries", []):
        tid = str(e.get("template_id") or "")
        if not tid:
            continue
        try:
            ts = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts > latest.get(tid, 0):
            latest[tid] = ts
    return latest


def recently_used_ids(days: int = DEFAULT_COOLDOWN_DAYS) -> set[str]:
    """Return the set of template ids used within the last ``days`` days."""
    if days <= 0:
        return set()
    data = load()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    return {
        str(e.get("template_id"))
        for e in data.get("entries", [])
        if e.get("template_id") and float(e.get("ts") or 0) >= cutoff_ts
    }


def recently_used_formats(days: int = DEFAULT_COOLDOWN_DAYS) -> list[str]:
    """Return formats used within ``days`` (most-recent-first, with repeats).

    Used by the matcher to *prefer* under-rotated formats — not to hard-block.
    """
    if days <= 0:
        return []
    data = load()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    rows = [
        e for e in data.get("entries", [])
        if e.get("format") and float(e.get("ts") or 0) >= cutoff_ts
    ]
    rows.sort(key=lambda e: e.get("ts") or 0, reverse=True)
    return [str(e["format"]) for e in rows]


def prune_older_than(days: int) -> int:
    """Drop entries older than ``days`` days. Returns the count removed."""
    if days <= 0:
        return 0
    data = load()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    before = len(data.get("entries", []))
    data["entries"] = [
        e for e in data.get("entries", [])
        if float(e.get("ts") or 0) >= cutoff_ts
    ]
    save(data)
    return before - len(data["entries"])


def reset() -> None:
    """Wipe the history file (used by tests)."""
    save(_empty())
