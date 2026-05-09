# Meme Engine

A CLI-powered meme generator built for tech/programming/AI/startup humor. Generates trending memes with smart template selection, 7-day rotation, and delivers them to Telegram.

## Features

- **100+ meme templates** from Imgflip, categorized by format (comparison, escalation, reaction, labeling, etc.)
- **Joke-first pipeline**: topic → format → caption → template — picks the right meme for the joke, not the other way around
- **7-day template cooldown** — no repeats within a week via `history.json`
- **Daily trending brief** — 5 fresh memes every morning at 9 AM IST via Telegram
- **15% AI wildcards** — occasionally generates fully custom memes using OpenAI image generation
- **Multi-platform sizing** — Instagram square/story/portrait, Twitter, Facebook, TikTok, Reddit
- **Telegram integration** — trigger memes via chat commands, receive results as photos

## Setup

Requires [uv](https://docs.astral.sh/uv/) as the package manager.

```bash
cd meme-engine
uv venv .venv
uv pip install -r requirements.txt
```

### Required Environment Variables

```
IMGFLIP_USERNAME=<your imgflip username>
IMGFLIP_PASSWORD=<your imgflip password>
TELEGRAM_BOT_TOKEN=<telegram bot token>
OPENAI_API_KEY=<optional, for AI wildcard memes>
```

## Usage

```bash
# Refresh trending template cache
python -m src.fetch_trending

# List top trending templates
python -m src.meme_generator --list 10

# Generate with a specific template
python -m src.meme_generator \
  --template "drake" \
  --top "writing tests" \
  --bottom "shipping on friday"

# Auto-pick template based on topic
python -m src.meme_generator --surprise --topic "monday standups" \
  --top "me at 9am" --bottom "me at 9:01am"

# Platform-specific sizing
python -m src.meme_generator --template "two buttons" \
  --top "merge to main" --bottom "wait for review" \
  --platform instagram_square

# AI-generated custom meme
python -m src.meme_generator --openai \
  --prompt "a cat coding python at 3am, vaporwave palette" \
  --top "git push --force" --bottom "what could go wrong"
```

Output goes to `tmp/<unix-ts>-<slug>.png` unless `--output` is specified.

## Telegram Commands

| Command | What it does |
|---------|-------------|
| `meme about <topic>` | Auto-picks template + writes captions |
| `trending memes` | Shows top 5 trending formats |
| `surprise me` | Random template + auto-generated captions |
| `meme for <platform>` | Generates with platform-specific sizing |
| `meme: <topic> // <top> // <bottom>` | Manual caption override |

## How It Works

### Joke-First Selection Pipeline

1. Pick a topic from the daily rotation
2. Match a joke format (comparison, escalation, reaction, etc.)
3. Select captions from curated pools or generate them
4. Pick a template in that format not used in the last 7 days
5. Render via Imgflip caption API
6. Send to Telegram

### Variety / Rotation

- **100-template pool** from Imgflip (refreshed daily)
- **7-day cooldown** per template via `history.json`
- **Format diversity** within each batch — avoids repeating the same format type
- **Web extras** — additional viral formats in `web_templates.json`
- **OpenAI wildcards** — 15% of daily slots skip templates entirely for AI-generated memes

### Cron Jobs (UTC)

| Time | Job |
|------|-----|
| `03:25` | Refresh Imgflip trending cache |
| `03:30` | Send daily trending memes brief (9 AM IST) |

## Project Structure

```
meme-engine/
  src/
    fetch_trending.py      # Refreshes trending.json + template images
    meme_generator.py      # CLI entry point
    text_overlay.py        # Pillow Impact-style text rendering
    daily_trending.py      # Morning brief (5 memes/day)
    template_categories.py # Format taxonomy
    template_matcher.py    # Joke-first selection + rotation
    history.py             # Usage log + 7-day cooldown
    web_templates.py       # Extra templates from outside Imgflip
  fonts/                   # Anton-Regular.ttf (Impact substitute)
  templates/               # Cached template images (gitignored)
  tmp/                     # Generated memes (gitignored)
  trending.json            # Imgflip cache (gitignored)
  history.json             # Rotation log (gitignored)
```

## Caption Guidelines

- Punch up at situations, never at people
- Specific > generic ("Wednesday standup at 9:01am" > "long meetings")
- Setup/payoff structure: top = premise, bottom = twist
- Cap each line at ~6 words
- ALL CAPS applied automatically

## Tech Stack

- Python 3 + Pillow (text overlay)
- Imgflip API (template rendering)
- OpenAI API (wildcard image generation)
- Telegram Bot API (delivery)
- uv (package management)
