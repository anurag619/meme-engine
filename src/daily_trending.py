#!/usr/bin/env python3
"""Daily trending-memes brief.

Picks 5 memes a day for Anurag's Telegram brief, with deliberate variety:

  1. Joke-first — pick a *format* (comparison / escalation / reaction / …)
     before picking a template.
  2. Format-aware caption pool — every format has a generic joke pool so we
     can caption any template, not just the ones with bespoke pools.
  3. Template rotation — ``history.json`` blocks any template used in the
     last 7 days. The 5 picks within a single run also avoid each other.
  4. Web-sourced extras — ``web_templates.json`` extras are merged in.
  5. OpenAI wildcard — 15% of slots skip Imgflip entirely and generate a
     fully custom meme via ``meme_generator.generate_openai_image``.

Designed to run from cron daily at 03:30 UTC (09:00 IST).

## Required env (loaded from ``~/.config/jarvis/secrets.env``)

  IMGFLIP_USERNAME      — for caption_image rendering
  IMGFLIP_PASSWORD      — same
  TELEGRAM_BOT_TOKEN    — for delivery
  OPENAI_API_KEY        — optional, enables 15% wildcard slots

If credentials are missing the script logs a clear setup-needed message and
exits 0 (so cron stays quiet until you finish the one-time setup).
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import requests

from . import history, web_templates
from .template_categories import ALL_FORMATS, get_format
from .template_matcher import pick_distinct_set

ROOT = Path(__file__).resolve().parent.parent
TRENDING_JSON = ROOT / "trending.json"
TMP_TRENDING_DIR = ROOT / "tmp" / "trending"
SECRETS_PATH = Path.home() / ".config" / "jarvis" / "secrets.env"

TOP_N = 5
TELEGRAM_CHAT_ID = "5757660658"
IMGFLIP_CAPTION_URL = "https://api.imgflip.com/caption_image"

# Probability per slot of replacing the Imgflip template with a custom OpenAI
# image. Set to 0 to disable wildcards. Honoured only when OPENAI_API_KEY is set.
WILDCARD_PROBABILITY = 0.15

# Default topic mix that the daily brief riffs on. Sampled per slot so
# captions feel like a varied scroll, not five jokes about the same thing.
DAILY_TOPICS: tuple[str, ...] = (
    "AI replacing developers",
    "founders pretending to have product-market fit",
    "engineers vs LLM hallucinations",
    "Series A pitch decks vs Series A reality",
    "shipping on Friday",
    "writing tests under deadline",
    "junior devs discovering sudo",
    "vibe-coding straight to production",
    "indie hackers vs VCs",
    "standups eating the morning",
)


# --------------------------------------------------------------------------- #
# Caption pools                                                               #
# --------------------------------------------------------------------------- #
# Per-template caption pools — written for the tech / AI / startup audience.
# Keyed by Imgflip template id. These take priority over the format-level
# pool below when a template happens to have a bespoke entry.
CAPTION_POOLS: dict[str, list[tuple[str, ...]]] = {
    # Drake Hotline Bling
    "181913649": [
        ("Writing the spec", "Vibe-coding straight to prod"),
        ("Reading the docs", "Pasting errors into Claude"),
        ("Series A pitch deck", "Memes about Series A pitch decks"),
        ("Shipping a feature", "Shipping a wrapper around an LLM"),
        ("Hiring a senior engineer", "Hiring an agent that writes itself"),
    ],
    # Tuxedo Winnie The Pooh
    "178591752": [
        ("Using AI", "Leveraging agentic AI"),
        ("Founder", "Founder & CEO of a one-person AI startup"),
        ("CRUD app", "AI-native CRUD app"),
    ],
    # Two Buttons
    "87743020": [
        ("Ship the MVP", "Refactor it for six more weeks"),
        ("Hire a team", "Replace the team with Claude"),
        ("Raise a seed round", "Bootstrap with Stripe payments"),
    ],
    # Distracted Boyfriend
    "112126428": [
        ("New JS framework", "Founders", "The React app that pays the bills"),
        ("Shiny LLM benchmark", "Indie hackers", "The boring API that ships"),
    ],
    # Expanding Brain
    "93895088": [
        ("Hire developers", "Hire a co-pilot",
         "Hire an AI agent", "Become the AI agent"),
    ],
    # This Is Fine
    "55311130": [
        ("Production database", "Migrations on Friday afternoon"),
        ("Series A runway", "Burning $80k/mo on GPU credits"),
    ],
    # Disaster Girl
    "97984": [
        ("Me", "After --force pushing to main"),
        ("Junior dev", "Day three with sudo access"),
    ],
    # Change My Mind
    "129242436": [
        ("Most AI startups are GPT wrappers with extra steps", ""),
        ("Standups exist to make engineers miss flow state", ""),
    ],
    # Always Has Been
    "252600902": [
        ("Wait, it's all prompt engineering?", "Always has been"),
        ("Wait, the AI was just regex?", "Always has been"),
    ],
    # Y'all Got Any More Of That
    "124055727": [
        ("Me asking the LLM for one more retry", ""),
        ("Founder asking VCs for 'just a small bridge round'", ""),
    ],
    # One Does Not Simply
    "61579": [
        ("One does not simply", "Ship on a Friday"),
        ("One does not simply", "Containerize a legacy Rails monolith"),
    ],
    # Mocking Spongebob
    "102156234": [
        ("BuT wE'rE aN Ai-FiRsT cOmPaNy", ""),
        ("MaYbE tHe rEaL bUg WaS tHe FrIeNdS wE pUsHeD aLoNg ThE wAy", ""),
    ],
    # Ancient Aliens
    "101470": [
        ("I'm not saying it was AI", "but it was AI"),
    ],
    # Surprised Pikachu
    "155067746": [
        ("Promoted feature flag to default", "Customer churn"),
        ("Skipped writing tests for 'just one PR'", "Outage at 2am"),
    ],
    # UNO Draw 25 Cards
    "217743513": [
        ("Document your code or draw 25", ""),
        ("Reply to the investor email or draw 25", ""),
        ("Talk to an actual user or draw 25", ""),
        ("Write a real test or draw 25", ""),
    ],
    # Bernie I Am Once Again Asking For Your Support
    "222403160": [
        ("I am once again asking", "for the team to write tests"),
        ("I am once again asking", "for one demo without a Zoom freeze"),
        ("I am once again asking", "for an AI that admits it doesn't know"),
        ("I am once again asking", "for a roadmap that survives a customer call"),
    ],
    # Left Exit 12 Off Ramp
    "124822590": [
        ("Me", "Refactor properly", "Vibe-code it by Friday"),
        ("Junior dev", "Read the docs", "Paste error into Claude"),
        ("Founder", "Build the boring thing", "Pivot to AI agents"),
    ],
    # Anakin Padme 4 Panel
    "322841258": [
        ("Our AI is fully autonomous",
         "It's not just a prompt loop, right?",
         "It's not just a prompt loop, right?"),
        ("We're cash-flow positive",
         "Including the GPU bill, right?",
         "Including the GPU bill, right?"),
        ("We have product-market fit",
         "Beyond the three friends who signed up?",
         "Beyond the three friends who signed up?"),
    ],
    # Running Away Balloon
    "131087935": [
        ("Series A money", "Founder", "GPU credits", "", ""),
        ("Sleep", "Indie hacker", "Hacker News dopamine", "", ""),
        ("Engineering quality", "PM", "Ship by Friday energy", "", ""),
    ],
    # Epic Handshake
    "135256802": [
        ("Junior devs", "Senior devs", "Asking ChatGPT first"),
        ("VCs", "Founders", "Pretending to have PMF"),
        ("Designers", "Engineers", "Blaming the PM"),
        ("OpenAI", "Anthropic", "Charging per token"),
    ],
    # Gru's Plan
    "131940431": [
        ("Build an AI wrapper", "Raise on the AI wave",
         "Profit", "Profit?"),
        ("Hire engineers", "Replace them with AI agents",
         "Customers stick around", "Customers stick around?"),
        ("Launch on Product Hunt", "Get #1 of the day",
         "Sustained growth", "Sustained growth?"),
    ],
    # Sad Pablo Escobar
    "80707627": [
        ("Founder waiting for", "the first paying customer", ""),
        ("Indie hacker waiting for", "the launch tweet to go viral", ""),
        ("Me waiting for", "the LLM to stop apologising", ""),
    ],
    # Waiting Skeleton
    "4087833": [
        ("Me waiting for the LLM to stop hallucinating my codebase", ""),
        ("Me waiting for that prospect to reply to the proposal", ""),
        ("Me waiting for tests to pass on flaky CI", ""),
        ("Me waiting for the founder to write the spec", ""),
    ],
    # X, X Everywhere
    "91538330": [
        ("AI startups, AI startups everywhere", ""),
        ("LLM wrappers, LLM wrappers everywhere", ""),
        ("Founders, founders everywhere", ""),
        ("Pivots, pivots everywhere", ""),
    ],
    # Woman Yelling At Cat
    "188390779": [
        ("Why are revenue metrics so low?",
         "I haven't shipped a feature this quarter"),
        ("Why didn't you scale the cluster?",
         "There are three users"),
        ("Why is the AI hallucinating?",
         "We literally trained it on Reddit"),
    ],
    # Buff Doge vs. Cheems
    "247375501": [
        ("2010 dev", "2026 dev",
         "Reads the man pages", "Reads ChatGPT"),
        ("Bootstrapped founder", "VC-backed founder",
         "Profit", "Pre-revenue, Series C"),
        ("Senior engineer", "Vibe coder",
         "Writes a test first", "Asks Claude what tests are"),
    ],
    # Batman Slapping Robin
    "438680": [
        ("'But the LLM said'", "Did you actually check?"),
        ("'It worked locally'", "Production isn't local"),
        ("'We're an AI company'", "You're a Notion wrapper"),
        ("'The agent will handle it'", "Have you read the diff?"),
    ],
    # Trade Offer
    "309868304": [
        ("Trade offer", "I receive: equity",
         "You receive: 80-hour weeks"),
        ("Trade offer", "I receive: 'lifetime' deal money",
         "You receive: lifetime support"),
        ("Trade offer", "I receive: a Series A",
         "You receive: a 5-year exit clause"),
    ],
    # Bike Fall
    "79132341": [
        ("Me", "Force-pushes to main", "The CI is broken"),
        ("Founder", "Pivots quarterly", "PMF is just hard to find"),
        ("Me", "Approves my own PR", "QA missed the bug"),
        ("Engineer", "Ignores Slack for 4h", "Why is everyone blocked?"),
    ],
    # Is This A Pigeon
    "100777631": [
        ("Me", "An AI hype cycle", "Is this product-market fit?"),
        ("Founder", "Free-trial signup", "Is this revenue?"),
        ("Junior dev", "Stack Overflow answer", "Is this senior engineering?"),
        ("VC", "ARR slide", "Is this a real business?"),
    ],
    # They're The Same Picture
    "180190441": [
        ("Series A pitch", "Series B pitch", ""),
        ("AI startup landing page", "Another AI startup landing page", ""),
        ("Standup", "An hour of your life you'll never get back", ""),
        ("'AI agent'", "An if-statement and a prompt", ""),
    ],
}

# Format-level caption pool. Used when the chosen template doesn't have a
# bespoke entry in CAPTION_POOLS. Keep these tech / AI / startup-flavoured;
# they're applied to a wide range of templates so they should read like jokes
# rather than punchlines tied to a specific image.
FORMAT_JOKE_POOLS: dict[str, list[tuple[str, ...]]] = {
    "comparison": [
        ("Roadmap last quarter", "Roadmap after one customer call"),
        ("Engineers writing tests", "Engineers deleting tests for green CI"),
        ("'It works on my machine'", "Production"),
        ("Senior dev reviewing a PR", "Senior dev reviewing their own PR"),
        ("AI in the demo", "AI in production"),
        ("Junior dev day 1", "Junior dev day 90 with sudo"),
    ],
    "escalation": [
        ("Write the spec", "Write the code",
         "Have Claude write the code", "Have Claude review Claude's code"),
        ("Ship the MVP", "Ship the rewrite",
         "Ship the rewrite of the rewrite", "Pivot to AI agents"),
        ("Manual QA", "Automated tests",
         "Continuous deployment", "Praying"),
    ],
    "reaction": [
        ("Me reading 'minor refactor' in the PR description", ""),
        ("Me when the LLM agrees with everything I say", ""),
        ("Me watching prod logs after the Friday deploy", ""),
        ("Me realising the AI agent has root access", ""),
        ("Founder mode hitting at 2am again", ""),
    ],
    "labeling": [
        ("Me", "AI hype cycle", "VC dollars"),
        ("Founder", "Engineering", "Marketing", "Customer support"),
        ("Engineer", "Writing tests", "Writing prod code", "Writing tweets"),
        ("Investor", "ARR slide", "Burn rate slide"),
    ],
    "declaration": [
        ("Most AI startups are GPT wrappers with extra steps", ""),
        ("Tests are documentation that can't lie", ""),
        ("'Pivot' is just 'we were wrong' for VCs", ""),
        ("If your demo needs Wi-Fi, your demo will fail", ""),
    ],
    "confrontation": [
        ("Why is the AI hallucinating?",
         "We literally trained it on Reddit"),
        ("Why didn't the deploy work?", "I tested it on my laptop"),
        ("'But the LLM said'", "Did you actually check?"),
        ("'We're AI-first'", "You're a Notion wrapper"),
    ],
    "multi_panel": [
        ("Our AI is fully autonomous",
         "It's not just a prompt loop, right?",
         "It's not just a prompt loop, right?"),
        ("We have product-market fit",
         "Beyond the three friends who signed up?",
         "Beyond the three friends who signed up?"),
        ("Hire engineers", "Replace them with agents",
         "Customers stay", "Customers stay?"),
    ],
}

GENERIC_FALLBACKS: list[tuple[str, ...]] = [
    ("Standup at 9am", "Productivity at 9:01am"),
    ("Roadmap last quarter", "Roadmap after one customer call"),
    ("Devs writing tests", "Devs deleting tests to make CI green"),
    ("'It works on my machine'", "Production"),
    ("Founder mode", "Founder also doing customer support, ops, and lunch"),
]


# --------------------------------------------------------------------------- #
# Env loading                                                                 #
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


# --------------------------------------------------------------------------- #
# Trending source                                                             #
# --------------------------------------------------------------------------- #
def load_trending() -> list[dict[str, Any]]:
    if not TRENDING_JSON.exists():
        print(f"[daily_trending] {TRENDING_JSON} missing — run "
              f"src.fetch_trending first.", file=sys.stderr)
        return []
    data = json.loads(TRENDING_JSON.read_text())
    imgflip = data.get("templates", [])
    return web_templates.merge_with_imgflip(imgflip)


# --------------------------------------------------------------------------- #
# Caption picking                                                             #
# --------------------------------------------------------------------------- #
def captions_for(
    template: dict[str, Any],
    fmt: str,
    *,
    rng: random.Random | None = None,
) -> list[str]:
    """Return a list of strings to feed text0..textN for this template.

    Resolution order:
      1. Bespoke pool keyed by template id (CAPTION_POOLS).
      2. Format-level pool (FORMAT_JOKE_POOLS).
      3. Generic fallback.
    """
    rng = rng or random
    tid = str(template.get("id"))
    pool = CAPTION_POOLS.get(tid)
    if pool:
        return list(rng.choice(pool))
    pool = FORMAT_JOKE_POOLS.get(fmt)
    if pool:
        return list(rng.choice(pool))
    return list(rng.choice(GENERIC_FALLBACKS))


# --------------------------------------------------------------------------- #
# Imgflip rendering                                                           #
# --------------------------------------------------------------------------- #
def render_via_imgflip(
    template_id: str,
    captions: list[str],
    *,
    username: str,
    password: str,
) -> bytes | None:
    payload: dict[str, str] = {
        "template_id": template_id,
        "username": username,
        "password": password,
    }
    for i, c in enumerate(captions):
        payload[f"text{i}"] = c or ""
    try:
        resp = requests.post(IMGFLIP_CAPTION_URL, data=payload, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            print(f"  ! imgflip failed: {body.get('error_message')}",
                  file=sys.stderr)
            return None
        img_url = body["data"]["url"]
        img_resp = requests.get(img_url, timeout=30)
        img_resp.raise_for_status()
        return img_resp.content
    except requests.RequestException as exc:
        print(f"  ! imgflip request error: {exc}", file=sys.stderr)
        return None


def render_openai_wildcard(
    *,
    topic: str,
    captions: list[str],
) -> tuple[bytes | None, dict[str, Any]]:
    """Generate a fully custom meme via OpenAI image API + Pillow text overlay.

    Returns ``(png_bytes, pseudo_template)``. The pseudo_template carries an id
    starting with ``openai-`` so history records distinguish wildcards from
    Imgflip renders.
    """
    pseudo = {
        "id": f"openai-{int(time.time())}",
        "name": "OpenAI Wildcard",
        "source": "openai",
        "is_wildcard": True,
    }
    try:
        # Local import — keeps daily_trending importable even when Pillow isn't.
        from io import BytesIO as _BytesIO
        from .meme_generator import generate_openai_image
        from .text_overlay import draw_meme_text, resolve_font

        prompt = (
            f"A photographic meme background about {topic}. "
            "Subject centred, leaving plenty of empty space at top and bottom "
            "for white block-letter caption text. No existing text in the image. "
            "Cinematic lighting, slightly absurd, clearly a meme."
        )
        image = generate_openai_image(prompt)
        top = captions[0] if captions else ""
        bottom = captions[1] if len(captions) > 1 else ""
        rendered = draw_meme_text(
            image, top=top, bottom=bottom, font_path=resolve_font(),
        )
        buf = _BytesIO()
        rendered.save(buf, format="PNG")
        return buf.getvalue(), pseudo
    except Exception as exc:  # noqa: BLE001 — wildcard is best-effort
        print(f"  ! openai wildcard failed: {exc}", file=sys.stderr)
        return None, pseudo


# --------------------------------------------------------------------------- #
# Telegram                                                                    #
# --------------------------------------------------------------------------- #
def telegram_send_photo(image_bytes: bytes, caption: str, slug: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[daily_trending] TELEGRAM_BOT_TOKEN missing.", file=sys.stderr)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024]},
            files={"photo": (f"{slug}.png", BytesIO(image_bytes), "image/png")},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("ok", False)
    except requests.RequestException as exc:
        print(f"[daily_trending] Telegram sendPhoto failed: {exc}",
              file=sys.stderr)
        return False


def telegram_send_message(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000]},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("ok", False)
    except requests.RequestException as exc:
        print(f"[daily_trending] Telegram sendMessage failed: {exc}",
              file=sys.stderr)
        return False


# --------------------------------------------------------------------------- #
# Selection — joke-first, rotation-aware                                      #
# --------------------------------------------------------------------------- #
def choose_daily_lineup(
    templates: list[dict[str, Any]],
    *,
    count: int = TOP_N,
    topics: tuple[str, ...] = DAILY_TOPICS,
    wildcard_probability: float = WILDCARD_PROBABILITY,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Build the day's lineup.

    Each entry is a dict with keys:
      - ``topic``      — the topic this slot is riffing on
      - ``template``   — the chosen template record (or wildcard pseudo)
      - ``format``     — joke format
      - ``captions``   — list[str] for text0..textN
      - ``wildcard``   — bool; True ⇒ render via OpenAI, not Imgflip
    """
    rng = rng or random
    sampled_topics = list(topics)
    rng.shuffle(sampled_topics)
    sampled_topics = sampled_topics[:max(count, 1)]
    if len(sampled_topics) < count:
        sampled_topics += rng.choices(topics, k=count - len(sampled_topics))

    # Decide wildcard slots up front so we don't blow the template budget on them.
    wildcard_eligible = bool(os.environ.get("OPENAI_API_KEY"))
    wildcard_slots = {
        i for i in range(count)
        if wildcard_eligible and rng.random() < wildcard_probability
    }
    template_slots = count - len(wildcard_slots)

    template_picks = pick_distinct_set(
        topic=sampled_topics[0] if sampled_topics else "",
        templates=templates,
        count=template_slots,
    ) if template_slots > 0 else []

    lineup: list[dict[str, Any]] = []
    pick_iter = iter(template_picks)
    for i in range(count):
        topic = sampled_topics[i] if i < len(sampled_topics) else "general"
        if i in wildcard_slots:
            fmt = rng.choice(list(ALL_FORMATS))
            captions = list(rng.choice(
                FORMAT_JOKE_POOLS.get(fmt, GENERIC_FALLBACKS)
            ))
            lineup.append({
                "topic": topic,
                "template": {
                    "id": f"openai-{int(time.time() * 1000)}-{i}",
                    "name": "OpenAI Wildcard",
                    "is_wildcard": True,
                },
                "format": fmt,
                "captions": captions,
                "wildcard": True,
            })
            continue
        try:
            tpl, fmt = next(pick_iter)
        except StopIteration:
            break
        captions = captions_for(tpl, fmt, rng=rng)
        lineup.append({
            "topic": topic,
            "template": tpl,
            "format": fmt,
            "captions": captions,
            "wildcard": False,
        })
    return lineup


# --------------------------------------------------------------------------- #
# Meme ideas                                                                  #
# --------------------------------------------------------------------------- #
def suggest_meme_ideas(lineup: list[dict[str, Any]]) -> list[str]:
    """2-3 meme angles tied to today's chosen formats."""
    if not lineup:
        return []
    angles_by_format = {
        "comparison":    "old-school engineer vs vibe coder energy",
        "escalation":    "the four stages of pretending to do RAG",
        "reaction":      "founder watching the Stripe dashboard at 11pm",
        "labeling":      "the cast of a Series A pitch deck",
        "declaration":   "the hot take engineers won't say in standup",
        "confrontation": "PM vs engineer arguing about 'minor scope'",
        "multi_panel":   "the four-panel arc of a 'pivot to AI'",
    }
    seen: set[str] = set()
    ideas: list[str] = []
    for slot in lineup:
        fmt = slot.get("format")
        if not fmt or fmt in seen:
            continue
        seen.add(fmt)
        angle = angles_by_format.get(fmt, "the chaos of building anything in 2026")
        name = slot.get("template", {}).get("name") or fmt
        ideas.append(f"💡 {name} ({fmt}) — {angle}")
        if len(ideas) >= 3:
            break
    return ideas


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #
def main() -> int:
    _load_secrets_env()

    user = os.environ.get("IMGFLIP_USERNAME")
    pwd = os.environ.get("IMGFLIP_PASSWORD")
    if not (user and pwd):
        print("[daily_trending] IMGFLIP_USERNAME/PASSWORD not set; "
              "add to ~/.config/jarvis/secrets.env. Skipping run.")
        return 0
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("[daily_trending] TELEGRAM_BOT_TOKEN not set; skipping run.")
        return 0

    templates = load_trending()
    if not templates:
        return 1

    lineup = choose_daily_lineup(templates, count=TOP_N)
    print(f"[daily_trending] picked {len(lineup)} memes from "
          f"{len(templates)} templates ...")
    for i, slot in enumerate(lineup, 1):
        tpl = slot["template"]
        flag = "🪄 wildcard" if slot["wildcard"] else f"id={tpl['id']}"
        print(f"  [{i}] fmt={slot['format']:<13} {flag:<25} "
              f"{tpl.get('name'):<35} topic={slot['topic']!r}")

    telegram_send_message(
        f"🔥 Today's {len(lineup)} memes, sir — joke-first, "
        f"7-day rotation, {int(WILDCARD_PROBABILITY*100)}% wildcards."
    )

    TMP_TRENDING_DIR.mkdir(parents=True, exist_ok=True)
    sent = 0
    # Track which template ids we've already used in this run so the wildcard
    # fallback doesn't accidentally pick a duplicate.
    used_ids_in_run: set[str] = {
        str(s["template"].get("id"))
        for s in lineup if not s["wildcard"]
    }
    # Once OpenAI fails (e.g. billing hard limit), skip remaining wildcards
    # for the rest of the run — no point hammering a 400 endpoint.
    wildcard_disabled_for_run = False

    for i, slot in enumerate(lineup, 1):
        tpl = slot["template"]
        captions = slot["captions"]
        fmt = slot["format"]
        topic = slot["topic"]

        png_bytes: bytes | None = None
        if slot["wildcard"] and not wildcard_disabled_for_run:
            png_bytes, _ = render_openai_wildcard(topic=topic, captions=captions)
            if png_bytes is None:
                wildcard_disabled_for_run = True

        if png_bytes is None and slot["wildcard"]:
            # Wildcard slot failed (or was pre-disabled) — fall back to an
            # Imgflip template so today's lineup stays at TOP_N memes.
            fallback_picks = pick_distinct_set(
                topic=topic, templates=templates, count=1,
            )
            # Manually exclude ids we've already used this run.
            fallback_picks = [
                p for p in fallback_picks
                if str(p[0].get("id")) not in used_ids_in_run
            ]
            if not fallback_picks:
                # Brute-force: pick any unused trending template.
                for cand in templates:
                    if str(cand.get("id")) not in used_ids_in_run:
                        fallback_picks = [(cand, get_format(cand))]
                        break
            if fallback_picks:
                tpl, fmt = fallback_picks[0]
                captions = captions_for(tpl, fmt)
                slot["template"] = tpl
                slot["format"] = fmt
                slot["captions"] = captions
                slot["wildcard"] = False
                print(f"  ↩ slot #{i} fell back to Imgflip "
                      f"(id={tpl.get('id')} {tpl.get('name')!r})")
                png_bytes = render_via_imgflip(
                    template_id=str(tpl["id"]),
                    captions=captions,
                    username=user,
                    password=pwd,
                )

        if png_bytes is None and not slot["wildcard"]:
            png_bytes = render_via_imgflip(
                template_id=str(tpl["id"]),
                captions=captions,
                username=user,
                password=pwd,
            )

        if not png_bytes:
            continue
        used_ids_in_run.add(str(tpl.get("id")))

        slug = re.sub(r"[^a-zA-Z0-9]+", "-",
                      (tpl.get("name") or "meme").lower()).strip("-")[:40]
        out_path = TMP_TRENDING_DIR / f"{int(time.time())}-{i:02d}-{slug}.png"
        out_path.write_bytes(png_bytes)

        history.record_use(
            template_id=str(tpl.get("id")),
            fmt=fmt,
            topic=topic,
            source="openai" if slot["wildcard"] else (
                tpl.get("source") or "imgflip"
            ),
        )

        wildcard_tag = " 🪄" if slot["wildcard"] else ""
        caption = (
            f"#{i} {tpl.get('name')}{wildcard_tag} — {fmt}\n"
            f"Topic: {topic}\n"
            f"Caption: {captions[0]!r}"
            + (f" / {captions[1]!r}" if len(captions) > 1 and captions[1] else "")
        )
        if telegram_send_photo(png_bytes, caption, slug):
            sent += 1

    ideas = suggest_meme_ideas(lineup)
    if ideas:
        telegram_send_message(
            "Meme angles to ride today's wave, sir:\n" + "\n".join(ideas)
        )

    print(f"[daily_trending] sent {sent} memes + idea brief.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
