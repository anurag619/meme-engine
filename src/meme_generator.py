#!/usr/bin/env python3
"""Meme generator — pick a trending template, overlay text, save to tmp/.

Usage examples
--------------
List the top 10 trending templates:
    python -m src.meme_generator --list 10

Render a specific template by fuzzy name match:
    python -m src.meme_generator \
        --template "drake" \
        --top "writing tests" \
        --bottom "shipping on friday"

Surprise me (random trending template) — captions still required unless
you pass --auto-caption (which uses a tiny built-in joke template):
    python -m src.meme_generator --surprise --topic "monday standups"

Resize for a platform:
    python -m src.meme_generator --template "two buttons" \
        --top "merge to main" --bottom "wait for review" \
        --platform instagram_square

Custom AI-generated image (fallback when no template fits):
    python -m src.meme_generator --openai \
        --prompt "a cat coding python at 3am, vaporwave palette" \
        --top "git push --force" --bottom "what could go wrong"
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
from io import BytesIO
from PIL import Image, ImageChops

from . import fetch_trending
from .text_overlay import (
    TEMPLATE_REGIONS,
    draw_meme_panels,
    draw_meme_text,
    resolve_font,
)

ROOT = Path(__file__).resolve().parent.parent
TRENDING_JSON = ROOT / "trending.json"
TEMPLATES_DIR = ROOT / "templates"
TMP_DIR = ROOT / "tmp"

# Per-template preprocessing applied at load time. Use to neutralise Imgflip
# split-layout templates (built-in white sidebars / text columns) so that the
# returned image is content-only at the desired aspect.
#
# Schema:
#   "<imgflip_id>": {
#       "crop": (x1, y1, x2, y2)  # normalised 0..1, OPTIONAL
#       "resize_to": (w, h)       # absolute pixels, OPTIONAL
#   }
#
# Order: crop first, then resize. Regions in TEMPLATE_REGIONS are interpreted
# against the *post-preprocess* image, so always express them in the final
# canvas space.
TEMPLATE_PREPROCESS: dict[str, dict[str, Any]] = {
    # Drake Hotline Bling — Imgflip serves a 1200x1200 split-layout (Drake on
    # left, white sidebar on right). Drop the sidebar, then resize the
    # photo-only column to a square 1080x1080 so Drake fills the frame.
    "181913649": {
        "crop": (0.0, 0.0, 0.5, 1.0),
        "resize_to": (1080, 1080),
    },
}


IMGFLIP_CAPTION_URL = "https://api.imgflip.com/caption_image"
SECRETS_PATH = Path.home() / ".config" / "jarvis" / "secrets.env"

# Imgflip serves captioned JPEGs at the template's native resolution, which
# for popular formats (Flex Tape, Clown, Drake) is < 600px. Telegram's
# sendPhoto pipeline then re-compresses, producing visible blocky JPEG
# artefacts. Upscale to ~1200px on the shorter edge with LANCZOS so text
# stays crisp and Telegram has no excuse to re-encode aggressively.
MIN_DELIVERY_SIDE_PX = 1200


def _upscale_for_delivery(image: Image.Image, min_side: int = MIN_DELIVERY_SIDE_PX) -> Image.Image:
    """Return ``image`` upscaled so its shorter edge is at least ``min_side``.

    No-op if already large enough. Uses LANCZOS resampling for clean text edges.
    """
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    w, h = image.size
    short = min(w, h)
    if short >= min_side:
        return image
    scale = min_side / float(short)
    return image.resize(
        (int(round(w * scale)), int(round(h * scale))), Image.LANCZOS
    )


def _load_secrets_env(path: Path = SECRETS_PATH) -> None:
    """Load KEY=VALUE pairs from ~/.config/jarvis/secrets.env into os.environ.

    Existing env vars win — we never clobber. No-op if the file is missing.
    Quiet about parsing errors; this is best-effort, not auth.
    """
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
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            os.environ.setdefault(key, val)
    except OSError:
        pass


def caption_via_imgflip(
    template_id: str,
    captions: list[str],
    *,
    username: str,
    password: str,
    timeout: int = 30,
) -> Image.Image:
    """Render a meme via Imgflip's caption_image endpoint.

    `captions[i]` is wired to text<i>. Imgflip handles layout, fonts,
    panel-aware placement, and stroke. Returns the rendered Image.
    Raises RuntimeError on API failure (call-site decides whether to fall back).
    """
    payload: dict[str, str] = {
        "template_id": str(template_id),
        "username": username,
        "password": password,
    }
    # Imgflip's text0/text1 only supports 2 boxes. For 3+ captions, use
    # the boxes[] parameter so all panels get text.
    if len(captions) <= 2:
        for i, c in enumerate(captions):
            payload[f"text{i}"] = c or ""
    else:
        for i, c in enumerate(captions):
            payload[f"boxes[{i}][text]"] = c or ""
    resp = requests.post(IMGFLIP_CAPTION_URL, data=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(
            f"Imgflip caption_image failed: {body.get('error_message')}"
        )
    img_url = body["data"]["url"]
    img_resp = requests.get(img_url, timeout=timeout)
    img_resp.raise_for_status()
    return _upscale_for_delivery(Image.open(BytesIO(img_resp.content)))


PLATFORM_SIZES: dict[str, tuple[int, int]] = {
    "instagram_square": (1080, 1080),
    "instagram_story": (1080, 1920),
    "instagram_portrait": (1080, 1350),
    "twitter": (1200, 675),
    "facebook": (1200, 630),
    "tiktok": (1080, 1920),
    "reddit": (1200, 1200),
}


# --------------------------------------------------------------------------- #
# Trending cache loading                                                       #
# --------------------------------------------------------------------------- #
def load_trending() -> dict[str, Any]:
    """Load trending.json, refreshing it on the fly if missing."""
    if not TRENDING_JSON.exists():
        print("[meme_generator] trending.json missing — refreshing now ...")
        rc = fetch_trending.main()
        if rc != 0:
            raise SystemExit(rc)
    return json.loads(TRENDING_JSON.read_text())


def find_template(query: str, templates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the best-matching template by id or fuzzy name match."""
    if not query:
        return None
    q = query.strip().lower()
    # Exact id match wins.
    for t in templates:
        if str(t.get("id")) == q:
            return t
    # Whole-name case-insensitive match.
    for t in templates:
        if (t.get("name") or "").lower() == q:
            return t
    # Substring scan, ranked by rank (highest trend first).
    matches = [
        t for t in templates
        if q in (t.get("name") or "").lower()
    ]
    if matches:
        return matches[0]
    # Word overlap fallback — useful for "two buttons" → "Two Buttons".
    q_words = set(re.findall(r"\w+", q))
    best, best_score = None, 0
    for t in templates:
        name_words = set(re.findall(r"\w+", (t.get("name") or "").lower()))
        score = len(q_words & name_words)
        if score > best_score:
            best, best_score = t, score
    return best


def pick_for_topic(topic: str, templates: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick a template that *feels* right for a topic.

    Light-touch heuristic: a few hand-tuned keyword → template-name hints,
    falling back to a random pick from the top 10 trending. Good enough for
    'surprise me' — Jarvis adds the actual humour at caption time.
    """
    topic_l = (topic or "").lower()
    hints: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
        (("compare", "versus", " vs ", "old", "new", "prefer"),
         ("drake", "two buttons", "expanding brain")),
        (("deny", "refuse", "wrong", "no thanks"),
         ("drake", "disaster girl", "this is fine")),
        (("hard choice", "decision", "dilemma"),
         ("two buttons", "sweating", "roll safe")),
        (("clever", "smart", "galaxy", "evolve", "level up"),
         ("expanding brain",)),
        (("everything", "fire", "broken", "outage", "bug", "incident"),
         ("this is fine", "disaster girl")),
        (("girlfriend", "boyfriend", "distracted", "tempted"),
         ("distracted boyfriend",)),
        (("yelling", "argue", "fight", "conspiracy"),
         ("woman yelling at a cat", "always has been")),
        (("space", "moon", "always has", "realised"),
         ("always has been",)),
        (("kid", "baby", "success"),
         ("success kid",)),
        (("dog", "fine", "calm"),
         ("this is fine",)),
    ]
    for keywords, candidate_names in hints:
        if any(k in topic_l for k in keywords):
            for cand in candidate_names:
                t = find_template(cand, templates)
                if t:
                    return t
    # Fallback: random from top 10 trending.
    pool = templates[:10] or templates
    return random.choice(pool)


def auto_caption(topic: str) -> tuple[str, str]:
    """Last-resort caption generator. Genuinely mid; supply your own when you can."""
    topic = (topic or "").strip() or "monday meetings"
    formats = [
        ("ME EXPLAINING", topic.upper()),
        ("EVERYONE", f"ME WHEN I HEAR '{topic.upper()}'"),
        (topic.upper(), "STILL NOT FIXED"),
        (f"DAY 47 OF {topic.upper()}", "I AM IN DANGER"),
    ]
    return random.choice(formats)


# --------------------------------------------------------------------------- #
# Image acquisition                                                            #
# --------------------------------------------------------------------------- #
def auto_trim_white(
    image: Image.Image,
    diff_threshold: int = 15,
    max_trim_ratio: float = 0.55,
) -> Image.Image:
    """Crop near-white margins from the image edges.

    Imgflip's "split-layout" templates (Drake, Tuxedo Pooh, Expanding Brain,
    etc.) include a wide white sidebar designed for their own captioner. We
    don't want it — text goes on the photo, classic-meme style.

    `diff_threshold` is how far a pixel can be from pure white before it counts
    as content (channel value < 255 - threshold). `max_trim_ratio` caps how
    much can be cropped from any single edge so we never destroy a real photo.
    """
    rgb = image.convert("RGB")
    w, h = rgb.size
    bg = Image.new("RGB", rgb.size, (255, 255, 255))
    diff = ImageChops.difference(rgb, bg).convert("L")
    # Pixels meaningfully darker than white become 255 in the mask.
    mask = diff.point(lambda v: 255 if v > diff_threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return rgb
    left, top, right, bottom = bbox
    max_left = int(w * max_trim_ratio)
    max_top = int(h * max_trim_ratio)
    left = min(left, max_left)
    top = min(top, max_top)
    right = max(right, w - max_left)
    bottom = max(bottom, h - max_top)
    if (left, top, right, bottom) == (0, 0, w, h):
        return rgb
    return rgb.crop((left, top, right, bottom))


def load_template_image(template: dict[str, Any], trim: bool = False) -> Image.Image:
    """Load a template image — from local cache if present, else download.

    By default the image is returned at Imgflip's original aspect ratio
    (e.g., Drake = 1200x1200). Set `trim=True` to strip near-white margins,
    but be aware that doing so changes the canvas aspect; regions defined in
    normalised coords still resolve correctly, but the output may end up
    narrow (Drake → 600x1200). Recommended only when you know the template
    has a useless sidebar AND you'll resize for a platform afterwards.
    """
    local = template.get("local_path")
    img: Image.Image | None = None
    if local:
        path = ROOT / local
        if path.exists():
            img = Image.open(path)
    if img is None:
        url = template.get("url")
        if not url:
            raise RuntimeError(f"Template {template.get('name')!r} has no URL.")
        resp = requests.get(
            url,
            headers={"User-Agent": "jarvis-meme-engine/0.1"},
            timeout=20,
        )
        resp.raise_for_status()
        ext = url.rsplit(".", 1)[-1].split("?")[0].lower()
        if ext not in {"jpg", "jpeg", "png", "webp"}:
            ext = "jpg"
        out = TEMPLATES_DIR / f"{template.get('id')}.{ext}"
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)
        img = Image.open(out)
    img = auto_trim_white(img) if trim else img
    pre = TEMPLATE_PREPROCESS.get(str(template.get("id")))
    if pre:
        if "crop" in pre:
            w, h = img.size
            x1, y1, x2, y2 = pre["crop"]
            img = img.crop(
                (int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h))
            )
        if "resize_to" in pre:
            img = img.resize(pre["resize_to"], Image.LANCZOS)
    return img


def generate_openai_image(prompt: str, size: str = "1024x1024") -> Image.Image:
    """Generate a custom meme background via OpenAI image API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set; cannot use --openai mode.")
    resp = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-image-1",
            "prompt": prompt,
            "size": size,
            "n": 1,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()["data"][0]
    if "b64_json" in data:
        import base64
        from io import BytesIO
        return Image.open(BytesIO(base64.b64decode(data["b64_json"])))
    if "url" in data:
        from io import BytesIO
        img_resp = requests.get(data["url"], timeout=60)
        img_resp.raise_for_status()
        return Image.open(BytesIO(img_resp.content))
    raise RuntimeError(f"Unexpected OpenAI response: {data}")


# --------------------------------------------------------------------------- #
# Output                                                                       #
# --------------------------------------------------------------------------- #
def fit_to_platform(image: Image.Image, target: tuple[int, int]) -> Image.Image:
    """Letterbox/pillarbox the meme into the target aspect without cropping."""
    tw, th = target
    iw, ih = image.size
    scale = min(tw / iw, th / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    resized = image.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", target, (0, 0, 0))
    canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text or "meme").strip("-").lower()
    return text[:40] or "meme"


def save_output(image: Image.Image, slug: str, output: Path | None) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    if output is None:
        output = TMP_DIR / f"{int(time.time())}-{slug}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")
    return output


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def cmd_list(n: int) -> int:
    data = load_trending()
    print(f"Top {n} trending templates (source: {data.get('source')}, "
          f"refreshed {time.ctime(data.get('generated_at', 0))}):")
    for t in data.get("templates", [])[:n]:
        cached = "✓" if t.get("local_path") else " "
        print(f"  [{t['rank']:>2}] {cached} {t['name']:<40} id={t['id']}  "
              f"boxes={t.get('box_count')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a meme.")
    parser.add_argument("--list", type=int, nargs="?", const=10,
                        help="List top N trending templates and exit.")
    parser.add_argument("--template", type=str,
                        help="Template name or id (fuzzy match).")
    parser.add_argument("--surprise", action="store_true",
                        help="Pick a random trending template.")
    parser.add_argument("--topic", type=str, default="",
                        help="Topic hint used for template picking and auto captions.")
    parser.add_argument("--top", type=str, default="",
                        help="Top caption text.")
    parser.add_argument("--bottom", type=str, default="",
                        help="Bottom caption text.")
    parser.add_argument("--auto-caption", action="store_true",
                        help="Generate captions from --topic if none provided.")
    parser.add_argument("--platform", type=str, choices=sorted(PLATFORM_SIZES),
                        help="Resize output to a platform-friendly canvas.")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output path (default: tmp/<ts>-<slug>.png).")
    parser.add_argument("--openai", action="store_true",
                        help="Generate a custom background via OpenAI.")
    parser.add_argument("--prompt", type=str, default="",
                        help="OpenAI image prompt (only with --openai).")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress informational output.")
    parser.add_argument("--no-imgflip", action="store_true",
                        help="Skip Imgflip caption_image API; force local Pillow render.")
    args = parser.parse_args()

    _load_secrets_env()

    if args.list is not None:
        return cmd_list(args.list)

    # Pick the template ------------------------------------------------------ #
    template: dict[str, Any] | None = None
    if args.openai:
        prompt = args.prompt or args.topic
        if not prompt:
            print("--openai requires --prompt or --topic.", file=sys.stderr)
            return 2
        if not args.quiet:
            print(f"[meme_generator] custom OpenAI image: {prompt!r}")
        chosen_name = "openai-custom"
    else:
        data = load_trending()
        templates = data.get("templates", [])
        if not templates:
            print("No trending templates cached. Run fetch_trending.py first.",
                  file=sys.stderr)
            return 2
        if args.template:
            template = find_template(args.template, templates)
            if not template:
                print(f"No template matched {args.template!r}.", file=sys.stderr)
                return 2
        elif args.surprise:
            template = pick_for_topic(args.topic, templates)
        elif args.topic:
            template = pick_for_topic(args.topic, templates)
        else:
            print("Need one of --template, --surprise, --topic, or --openai.",
                  file=sys.stderr)
            return 2
        if not args.quiet:
            print(f"[meme_generator] using template "
                  f"{template['name']!r} (id={template['id']})")
        chosen_name = template["name"]

    # Captions --------------------------------------------------------------- #
    top, bottom = args.top, args.bottom
    if not top and not bottom and args.auto_caption:
        top, bottom = auto_caption(args.topic)
        if not args.quiet:
            print(f"[meme_generator] auto captions: {top!r} / {bottom!r}")
    if not top and not bottom:
        print("No captions provided. Pass --top / --bottom (or --auto-caption).",
              file=sys.stderr)
        return 2

    # ---------------------------------------------------------------------- #
    # Render path A: Imgflip caption_image API (preferred for Imgflip templates)
    # ---------------------------------------------------------------------- #
    rendered: Image.Image | None = None
    if not args.openai and not args.no_imgflip:
        imgflip_user = os.environ.get("IMGFLIP_USERNAME")
        imgflip_pass = os.environ.get("IMGFLIP_PASSWORD")
        if imgflip_user and imgflip_pass:
            try:
                if not args.quiet:
                    print(f"[meme_generator] rendering via Imgflip API "
                          f"(template_id={template['id']})")
                rendered = caption_via_imgflip(
                    template_id=str(template["id"]),
                    captions=[top or "", bottom or ""],
                    username=imgflip_user,
                    password=imgflip_pass,
                )
            except (requests.RequestException, RuntimeError) as exc:
                print(f"[meme_generator] Imgflip API failed ({exc}); "
                      f"falling back to local Pillow render.", file=sys.stderr)
        elif not args.quiet:
            print("[meme_generator] no IMGFLIP_USERNAME/IMGFLIP_PASSWORD; "
                  "using local Pillow render. "
                  "Add credentials to ~/.config/jarvis/secrets.env to enable "
                  "the Imgflip API.")

    # ---------------------------------------------------------------------- #
    # Render path B: local Pillow (custom OpenAI images, or Imgflip fallback)
    # ---------------------------------------------------------------------- #
    if rendered is None:
        if args.openai:
            image = generate_openai_image(args.prompt or args.topic)
        else:
            image = load_template_image(template)
        font_path = resolve_font()
        template_id = None if args.openai else str(template["id"])
        regions = TEMPLATE_REGIONS.get(template_id) if template_id else None
        if regions:
            captions = [c for c in (top, bottom) if c is not None]
            if not args.quiet:
                print(f"[meme_generator] using {len(regions)} text region(s) "
                      f"defined for template id {template_id}")
            rendered = draw_meme_panels(
                image, captions, regions, font_path=font_path,
            )
        else:
            rendered = draw_meme_text(
                image, top=top, bottom=bottom, font_path=font_path,
            )

    if args.platform:
        rendered = fit_to_platform(rendered, PLATFORM_SIZES[args.platform])

    out_path = save_output(rendered, slugify(chosen_name), args.output)
    if not args.quiet:
        print(f"[meme_generator] wrote {out_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
