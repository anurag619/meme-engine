"""Non-Imgflip template pool — fresh meme formats sourced from the web.

Imgflip's trending list is solid but slow to turn over: a template that goes
viral on Twitter / Reddit this week often takes months to land in the API. To
keep the pool fresh we layer a manually-curated extras file on top.

## Storage

``~/jarvis-workspace/meme-engine/web_templates.json``::

    {
      "updated_at": 1715216400,
      "templates": [
        {
          "id": "web-2026-05-skibidi-toilet",   # any unique string is fine
          "name": "Skibidi Toilet Reaction",
          "url": "https://i.imgur.com/abc.jpg",  # public direct link
          "format": "reaction",                  # one of ALL_FORMATS
          "box_count": 2,
          "source": "web:knowyourmeme",
          "added_on": "2026-05-09"
        },
        ...
      ]
    }

## Refresh workflow (Jarvis, not Python)

Once a week (or on demand), Jarvis runs a WebSearch for "new meme templates
2026" / "viral meme formats this month", inspects the results, and appends
any genuinely-new entries to this file. The Python side is read-only — we
deliberately don't call WebSearch from inside the cron because:

  1. Anthropic's WebSearch is gated to Claude tools, not generic Python.
  2. Curation needs taste, not regex.
  3. New templates need a hosted image URL we trust — better picked by hand.

``daily_trending`` reads this file and merges its entries with Imgflip's
trending list. Captions for web templates fall through to the format-level
joke pool defined in ``daily_trending.FORMAT_JOKE_POOLS``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WEB_TEMPLATES_PATH = ROOT / "web_templates.json"


def load() -> list[dict[str, Any]]:
    """Return the current list of web-sourced templates (may be empty)."""
    if not WEB_TEMPLATES_PATH.exists():
        return []
    try:
        data = json.loads(WEB_TEMPLATES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return data  # tolerate older flat-list format
    return list(data.get("templates", []))


def save(templates: list[dict[str, Any]]) -> None:
    WEB_TEMPLATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEB_TEMPLATES_PATH.write_text(json.dumps({
        "updated_at": int(time.time()),
        "templates": templates,
    }, indent=2))


def merge_with_imgflip(
    imgflip_templates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append web templates to the Imgflip list, deduping by id."""
    seen_ids = {str(t.get("id")) for t in imgflip_templates if t.get("id")}
    merged = list(imgflip_templates)
    for t in load():
        tid = str(t.get("id") or "")
        if not tid or tid in seen_ids:
            continue
        # Normalise shape so the rest of the pipeline doesn't have to special-case.
        merged.append({
            "rank": 999,                     # always lower priority than Imgflip ranks
            "id": tid,
            "name": t.get("name") or tid,
            "url": t.get("url"),
            "width": t.get("width"),
            "height": t.get("height"),
            "box_count": t.get("box_count") or 2,
            "captions": None,
            "local_path": None,              # daily_trending will skip Imgflip render for these
            "format": t.get("format"),
            "source": t.get("source") or "web",
            "is_web": True,
        })
        seen_ids.add(tid)
    return merged


def add_template(
    *,
    template_id: str,
    name: str,
    url: str,
    fmt: str,
    box_count: int = 2,
    source: str = "web",
) -> None:
    """Convenience helper for appending a new web template."""
    existing = load()
    if any(str(t.get("id")) == str(template_id) for t in existing):
        return
    from datetime import date as _date
    existing.append({
        "id": str(template_id),
        "name": name,
        "url": url,
        "format": fmt,
        "box_count": int(box_count),
        "source": source,
        "added_on": _date.today().isoformat(),
    })
    save(existing)
