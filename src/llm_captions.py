"""LLM-powered captions and template selection.

This module is an *upgrade* layer over the existing static caption pools and
heuristic template matcher. Every public function returns ``None`` (or an
empty list) on any failure — bad API key, timeout, JSON parse error, wrong
caption count, etc. — so the daily brief keeps shipping on the static pools
when the LLM is unavailable.

Guarantees:

  - No exception is allowed to escape these functions. Callers can treat
    ``None`` / ``[]`` as "fall back to your existing logic".
  - All calls go through ``gpt-4o-mini`` for cost reasons — the entire daily
    brief is well under 1¢ at this model.
  - ``OPENAI_API_KEY`` is checked once at module load. If missing, every
    function short-circuits to the fallback path silently. Cron stays quiet.
  - Temperature 0.9 for captions (we want jokes, not safety), 0.4 for
    template selection (we want sound judgement).

Typical wiring:

  >>> from .llm_captions import generate_captions
  >>> captions = generate_captions(
  ...     template_name="Flex Tape",
  ...     template_format="reaction",
  ...     box_count=2,
  ...     topic="shipping on Friday",
  ... )
  >>> if captions is None:
  ...     captions = static_fallback_pool[...]
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

# Soft import — module must load even when openai isn't installed yet.
try:
    from openai import OpenAI  # type: ignore[import-not-found]
    _OPENAI_AVAILABLE = True
except Exception:  # noqa: BLE001 — any import-time error disables LLM
    OpenAI = None  # type: ignore[assignment, misc]
    _OPENAI_AVAILABLE = False

MODEL = "gpt-4o-mini"
CAPTION_TEMPERATURE = 0.9
SELECTION_TEMPERATURE = 0.4
REQUEST_TIMEOUT_SECONDS = 30

# Format-specific guidance threaded into the system prompt. Keeps the LLM
# honest about joke shape — without this, gpt-4o-mini happily writes a
# generic two-liner for every template regardless of structure.
FORMAT_GUIDANCE: dict[str, str] = {
    "comparison":
        "Top caption = the boring/conventional thing. "
        "Bottom caption = the funny/absurd/edgy upgrade. "
        "Both about the SAME subject — the joke is the contrast.",
    "escalation":
        "Each caption escalates absurdity. Caption 1 is sane, "
        "the final caption is unhinged. Maintain a clear ascending arc.",
    "reaction":
        "ONE punchy observational line — the reaction caption sits over a "
        "face/scene that already conveys the emotion. Setup + payoff in one line.",
    "labeling":
        "Each caption labels a specific element of the scene. Be concrete "
        "(a role, a tool, a metric) — labels are the joke.",
    "declaration":
        "ONE bold, declarative hot take. Format is a sign / proclamation / "
        "podium statement. Confidence is the joke.",
    "confrontation":
        "Caption 1 = the accusation/yell/slap. Caption 2 = the deadpan "
        "deflection or admission. Setup ↔ payoff structure.",
    "multi_panel":
        "Sequential narrative — each caption is one beat. The final beat "
        "twists the premise. Captions 2-3 can be the same to show realisation.",
}

# Few-shot examples plucked from the existing CAPTION_POOLS. Demonstrate the
# desired voice (sharp, observational, tech audience) and structure per format.
FEW_SHOT_EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "comparison": [
        {
            "template": "Drake Hotline Bling",
            "topic": "AI replacing developers",
            "captions": ["Writing the spec", "Vibe-coding straight to prod"],
        },
        {
            "template": "Buff Doge vs Cheems",
            "topic": "engineering generations",
            "captions": [
                "2010 dev", "2026 dev",
                "Reads the man pages", "Reads ChatGPT",
            ],
        },
    ],
    "escalation": [
        {
            "template": "Clown Applying Makeup",
            "topic": "deploy strategies",
            "captions": [
                "Manual deploy", "CI/CD pipeline",
                "GitOps with ArgoCD", "Push to main and pray",
            ],
        },
    ],
    "reaction": [
        {
            "template": "This Is Fine",
            "topic": "shipping on Friday",
            "captions": ["Production database", "Migrations on Friday afternoon"],
        },
        {
            "template": "Waiting Skeleton",
            "topic": "flaky CI",
            "captions": ["Me waiting for tests to pass on flaky CI", ""],
        },
    ],
    "labeling": [
        {
            "template": "Distracted Boyfriend",
            "topic": "tech FOMO",
            "captions": [
                "Shiny LLM benchmark", "Indie hackers",
                "The boring API that ships",
            ],
        },
    ],
    "declaration": [
        {
            "template": "Change My Mind",
            "topic": "AI startups",
            "captions": [
                "Most AI startups are GPT wrappers with extra steps", "",
            ],
        },
    ],
    "confrontation": [
        {
            "template": "Woman Yelling At Cat",
            "topic": "engineering velocity",
            "captions": [
                "Why are revenue metrics so low?",
                "I haven't shipped a feature this quarter",
            ],
        },
    ],
    "multi_panel": [
        {
            "template": "Anakin Padme 4 Panel",
            "topic": "fake AI autonomy",
            "captions": [
                "Our AI is fully autonomous",
                "It's not just a prompt loop, right?",
                "It's not just a prompt loop, right?",
            ],
        },
    ],
}

CAPTION_RULES = (
    "Voice: sharp, observational, tech-audience humour. "
    "Punch up at situations, never at people. "
    "Specific beats generic. Setup ↔ payoff structure. "
    "Each caption MUST be 8 words or fewer. "
    "Output ALL CAPS handling is automatic — write normal case, "
    "the renderer uppercases at draw time. "
    "Never use emojis or hashtags."
)


# --------------------------------------------------------------------------- #
# Module-level state                                                           #
# --------------------------------------------------------------------------- #
def _api_key() -> str | None:
    """Fetch the OpenAI key from env, or return ``None``.

    Resolved per-call (not cached at import) so that tests / interactive
    runs can set the env var after import without restarting the process.
    """
    key = os.environ.get("OPENAI_API_KEY") or ""
    return key.strip() or None


def _client() -> Any | None:
    """Return an OpenAI client, or ``None`` if unavailable."""
    if not _OPENAI_AVAILABLE:
        return None
    key = _api_key()
    if not key:
        return None
    try:
        return OpenAI(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! llm_captions: client init failed ({exc})", file=sys.stderr)
        return None


def is_enabled() -> bool:
    """True iff the LLM path can be attempted right now."""
    return _OPENAI_AVAILABLE and _api_key() is not None


# --------------------------------------------------------------------------- #
# Prompt builders                                                              #
# --------------------------------------------------------------------------- #
def _caption_system_prompt(fmt: str, box_count: int) -> str:
    guidance = FORMAT_GUIDANCE.get(fmt, "Setup + payoff. Observational humour.")
    examples = FEW_SHOT_EXAMPLES.get(fmt) or FEW_SHOT_EXAMPLES["reaction"]
    examples_block = "\n".join(
        f"  - template={ex['template']!r} topic={ex['topic']!r} "
        f"-> {json.dumps(ex['captions'])}"
        for ex in examples
    )
    return (
        "You write captions for meme templates aimed at engineers, founders, "
        "and AI-curious technologists.\n\n"
        f"FORMAT ({fmt}): {guidance}\n\n"
        f"RULES: {CAPTION_RULES}\n\n"
        f"This template has {box_count} text box(es). Return EXACTLY "
        f"{box_count} caption(s) — no more, no fewer. Empty string is allowed "
        "for placeholder boxes but use it sparingly.\n\n"
        "FEW-SHOT EXAMPLES (same format, different topic):\n"
        f"{examples_block}\n\n"
        "Respond as a JSON object with one key: \"captions\" — a list of strings. "
        "No prose, no explanation."
    )


def _selection_system_prompt(count: int) -> str:
    return (
        "You are a meme-template selector for a daily brief aimed at engineers "
        "and founders. Given a topic and a list of eligible templates (already "
        "filtered for 7-day cooldown and within-batch dedup), pick the "
        f"{count} template(s) whose joke format best fits the topic.\n\n"
        "Prefer:\n"
        "  - format-topic fit (comparison/contrast topics -> Drake-style; "
        "escalation topics -> Expanding Brain / Clown; reaction topics -> "
        "This Is Fine / Surprised Pikachu; etc.)\n"
        "  - format diversity if asked for multiple picks\n"
        "  - templates with lower box_count for simple jokes, higher for "
        "narrative jokes\n\n"
        "Respond as a JSON object with one key: \"ids\" — a list of "
        f"{count} template id strings, in your preferred order. Use ids "
        "exactly as given (strings, not ints). No prose, no explanation."
    )


# --------------------------------------------------------------------------- #
# Public API: captions                                                         #
# --------------------------------------------------------------------------- #
def generate_captions(
    template_name: str,
    template_format: str,
    box_count: int,
    topic: str,
) -> list[str] | None:
    """Generate captions for a single template. Returns ``None`` on any failure.

    Validation:
      - Response must be JSON with key ``captions``.
      - List length must equal ``box_count`` (we trim/skip otherwise).
      - Each caption is coerced to ``str`` and stripped.
    """
    client = _client()
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=CAPTION_TEMPERATURE,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": _caption_system_prompt(template_format, box_count),
                },
                {
                    "role": "user",
                    "content": (
                        f"Template: {template_name}\n"
                        f"Format: {template_format}\n"
                        f"Box count: {box_count}\n"
                        f"Topic: {topic}\n\n"
                        f"Write {box_count} caption(s)."
                    ),
                },
            ],
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        raw = resp.choices[0].message.content or ""
        payload = json.loads(raw)
        captions = payload.get("captions")
        if not isinstance(captions, list):
            return None
        captions = [str(c).strip() for c in captions]
        if len(captions) != box_count:
            return None
        return captions
    except Exception as exc:  # noqa: BLE001 — never propagate
        print(f"  ! llm_captions.generate_captions failed ({exc})",
              file=sys.stderr)
        return None


def generate_batch_captions(
    slots: list[dict[str, Any]],
) -> list[list[str] | None] | None:
    """Generate captions for an entire batch in a single LLM call.

    Each slot dict must have keys: ``template_name``, ``format``,
    ``box_count``, ``topic``.

    Returns a list parallel to ``slots`` where each entry is the caption list
    for that slot, or ``None`` for slots the LLM couldn't satisfy. Returns
    ``None`` (the outer value) if the entire call failed — caller should fall
    back to per-slot static pools.
    """
    if not slots:
        return []
    client = _client()
    if client is None:
        return None
    try:
        slot_payload = [
            {
                "index": i,
                "template": s["template_name"],
                "format": s["format"],
                "box_count": int(s["box_count"]),
                "topic": s["topic"],
            }
            for i, s in enumerate(slots)
        ]
        system = (
            "You write captions for a daily meme brief aimed at engineers and "
            "founders. You will receive a batch of slots, each with its own "
            "template, format, box count, and topic. Produce captions for ALL "
            "slots in one response.\n\n"
            f"RULES: {CAPTION_RULES}\n\n"
            "For each slot, the number of captions MUST equal its box_count. "
            "Vary the angle across slots — the brief should not read like five "
            "jokes about the same beat.\n\n"
            "Respond as a JSON object with key \"slots\" — a list of objects, "
            "each {\"index\": <int>, \"captions\": [<strings>]}. No prose."
        )
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=CAPTION_TEMPERATURE,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        "Batch slots:\n"
                        f"{json.dumps(slot_payload, indent=2)}\n\n"
                        "Write captions for every slot."
                    ),
                },
            ],
            timeout=REQUEST_TIMEOUT_SECONDS * 2,
        )
        raw = resp.choices[0].message.content or ""
        payload = json.loads(raw)
        out_slots = payload.get("slots")
        if not isinstance(out_slots, list):
            return None
        # Reshape to a list parallel to ``slots``.
        by_idx: dict[int, list[str]] = {}
        for entry in out_slots:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("index"))
            except (TypeError, ValueError):
                continue
            captions = entry.get("captions")
            if not isinstance(captions, list):
                continue
            by_idx[idx] = [str(c).strip() for c in captions]
        results: list[list[str] | None] = []
        for i, slot in enumerate(slots):
            captions = by_idx.get(i)
            if captions is None or len(captions) != int(slot["box_count"]):
                results.append(None)
            else:
                results.append(captions)
        # If literally every slot is None, treat as a failure so the caller
        # can take the static path cleanly instead of looping over Nones.
        if all(r is None for r in results):
            return None
        return results
    except Exception as exc:  # noqa: BLE001
        print(f"  ! llm_captions.generate_batch_captions failed ({exc})",
              file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# Public API: template selection                                               #
# --------------------------------------------------------------------------- #
def _compact_template_line(t: dict[str, Any], fmt: str) -> str:
    """Compact one template into a single line for the selection prompt."""
    name = str(t.get("name") or "").strip()
    return f"{t.get('id')} | {name} | {fmt} | boxes={t.get('box_count') or '?'}"


def select_template(
    topic: str,
    available_templates: list[dict[str, Any]],
    count: int,
    *,
    format_of: Any = None,
) -> list[str] | None:
    """Ask the LLM to pick ``count`` template ids from the eligible pool.

    ``available_templates`` is the *post-cooldown, post-dedup* list — the
    cooldown and within-batch dedup guards are the caller's responsibility.
    The LLM only judges fit.

    ``format_of`` is a callable ``(template) -> str`` used to label each
    candidate's format in the prompt. Defaults to importing
    ``template_categories.get_format`` lazily so this module stays
    self-contained.

    Returns the list of chosen ids (length ``count``), or ``None`` on
    failure. Caller should validate the ids against ``available_templates``
    before using them.
    """
    if count <= 0 or not available_templates:
        return None
    client = _client()
    if client is None:
        return None
    if format_of is None:
        from .template_categories import get_format as _gf
        format_of = _gf
    try:
        lines = [_compact_template_line(t, format_of(t)) for t in available_templates]
        prompt_body = "\n".join(lines)
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=SELECTION_TEMPERATURE,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _selection_system_prompt(count)},
                {
                    "role": "user",
                    "content": (
                        f"Topic: {topic}\n"
                        f"Pick {count} template(s). Available pool "
                        f"(id | name | format | box_count):\n\n"
                        f"{prompt_body}"
                    ),
                },
            ],
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        raw = resp.choices[0].message.content or ""
        payload = json.loads(raw)
        ids = payload.get("ids")
        if not isinstance(ids, list):
            return None
        ids = [str(x) for x in ids]
        valid_ids = {str(t.get("id")) for t in available_templates}
        filtered = [i for i in ids if i in valid_ids]
        if not filtered:
            return None
        # Dedupe while preserving order.
        seen: set[str] = set()
        ordered: list[str] = []
        for i in filtered:
            if i not in seen:
                seen.add(i)
                ordered.append(i)
        return ordered[:count] if ordered else None
    except Exception as exc:  # noqa: BLE001
        print(f"  ! llm_captions.select_template failed ({exc})",
              file=sys.stderr)
        return None
