#!/usr/bin/env python3
"""Fetch trending tech headlines and distil them into meme topic phrases.

The static ``DAILY_TOPICS`` list in ``daily_trending.py`` keeps the brand
voice stable — but on its own it goes stale. This module pulls *this
week's* tech chatter and asks gpt-4o-mini to distil it into 10-15 meme
topic phrases, written to ``topics.json``.

The daily brief merges the fresh phrases with the static pool, so the
brief stays current without abandoning the evergreen jokes.

Sources
-------
- **Hacker News front page** via the Algolia HN API
  (``hn.algolia.com/api/v1/search?tags=front_page``). Free, no auth,
  single request, IP-friendly. Returns the current top 20-50 stories with
  titles + URLs.

Reddit is not used (IP-blocked from this host — see CLAUDE.md). Twitter
is paywalled. HN is the only realistic free source for this audience.

Output
------
``topics.json``::

    {
      "generated_at": <unix timestamp>,
      "source": "hn",
      "headline_count": <int>,
      "topics": [<phrases>]
    }

Failure mode
------------
Any failure (HN unreachable, LLM down, bad JSON) returns a non-zero exit
and leaves ``topics.json`` untouched. The daily brief falls back to
``DAILY_TOPICS`` silently — cron stays quiet.

Manual run
----------
::

    ~/jarvis-workspace/meme-engine/.venv/bin/python \
      -m src.fetch_topics --quiet
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

from . import llm_captions

ROOT = Path(__file__).resolve().parent.parent
TOPICS_JSON = ROOT / "topics.json"
SECRETS_PATH = Path.home() / ".config" / "jarvis" / "secrets.env"

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search"
HN_HEADERS = {"User-Agent": "jarvis-meme-engine/0.1"}
DEFAULT_HEADLINE_COUNT = 40
DEFAULT_TOPIC_COUNT = 12

# Words/phrases that scream "not meme-able for this audience" — used to
# pre-filter HN headlines before sending to the LLM.
HEADLINE_BLOCKLIST: tuple[str, ...] = (
    "ask hn:", "show hn:", "tell hn:",
    "obituary", "in memoriam", "rip ",
    "ukraine", "gaza", "israel", "palestine", "russia",
)


def _load_secrets_env(path: Path = SECRETS_PATH) -> None:
    """Mirror the loader pattern used in daily_trending / meme_generator."""
    if not path.exists():
        return
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(
                key.strip(), val.strip().strip("'").strip('"')
            )
    except OSError:
        pass


def fetch_hn_headlines(
    count: int = DEFAULT_HEADLINE_COUNT,
    *,
    timeout: int = 20,
) -> list[str]:
    """Fetch the top ``count`` HN front-page headlines via Algolia.

    Returns an empty list on any failure — caller decides whether that's
    a hard error or "skip refresh and keep the existing topics.json".
    """
    try:
        resp = requests.get(
            HN_ALGOLIA_URL,
            params={
                "tags": "front_page",
                "hitsPerPage": min(max(count, 1), 100),
            },
            headers=HN_HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits") or []
    except (requests.RequestException, ValueError) as exc:
        print(f"[fetch_topics] HN fetch failed: {exc}", file=sys.stderr)
        return []
    titles: list[str] = []
    for h in hits:
        title = (h.get("title") or "").strip()
        if not title:
            continue
        lower = title.lower()
        if any(b in lower for b in HEADLINE_BLOCKLIST):
            continue
        titles.append(title)
    return titles


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh topics.json from current HN front page."
    )
    parser.add_argument(
        "--count", type=int, default=DEFAULT_TOPIC_COUNT,
        help=f"Number of topic phrases to produce (default {DEFAULT_TOPIC_COUNT}).",
    )
    parser.add_argument(
        "--headlines", type=int, default=DEFAULT_HEADLINE_COUNT,
        help=f"Number of HN headlines to feed the model "
             f"(default {DEFAULT_HEADLINE_COUNT}).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress informational output (use for cron).",
    )
    args = parser.parse_args()

    _load_secrets_env()

    if not llm_captions.is_enabled():
        print("[fetch_topics] OPENAI_API_KEY not set; skipping topic refresh. "
              "Daily brief will use static DAILY_TOPICS.")
        return 0

    headlines = fetch_hn_headlines(args.headlines)
    if not headlines:
        print("[fetch_topics] No HN headlines available; leaving topics.json "
              "untouched.", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"[fetch_topics] fetched {len(headlines)} HN headlines; "
              f"distilling into {args.count} topic phrases ...")

    topics = llm_captions.distil_topics(headlines, count=args.count)
    if not topics:
        print("[fetch_topics] LLM distillation failed; leaving topics.json "
              "untouched.", file=sys.stderr)
        return 1

    payload = {
        "generated_at": int(time.time()),
        "source": "hn",
        "headline_count": len(headlines),
        "topic_count": len(topics),
        "topics": topics,
    }
    TOPICS_JSON.write_text(json.dumps(payload, indent=2))
    if not args.quiet:
        print(f"[fetch_topics] wrote {TOPICS_JSON} with {len(topics)} topics:")
        for t in topics:
            print(f"  • {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
