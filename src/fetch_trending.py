#!/usr/bin/env python3
"""Fetch trending meme templates from Imgflip and cache locally.

Writes:
  - trending.json  (metadata for top N templates)
  - templates/<id>.<ext>  (cached template images)

Optionally extensible to Reddit (r/memes, r/dankmemes, r/ProgrammerHumor) —
stubs included; live scraping is added in a later pass.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
TRENDING_JSON = ROOT / "trending.json"

IMGFLIP_URL = "https://api.imgflip.com/get_memes"
# Imgflip's `get_memes` returns 100 popular templates. Cache the full list so
# we have a wide pool to rotate through (see template_matcher.py / history.py).
DEFAULT_TOP_N = 100
USER_AGENT = "jarvis-meme-engine/0.1 (+https://github.com/anurag619)"
REQUEST_TIMEOUT = 20


def fetch_imgflip(top_n: int = DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """Hit Imgflip's get_memes endpoint and return the top N templates."""
    resp = requests.get(
        IMGFLIP_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError(f"Imgflip API returned failure: {payload}")
    memes = payload["data"]["memes"][:top_n]
    return memes


def download_template(meme: dict[str, Any], dest_dir: Path) -> Path | None:
    """Download a single template image into dest_dir/<id>.<ext>."""
    url = meme.get("url")
    meme_id = meme.get("id")
    if not url or not meme_id:
        return None
    ext = url.rsplit(".", 1)[-1].split("?")[0].lower()
    if ext not in {"jpg", "jpeg", "png", "webp"}:
        ext = "jpg"
    out = dest_dir / f"{meme_id}.{ext}"
    if out.exists():
        return out
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        out.write_bytes(r.content)
        return out
    except requests.RequestException as exc:
        print(f"  ! failed to download {url}: {exc}", file=sys.stderr)
        return None


def fetch_reddit_topics(_subreddits: list[str]) -> list[dict[str, Any]]:
    """Stub for Reddit trending-topic scraping. Filled in next iteration."""
    # Intentionally empty for now — kept so callers have a stable shape.
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh trending meme cache.")
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP_N,
        help=f"How many templates to cache (default {DEFAULT_TOP_N}).",
    )
    parser.add_argument(
        "--no-download", action="store_true",
        help="Only refresh metadata; skip downloading template images.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-template log lines.",
    )
    args = parser.parse_args()

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    if not args.quiet:
        print(f"[fetch_trending] hitting {IMGFLIP_URL} ...")
    try:
        memes = fetch_imgflip(top_n=args.top)
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[fetch_trending] ERROR: {exc}", file=sys.stderr)
        return 1

    cached: list[dict[str, Any]] = []
    for idx, meme in enumerate(memes, start=1):
        local_path: Path | None = None
        if not args.no_download:
            local_path = download_template(meme, TEMPLATES_DIR)
        record = {
            "rank": idx,
            "id": meme.get("id"),
            "name": meme.get("name"),
            "url": meme.get("url"),
            "width": meme.get("width"),
            "height": meme.get("height"),
            "box_count": meme.get("box_count"),
            "captions": meme.get("captions"),
            "local_path": str(local_path.relative_to(ROOT)) if local_path else None,
        }
        cached.append(record)
        if not args.quiet:
            status = "cached" if local_path else "metadata only"
            print(f"  [{idx:>2}] {meme.get('name')!r:<40} {status}")

    output = {
        "generated_at": int(time.time()),
        "source": "imgflip",
        "count": len(cached),
        "templates": cached,
        "reddit_topics": fetch_reddit_topics(
            ["memes", "dankmemes", "ProgrammerHumor"]
        ),
    }
    TRENDING_JSON.write_text(json.dumps(output, indent=2))
    if not args.quiet:
        print(f"[fetch_trending] wrote {TRENDING_JSON.relative_to(ROOT)} "
              f"({len(cached)} templates).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
