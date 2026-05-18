#!/usr/bin/env python3
"""Fetch trending tech + world topics, distil into meme topic phrases.

The static ``DAILY_TOPICS`` list in ``daily_trending.py`` keeps the brand
voice stable. This module pulls *this week's* chatter from multiple
sources and distils each via gpt-4o-mini into 6-12 meme topic phrases,
written to ``topics.json``.

The daily brief merges the fresh phrases with the static pool, so the
brief stays current without abandoning the evergreen jokes.

Sources
-------
- **Hacker News front page** via Algolia
  (``hn.algolia.com/api/v1/search?tags=front_page``). Tech / engineering
  chatter. Free, no auth, single request.
- **Wikipedia Current Events Portal** (the curated daily summary on
  ``en.wikipedia.org/wiki/Portal:Current_events``). World news, already
  neutrally phrased. Free.
- **OpenAI web search** (Responses API + ``web_search_preview`` tool).
  One synthesised "absurd / culturally-significant" world digest per
  refresh. ~$0.025/call.

Reddit is not used (IP-blocked from this host — see CLAUDE.md).
Twitter is paywalled (see Anurag's call note from this week).

Tone
----
World items pass through a stricter ``WORLD_BLOCKLIST`` pre-filter and a
tone-guarded LLM prompt (``llm_captions.distil_world_topics``) that skips
wars, casualties, named tragedies, and anything that only memes by
punching down. The output is biased toward absurd cultural moments,
corporate clown shows, and tech-policy collisions.

Output
------
``topics.json``::

    {
      "generated_at": <unix timestamp>,
      "sources":       [<source names that contributed>],
      "by_source":     {<source>: [<phrases>]},
      "topic_count":   <int>,
      "topics":        [<phrases>]   # flattened, deduped, fresh-first
    }

The ``topics`` key remains the single source of truth for
``daily_trending.load_topics()`` — backward compatible with the v1
schema.

Failure mode
------------
Any single source failing is logged and skipped — the others still write
their share. If *all* sources fail, the script exits 1 and leaves
``topics.json`` untouched. The daily brief falls back to ``DAILY_TOPICS``.

Manual run
----------
::

    # full refresh
    .venv/bin/python -m src.fetch_topics

    # only tech (skip world sources)
    .venv/bin/python -m src.fetch_topics --skip-world

    # debug — print what each source returned
    .venv/bin/python -m src.fetch_topics --debug
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import requests

from . import llm_captions

ROOT = Path(__file__).resolve().parent.parent
TOPICS_JSON = ROOT / "topics.json"
SECRETS_PATH = Path.home() / ".config" / "jarvis" / "secrets.env"

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search"
HN_HEADERS = {"User-Agent": "jarvis-meme-engine/0.1"}
WIKI_CURRENT_EVENTS_URL = (
    "https://en.wikipedia.org/w/api.php?action=parse&page=Portal:Current_events"
    "&format=json&prop=text"
)
DEFAULT_HN_COUNT = 40
DEFAULT_HN_TOPICS = 12
DEFAULT_WIKI_TOPICS = 8
DEFAULT_WEBSEARCH_TOPICS = 6

# Words/phrases that scream "not meme-able for this audience" — used to
# pre-filter HN headlines before sending to the LLM.
HEADLINE_BLOCKLIST: tuple[str, ...] = (
    "ask hn:", "show hn:", "tell hn:",
    "obituary", "in memoriam", "rip ",
    "ukraine", "gaza", "israel", "palestine", "russia",
)

# Stricter blocklist for world-news items (Wikipedia / web search). The LLM
# applies tone guardrails too, but cheap pre-filtering keeps the prompt focused.
WORLD_BLOCKLIST: tuple[str, ...] = (
    # Active armed conflicts and named war zones
    "ukraine", "gaza", "israel", "palestine", "russia", "syria", "yemen",
    "sudan", "myanmar", "ethiopia",
    # Casualty-coded words
    "killed", "killing", "casualties", "dead", "deaths", "fatalities",
    "shooting", "shootings", "shot dead", "massacre", "bomb", "bombing",
    "airstrike", "missile strike", "drone strike", "attack on",
    # Named tragedies / suffering
    "hostage", "kidnap", "refugee", "famine", "genocide", "ethnic cleansing",
    # Major individual disasters
    "plane crash", "earthquake kills", "tsunami", "flood kills",
)

WEB_SEARCH_QUERY = (
    "Summarise the most culturally significant, absurd, or "
    "tech-policy-relevant world events from the past week. Skip stories "
    "about wars, casualties, or human tragedies — focus instead on "
    "corporate clown shows, AI-in-courts / AI-in-elections, billionaire "
    "shenanigans, regulator chaos, viral cultural moments, climate-tech "
    "drama, and Big Tech antitrust news. Give 8-12 short headline-style "
    "items, one per line, no commentary."
)


# --------------------------------------------------------------------------- #
# Env loader (mirrors other modules)                                          #
# --------------------------------------------------------------------------- #
def _load_secrets_env(path: Path = SECRETS_PATH) -> None:
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


def _passes_blocklist(text: str, blocklist: tuple[str, ...]) -> bool:
    """True if ``text`` doesn't contain any blocklist term (case-insensitive)."""
    low = text.lower()
    return not any(b in low for b in blocklist)


# --------------------------------------------------------------------------- #
# Source 1 — Hacker News (tech)                                               #
# --------------------------------------------------------------------------- #
def fetch_hn_headlines(
    count: int = DEFAULT_HN_COUNT,
    *,
    timeout: int = 20,
) -> list[str]:
    """Fetch the top ``count`` HN front-page headlines via Algolia."""
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
        if title and _passes_blocklist(title, HEADLINE_BLOCKLIST):
            titles.append(title)
    return titles


# --------------------------------------------------------------------------- #
# Source 2 — Wikipedia Current Events Portal (world)                          #
# --------------------------------------------------------------------------- #
class _WikiEventExtractor(HTMLParser):
    """Strip HTML and collect plain-text bullet lines from the portal page.

    The portal HTML is structured as nested <ul>/<li> trees under category
    headings. We don't bother with category context — every leaf <li>'s
    text becomes one candidate event item. The LLM downstream sorts the
    noise from the signal.
    """
    def __init__(self) -> None:
        super().__init__()
        self.in_li = 0
        self.buf: list[str] = []
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "li":
            if self.in_li:
                # Flush parent before recursing; treat nested as separate items.
                self._flush()
            self.in_li += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self.in_li:
            self._flush()
            self.in_li -= 1

    def handle_data(self, data: str) -> None:
        if self.in_li:
            self.buf.append(data)

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        # Drop noise: footnote markers, single-char fragments, citation tails.
        text = re.sub(r"\[\d+\]", "", text)
        text = re.sub(r"\(AP\)|\(Reuters\)|\(BBC\)", "", text).strip()
        if len(text) >= 25:
            self.items.append(text)
        self.buf = []


def fetch_wikipedia_events(
    *,
    timeout: int = 20,
    max_items: int = 60,
) -> list[str]:
    """Fetch and parse the Wikipedia Current Events Portal.

    Returns a list of plain-text event lines that passed ``WORLD_BLOCKLIST``.
    Empty list on any failure.
    """
    try:
        resp = requests.get(
            WIKI_CURRENT_EVENTS_URL,
            headers={"User-Agent": HN_HEADERS["User-Agent"]},
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        html = (
            body.get("parse", {})
                .get("text", {})
                .get("*", "")
        )
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"[fetch_topics] Wikipedia fetch failed: {exc}", file=sys.stderr)
        return []
    if not html:
        return []
    parser = _WikiEventExtractor()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001 — bad HTML shouldn't kill the run
        print(f"[fetch_topics] Wikipedia parse failed: {exc}", file=sys.stderr)
        return []
    items: list[str] = []
    for text in parser.items:
        if not _passes_blocklist(text, WORLD_BLOCKLIST):
            continue
        # Drop boilerplate / category headers that slipped through
        low = text.lower()
        if low.startswith(("see also", "main article", "ongoing", "elections")):
            continue
        items.append(text)
        if len(items) >= max_items:
            break
    return items


# --------------------------------------------------------------------------- #
# Source 3 — OpenAI web search (world)                                        #
# --------------------------------------------------------------------------- #
def fetch_openai_world_digest() -> list[str]:
    """One synthesised digest of recent culture/business stories.

    Uses OpenAI's web search via ``llm_captions.openai_web_search``. Returns
    a list of headline-style lines. Empty list on failure.
    """
    raw = llm_captions.openai_web_search(WEB_SEARCH_QUERY)
    if not raw:
        return []
    # The model is asked for one item per line. Strip bullets/numbering/etc.
    items: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading markdown bullets, numbers, dashes.
        line = re.sub(r"^[\-\*•‣◦]\s*", "", line)
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        line = line.strip("•- ").strip()
        if len(line) < 15 or len(line) > 300:
            continue
        if not _passes_blocklist(line, WORLD_BLOCKLIST):
            continue
        items.append(line)
    return items[:30]


# --------------------------------------------------------------------------- #
# Output writer                                                                #
# --------------------------------------------------------------------------- #
def _merge_dedupe(*pools: list[str]) -> list[str]:
    """Merge multiple ordered pools into one, case-insensitive dedupe."""
    seen: set[str] = set()
    merged: list[str] = []
    for pool in pools:
        for t in pool:
            key = t.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(t)
    return merged


# --------------------------------------------------------------------------- #
# Orchestration                                                                #
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh topics.json from HN + Wikipedia + OpenAI web search."
    )
    parser.add_argument(
        "--hn-count", type=int, default=DEFAULT_HN_COUNT,
        help=f"HN headlines to feed the model (default {DEFAULT_HN_COUNT}).",
    )
    parser.add_argument(
        "--hn-topics", type=int, default=DEFAULT_HN_TOPICS,
        help=f"HN-derived topics to produce (default {DEFAULT_HN_TOPICS}).",
    )
    parser.add_argument(
        "--wiki-topics", type=int, default=DEFAULT_WIKI_TOPICS,
        help=f"Wikipedia-derived topics (default {DEFAULT_WIKI_TOPICS}).",
    )
    parser.add_argument(
        "--websearch-topics", type=int, default=DEFAULT_WEBSEARCH_TOPICS,
        help=f"Web-search-derived topics (default {DEFAULT_WEBSEARCH_TOPICS}).",
    )
    parser.add_argument(
        "--skip-world", action="store_true",
        help="Skip Wikipedia + web search; HN tech-only refresh.",
    )
    parser.add_argument(
        "--skip-websearch", action="store_true",
        help="Skip the OpenAI web search call (saves ~$0.025).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress informational output (use for cron).",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Print raw items from each source before distillation.",
    )
    args = parser.parse_args()

    _load_secrets_env()

    if not llm_captions.is_enabled():
        print("[fetch_topics] OPENAI_API_KEY not set; skipping topic refresh. "
              "Daily brief will use static DAILY_TOPICS.")
        return 0

    by_source: dict[str, list[str]] = {}

    # --- HN (tech) ----------------------------------------------------------
    if not args.quiet:
        print(f"[fetch_topics] fetching HN front page ...")
    hn_headlines = fetch_hn_headlines(args.hn_count)
    if args.debug:
        for h in hn_headlines[:10]:
            print(f"  hn-raw> {h}")
    if hn_headlines:
        hn_topics = llm_captions.distil_topics(
            hn_headlines, count=args.hn_topics,
        )
        if hn_topics:
            by_source["hn"] = hn_topics
            if not args.quiet:
                print(f"  ✓ hn         → {len(hn_topics)} topics")
        else:
            print("  ! hn distillation failed", file=sys.stderr)
    else:
        print("  ! hn returned no usable headlines", file=sys.stderr)

    # --- Wikipedia (world) --------------------------------------------------
    if not args.skip_world:
        if not args.quiet:
            print(f"[fetch_topics] fetching Wikipedia Current Events ...")
        wiki_items = fetch_wikipedia_events()
        if args.debug:
            for w in wiki_items[:10]:
                print(f"  wiki-raw> {w[:100]}")
        if wiki_items:
            wiki_topics = llm_captions.distil_world_topics(
                wiki_items, count=args.wiki_topics,
            )
            if wiki_topics:
                by_source["wikipedia"] = wiki_topics
                if not args.quiet:
                    print(f"  ✓ wikipedia  → {len(wiki_topics)} topics")
            else:
                print("  ! wikipedia distillation produced nothing meme-able",
                      file=sys.stderr)
        else:
            print("  ! wikipedia returned no usable items", file=sys.stderr)

    # --- OpenAI web search (world) -----------------------------------------
    if not args.skip_world and not args.skip_websearch:
        if not args.quiet:
            print(f"[fetch_topics] running OpenAI web search digest ...")
        web_items = fetch_openai_world_digest()
        if args.debug:
            for w in web_items[:10]:
                print(f"  web-raw> {w}")
        if web_items:
            web_topics = llm_captions.distil_world_topics(
                web_items, count=args.websearch_topics,
            )
            if web_topics:
                by_source["websearch"] = web_topics
                if not args.quiet:
                    print(f"  ✓ websearch  → {len(web_topics)} topics")
            else:
                print("  ! websearch distillation produced nothing meme-able",
                      file=sys.stderr)
        else:
            print("  ! websearch returned no usable items", file=sys.stderr)

    if not by_source:
        print("[fetch_topics] every source failed; leaving topics.json "
              "untouched.", file=sys.stderr)
        return 1

    # Flatten in deterministic order: HN first (tech bias is the brand),
    # then world sources. Within-source order preserved.
    flat = _merge_dedupe(
        by_source.get("hn", []),
        by_source.get("wikipedia", []),
        by_source.get("websearch", []),
    )

    payload = {
        "generated_at": int(time.time()),
        "sources": list(by_source.keys()),
        "by_source": by_source,
        "topic_count": len(flat),
        "topics": flat,
    }
    TOPICS_JSON.write_text(json.dumps(payload, indent=2))

    if not args.quiet:
        print(f"\n[fetch_topics] wrote {TOPICS_JSON} with {len(flat)} topics "
              f"from {len(by_source)} source(s):")
        for src, items in by_source.items():
            print(f"  [{src}]")
            for t in items:
                print(f"    • {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
