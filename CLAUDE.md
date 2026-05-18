# meme-engine

Trending meme generator. Imgflip + Pillow today; Reddit scraping next.

## Layout

```
meme-engine/
  CLAUDE.md
  pyproject.toml
  requirements.txt
  src/
    fetch_trending.py     # refreshes trending.json + templates/ (top 100)
    meme_generator.py     # CLI entry point
    text_overlay.py       # Pillow Impact-style text drawing
    daily_trending.py     # joke-first daily brief (5 memes/day)
    template_categories.py# format taxonomy (comparison/escalation/...)
    template_matcher.py   # joke-first selection + 7-day rotation
    history.py            # usage log (history.json) + cooldown
    web_templates.py      # extra templates from outside Imgflip
    llm_captions.py       # gpt-4o-mini captions + template selection (with fallbacks)
  templates/              # cached template images (gitignored)
  fonts/                  # Anton-Regular.ttf (free Impact substitute)
  trending.json           # refreshed daily (gitignored, 100 templates)
  history.json            # usage log for 7-day rotation (gitignored)
  web_templates.json      # web-sourced extras (gitignored)
  tests/                  # local-only test scripts (gitignored)
  tmp/                    # generated memes (gitignored)
```

## Setup

`uv` is the package manager — pip is not installed.

```bash
cd ~/jarvis-workspace/meme-engine
uv venv .venv
uv pip install -r requirements.txt
```

The font is committed in `fonts/Anton-Regular.ttf` (OFL, Google Fonts). It's
visually indistinguishable from Impact at typical meme sizes. If a real
`Impact.ttf` is dropped into `fonts/`, the overlay code will prefer it.

## Usage

Always run via the venv: `~/jarvis-workspace/meme-engine/.venv/bin/python -m src.meme_generator …`

```bash
# Refresh trending cache (also runs daily via cron)
python -m src.fetch_trending

# Top 10 trending templates
python -m src.meme_generator --list 10

# Render with a fuzzy template name
python -m src.meme_generator \
  --template "drake" \
  --top "writing tests" \
  --bottom "shipping on friday"

# Surprise me — picks a template based on the topic
python -m src.meme_generator --surprise --topic "monday standups" \
  --top "me at 9am" --bottom "me at 9:01am"

# Resize for Instagram
python -m src.meme_generator --template "two buttons" \
  --top "merge to main" --bottom "wait for review" \
  --platform instagram_square

# Custom AI image (requires OPENAI_API_KEY)
python -m src.meme_generator --openai \
  --prompt "a cat coding python at 3am, vaporwave palette" \
  --top "git push --force" --bottom "what could go wrong"
```

Output goes to `tmp/<unix-ts>-<slug>.png` unless `--output` is given.

## Telegram triggers (Jarvis behaviour)

When Anurag says any of these in Telegram, run the meme engine and send the PNG
back via `sendPhoto` to chat `5757660658`:

| Trigger phrase | Action |
| --- | --- |
| `meme about <topic>` | `--surprise --topic "<topic>"` + Jarvis writes `--top` and `--bottom` (be actually funny — sharp, observational, never corporate). |
| `trending memes` | `--list 5` and reply with the formatted list as text. |
| `surprise me` | `--surprise --topic "general"` + Jarvis writes captions. |
| `meme for <platform>` | Same as above plus `--platform <…>`. Platforms: `instagram_square`, `instagram_story`, `instagram_portrait`, `twitter`, `facebook`, `tiktok`, `reddit`. |
| `meme: <topic> // <top> // <bottom>` | Manual caption override. |

Caption rules — Jarvis writes these, not the script:

- Punch up at situations, never at people.
- Specific > generic. "Wednesday standup at 9:01am" beats "long meetings".
- Setup ↔ payoff structure. Top is the premise, bottom is the twist.
- ALL CAPS is handled automatically — don't pre-uppercase.
- Cap each line at ~6 words; the overlay will wrap longer lines but it looks worse.

## Cron

Two daily jobs, both UTC:

```
# 03:25 UTC — refresh the Imgflip trending cache
25 3 * * * /home/jarvis/jarvis-workspace/meme-engine/.venv/bin/python \
  /home/jarvis/jarvis-workspace/meme-engine/src/fetch_trending.py \
  --quiet >> /home/jarvis/jarvis-workspace/logs/meme-engine-cron.log 2>&1

# 03:30 UTC (= 09:00 IST) — send the daily trending memes brief to Telegram
30 3 * * * /home/jarvis/jarvis-workspace/meme-engine/.venv/bin/python \
  /home/jarvis/jarvis-workspace/meme-engine/src/daily_trending.py \
  >> /home/jarvis/jarvis-workspace/logs/meme-trending-cron.log 2>&1
```

Order matters: the refresh runs 5 minutes before the brief so the brief always
uses fresh data. Logs land in `~/jarvis-workspace/logs/meme-engine-cron.log`
and `meme-trending-cron.log` respectively.

## daily_trending.py

The morning brief. Joke-first selection with template rotation:

1. **Pick a topic** per slot from `DAILY_TOPICS`.
2. **Pick a joke format** for that topic (`template_categories.suggest_format`
   — keyword heuristic; falls back to "least-recently-used format with a
   non-empty bucket").
3. **Pick a template in that format** that hasn't been used in the last 7
   days (`template_matcher.pick_distinct_set` + `history.recently_used_ids`).
4. **Pick captions** — preferred path is **gpt-4o-mini** via
   `llm_captions.generate_batch_captions` (one batch call per brief, see the
   LLM section below). Static fallback chain on any failure: bespoke pool
   keyed by template id (`CAPTION_POOLS`), then format-level
   (`FORMAT_JOKE_POOLS`), then `GENERIC_FALLBACKS`.
5. **15% wildcard slots** — when `OPENAI_API_KEY` is set, ~15% of slots skip
   the template entirely and generate a custom meme via OpenAI image gen +
   local Pillow text overlay.
6. **Record use** — every render appends to `history.json` so tomorrow's
   brief avoids today's templates.

Reads from `trending.json` (top 100 Imgflip templates) merged with
`web_templates.json` (manually curated extras — see `web_templates.py`).

Renders via the Imgflip `caption_image` API and sends to Telegram chat
`5757660658`. Closes with 2–3 meme angles tied to today's chosen formats.

**Why Imgflip-only as the source:** Reddit is hard-blocked from this
DigitalOcean IP at every layer — direct curl, every public mirror (libreddit,
redlib, redditgw), RSSHub, Anthropic's WebFetch, and Anthropic's WebSearch
(reddit.com is on their blocklist). The only viable Reddit access from this
host would be the official OAuth API, which we deliberately skipped to avoid
shipping Reddit credentials. Imgflip's trending list is the canonical source
of "what meme formats are hot" anyway, and we already cache it.

**Required env** (in `~/.config/jarvis/secrets.env`):

- `IMGFLIP_USERNAME`, `IMGFLIP_PASSWORD` — for caption_image rendering.
- `TELEGRAM_BOT_TOKEN` — for delivery.

If anything's missing the script logs a clear setup-needed line and exits 0,
keeping cron silent.

**Manual run:**
```
~/jarvis-workspace/meme-engine/.venv/bin/python \
  -m src.daily_trending
```

**Extending the caption pool:** add an entry to `CAPTION_POOLS` keyed by
Imgflip template id. Each entry is a list of caption tuples; one is picked
at random per run. Aim for sharp, observational, tech-audience-specific
jokes — not corporate humour.

## LLM-powered captions & template selection (`src/llm_captions.py`)

When `OPENAI_API_KEY` is set, the engine uses **gpt-4o-mini** to:

1. **Generate captions** that fit the chosen template's joke format (passed
   through few-shot examples per format + an 8-word cap per line).
2. **Pick the best template** out of the post-cooldown, post-dedup eligible
   pool — same rotation guardrails still apply, the LLM only judges fit.

Architecture is **upgrade, not replace**:

- Every call returns `None` (or empty list) on any failure — missing key,
  timeout, bad JSON, wrong caption count. The existing static `CAPTION_POOLS`
  / `FORMAT_JOKE_POOLS` / `GENERIC_FALLBACKS` and `random.choice` matcher are
  the safety net.
- Daily cron without `OPENAI_API_KEY` keeps working exactly as before — the
  module short-circuits silently.
- The `openai` SDK is a soft import; if it's not installed the rest of the
  engine still runs.

### Where the LLM is wired in

| Code path | Function | LLM call |
|---|---|---|
| `daily_trending.choose_daily_lineup` | `_apply_batch_llm_captions` | **One** batch call for all 5 slots (cheaper, lets the model vary the angle across the brief). Per-slot retry for slots the batch missed. |
| `daily_trending.captions_for` | `llm_captions.generate_captions` | Per-template fallback when batch isn't used (and direct callers like the wildcard fallback path). |
| `template_matcher.pick_template` | `pick_template_via_llm` → `select_template` | After cooldown / format / dedup filtering, the LLM judges the *fresh* bucket. Falls through to `random.choice` on failure. |
| `template_matcher.pick_distinct_set` | Same as above, per slot in the loop. |
| `meme_generator.main` (`--auto-caption`) | `llm_captions.generate_captions` | When `--topic` is supplied, tries LLM before the basic `auto_caption()` joke template. |

### Tuning knobs (in `src/llm_captions.py`)

- `MODEL = "gpt-4o-mini"` — cheap, fast, good enough for meme captions.
- `CAPTION_TEMPERATURE = 0.9` — we want jokes, not safety.
- `SELECTION_TEMPERATURE = 0.4` — we want sound judgement.
- `REQUEST_TIMEOUT_SECONDS = 30`.
- `FORMAT_GUIDANCE` — per-format prompt guidance (comparison ↔ contrast, escalation ↔ ascending absurdity, etc.).
- `FEW_SHOT_EXAMPLES` — 1-2 examples per format pulled from the existing `CAPTION_POOLS`.

### Env requirements

- `OPENAI_API_KEY` in `~/.config/jarvis/secrets.env` — **optional**. If
  missing, the engine falls back to static pools and the heuristic matcher
  silently. No log noise, no crash.

### Cost ballpark

Daily brief (5 memes) = 1 batch caption call + ~5 template-selection calls
+ optional per-slot retries. Total under **$0.005/day** on gpt-4o-mini.

## Variety / rotation rules

The matcher exists because Imgflip trending barely moves and the old code
kept landing on the same 3 templates. Rules:

1. **100-template pool** — `fetch_trending.py` pulls all 100 from
   `get_memes`, not the top 3-5.
2. **7-day cooldown** — `history.json` blocks any template id used within
   the past 7 days. Inside a single run, the 5 picks also dedupe each other.
3. **Joke-first** — caption pool is chosen *before* the template:
   `topic → format → caption → template`, not the reverse.
4. **Format diversity within a batch** — `pick_distinct_set` prefers formats
   not yet seen in the current batch, falling back to repeats only once all
   non-empty formats have been covered.
5. **Web extras** — drop new viral formats into `web_templates.json` (id,
   name, url, format, box_count). Jarvis runs a weekly WebSearch ("new meme
   templates 2026" / "viral meme formats this month") and curates them by
   hand — Python doesn't fetch them itself.
6. **OpenAI wildcards** — 15% of slots, when `OPENAI_API_KEY` is set, skip
   the template entirely. Tunable via `WILDCARD_PROBABILITY` in
   `daily_trending.py`.

### Variety test

`tests/variety_test.py` proves the matcher works:

```bash
cd ~/jarvis-workspace/meme-engine
.venv/bin/python tests/variety_test.py
```

Asserts: 5 memes about the same topic land on 5 distinct templates spanning
≥3 format categories, and the 7-day cooldown blocks reuse on the next run.

## Roadmap

- [ ] Reddit trending-topic scraper (`fetch_reddit_topics`) — currently a stub.
- [x] LLM-generated captions as a default rather than auto-caption fallback. (gpt-4o-mini via `src/llm_captions.py`, with static pools as the safety net.)
- [ ] Weekly auto-refresh of `web_templates.json` via Jarvis WebSearch.

## Template hygiene rules

**Always verify template images don't ship with built-in text areas (white
sidebars, captioner-friendly margins, watermarks).** Imgflip serves several
templates as "split-layout" variants designed for their own captioner — Drake
Hotline Bling, Tuxedo Pooh, and Expanding Brain are the worst offenders.
Before adding a template's regions, open the cached file in `templates/` and
look for:

- Large blocks of pure white that aren't part of the actual photo.
- Pre-rendered example text or watermark overlays.
- Aspect ratios that look distorted (suggests sidebar padding).

If a template fails the check:

1. **Try a direct alternate URL first.** Imgflip occasionally hosts a
   no-sidebar variant under a different file hash (e.g.,
   `https://i.imgflip.com/<hash>.jpg`). Hash-test the alternate vs the cached
   file — if `md5sum` matches, the URL is the same image.
2. **If no alternate exists, register a preprocess entry in
   `TEMPLATE_PREPROCESS`** (in `src/meme_generator.py`). Set `crop` to drop
   the dead area and `resize_to` to bring the result back to the intended
   aspect. Regions in `TEMPLATE_REGIONS` are resolved against the
   *post-preprocess* canvas — keep that in mind when drawing them up.
3. **As a last resort, source the template from another host** (Reddit
   archive, Imgur, KnowYourMeme) and replace the cached file. Document the
   source in this file under "Decisions" so it doesn't get overwritten by
   the daily refresh — `fetch_trending.py` skips downloads when the cached
   file already exists.

## Decisions

- Use Anton (OFL) as the Impact substitute. Impact itself is Microsoft proprietary; we don't ship it. If a real `Impact.ttf` shows up in `fonts/`, the overlay will pick it up automatically.
- Trending cache is refreshed once a day at 03:37 UTC. Imgflip's "trending" list barely moves — more frequent fetches add nothing.
- Output stays inside `tmp/` (gitignored). Memes are ephemeral; we don't archive.
