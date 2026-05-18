"""Joke-first template matcher.

The old flow was "grab the top trending template, then write a caption that
fits". That kept landing on the same 3 templates. This module flips it:

    1. Decide what *kind* of joke to tell (format).
    2. Pick a template in that format that hasn't been used recently.

Inputs come from ``template_categories`` (format taxonomy) and ``history``
(7-day cooldown). The Imgflip + web pools are merged via
``web_templates.merge_with_imgflip`` upstream.
"""
from __future__ import annotations

import random
from collections import Counter
from typing import Any, Iterable

from .template_categories import (
    ALL_FORMATS,
    get_format,
    suggest_format,
    templates_by_format,
)
from . import history, llm_captions


def _least_used_format(
    grouped: dict[str, list[dict[str, Any]]],
    recent_formats: Iterable[str],
) -> str:
    """Pick the format with the fewest recent uses, breaking ties randomly.

    Empty buckets are skipped so we never recommend a format we can't fill.
    """
    counts = Counter(recent_formats)
    eligible = [fmt for fmt in ALL_FORMATS if grouped.get(fmt)]
    if not eligible:
        # Pathological — every format bucket is empty. Caller will handle.
        return random.choice(list(ALL_FORMATS))
    eligible.sort(key=lambda f: (counts.get(f, 0), random.random()))
    return eligible[0]


def choose_format(
    topic: str,
    grouped: dict[str, list[dict[str, Any]]],
    *,
    recent_formats: list[str] | None = None,
    forbid: Iterable[str] = (),
) -> str:
    """Pick a format for this joke.

    Order of preference:
      1. Topic-keyword hint (``suggest_format``) — if it points to a non-empty,
         non-forbidden bucket.
      2. The least-recently-used non-forbidden format with templates in it.
    """
    forbid_set = set(forbid)
    suggested = suggest_format(topic)
    if suggested and suggested not in forbid_set and grouped.get(suggested):
        return suggested

    if recent_formats is None:
        recent_formats = history.recently_used_formats()

    # Filter the grouped dict so _least_used_format doesn't pick a forbidden bucket.
    pruned = {
        fmt: tpls for fmt, tpls in grouped.items()
        if fmt not in forbid_set and tpls
    }
    if not pruned:
        return random.choice(list(ALL_FORMATS))
    return _least_used_format(pruned, recent_formats)


# Within a "fresh" bucket (already past cooldown / within-batch dedup), we
# always prefer the least-recently-used template — even when the LLM is
# making the call. This stops "iconic" templates (Drake, This Is Fine,
# Panik Kalm) from re-surfacing every 3-5 days when the LLM picker biases
# toward famous formats. We compute an LRU-sorted slice of the bucket and
# only that slice is presented to the LLM.
#
# LRU_CANDIDATE_FLOOR keeps the candidate pool from collapsing to one
# template (which would defeat the LLM's purpose). LRU_CANDIDATE_FRACTION
# is the share of the bucket we keep at the LRU end.
LRU_CANDIDATE_FLOOR = 3
LRU_CANDIDATE_FRACTION = 0.5


def _lru_sorted(
    candidates: list[dict[str, Any]],
    *,
    last_used: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Sort ``candidates`` least-recently-used first, with random tie-break.

    Templates never used are treated as ts=0 and float to the top. Random
    tie-break stops the same template id from always winning when several
    are never-used.
    """
    if last_used is None:
        last_used = history.last_used_timestamps()
    return sorted(
        candidates,
        key=lambda t: (last_used.get(str(t.get("id")), 0.0), random.random()),
    )


def _lru_candidate_pool(
    fresh: list[dict[str, Any]],
    *,
    last_used: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Return the LRU-end slice of ``fresh`` to present to the LLM picker.

    Size = max(LRU_CANDIDATE_FLOOR, ⌈len(fresh) * LRU_CANDIDATE_FRACTION⌉)
    capped at len(fresh).
    """
    if not fresh:
        return []
    ordered = _lru_sorted(fresh, last_used=last_used)
    cap = max(
        LRU_CANDIDATE_FLOOR,
        -(-len(ordered) * 1 // 2),  # ceil-div for the fraction
    )
    return ordered[: min(cap, len(ordered))]


def pick_template_via_llm(
    *,
    topic: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Ask the LLM to pick the best template out of an already-filtered pool.

    ``candidates`` MUST already be filtered for cooldown / within-batch dedup
    by the caller — the LLM only judges fit. Returns the chosen template, or
    ``None`` on any failure (no key, timeout, bad JSON, id not in pool).

    Kept as a thin wrapper around ``llm_captions.select_template`` so the LLM
    contract lives in one module.
    """
    if not candidates or not llm_captions.is_enabled():
        return None
    ids = llm_captions.select_template(
        topic=topic,
        available_templates=candidates,
        count=1,
        format_of=get_format,
    )
    if not ids:
        return None
    by_id = {str(t.get("id")): t for t in candidates}
    return by_id.get(ids[0])


def pick_template(
    *,
    topic: str,
    templates: list[dict[str, Any]],
    fmt: str | None = None,
    exclude_ids: Iterable[str] | None = None,
    cooldown_days: int = 7,
) -> tuple[dict[str, Any], str]:
    """Pick a template for the given topic.

    Returns ``(template, format)``. If ``fmt`` is supplied we trust it; otherwise
    ``choose_format`` decides. Templates whose id is in ``exclude_ids`` or in
    the recent-cooldown window are skipped. If a format's pool is fully on
    cooldown we relax the cooldown for that format only (better to reuse than
    fail).

    Raises ``RuntimeError`` if ``templates`` is empty.
    """
    if not templates:
        raise RuntimeError("template_matcher.pick_template called with empty pool")

    grouped = templates_by_format(templates)
    recent_ids = set(history.recently_used_ids(cooldown_days))
    if exclude_ids:
        recent_ids |= {str(i) for i in exclude_ids}
    recent_formats = history.recently_used_formats(cooldown_days)

    chosen_fmt = fmt or choose_format(
        topic, grouped, recent_formats=recent_formats,
    )
    bucket = list(grouped.get(chosen_fmt) or [])

    fresh = [t for t in bucket if str(t.get("id")) not in recent_ids]
    if fresh:
        # LRU-bounded LLM pick. We narrow ``fresh`` to its LRU half before
        # showing it to the LLM, so even when the model has a bias toward
        # iconic formats (Drake-shaped comparisons, etc.) it can only
        # choose from templates we haven't used in a while. Falls through
        # to the LRU template directly on any LLM failure.
        last_used = history.last_used_timestamps()
        candidate_pool = _lru_candidate_pool(fresh, last_used=last_used)
        llm_pick = pick_template_via_llm(topic=topic, candidates=candidate_pool)
        if llm_pick is not None:
            return llm_pick, chosen_fmt
        return candidate_pool[0], chosen_fmt

    # Bucket fully on cooldown — relax for this format only.
    if bucket:
        # Avoid the very-most-recent id at minimum.
        avoid = {str(i) for i in (exclude_ids or [])}
        relaxed = [t for t in bucket if str(t.get("id")) not in avoid] or bucket
        # Still prefer LRU even in the relaxed pool — this is the path
        # taken when a small format bucket is fully on cooldown.
        return _lru_sorted(relaxed)[0], chosen_fmt

    # Format had no templates at all — fall back to any non-cooldown template.
    fallback_pool = [
        t for t in templates if str(t.get("id")) not in recent_ids
    ] or templates
    pick = random.choice(fallback_pool)
    return pick, get_format(pick)


def pick_distinct_set(
    *,
    topic: str,
    templates: list[dict[str, Any]],
    count: int,
    cooldown_days: int = 7,
    prefer_diverse_formats: bool = True,
) -> list[tuple[dict[str, Any], str]]:
    """Pick ``count`` templates for this topic with no template repeats and
    (when ``prefer_diverse_formats``) format diversity within the batch.

    Used by daily_trending to fill its 5 daily slots, and by the variety test
    to prove different jokes about the same topic land on different templates.
    """
    if count <= 0 or not templates:
        return []

    grouped = templates_by_format(templates)
    recent_ids = set(history.recently_used_ids(cooldown_days))
    recent_formats = history.recently_used_formats(cooldown_days)

    used_ids: set[str] = set()
    used_formats: list[str] = []
    picks: list[tuple[dict[str, Any], str]] = []

    for _ in range(count):
        forbid = set(used_formats) if prefer_diverse_formats else set()
        # If we've already covered every format, allow repeats.
        if len(forbid) >= len([f for f in ALL_FORMATS if grouped.get(f)]):
            forbid = set()

        chosen_fmt = choose_format(
            topic, grouped,
            recent_formats=recent_formats + used_formats,
            forbid=forbid,
        )
        bucket = list(grouped.get(chosen_fmt) or [])
        fresh = [
            t for t in bucket
            if str(t.get("id")) not in recent_ids
            and str(t.get("id")) not in used_ids
        ]
        if not fresh:
            # Drop cooldown for this batch slot, but keep within-batch dedup.
            fresh = [t for t in bucket if str(t.get("id")) not in used_ids]
        if not fresh:
            # Bucket genuinely empty after dedup — pick any unused template.
            fresh = [
                t for t in templates if str(t.get("id")) not in used_ids
            ]
            if not fresh:
                break  # asked for more memes than we have templates
            chosen_fmt = get_format(fresh[0])

        # LRU-bounded LLM pick — restricts the LLM's candidate pool to
        # the least-recently-used slice of ``fresh`` so iconic templates
        # don't keep re-surfacing every few days. Falls through to the
        # LRU template directly on any LLM failure.
        last_used = history.last_used_timestamps()
        candidate_pool = _lru_candidate_pool(fresh, last_used=last_used)
        llm_pick = pick_template_via_llm(topic=topic, candidates=candidate_pool)
        pick = llm_pick if llm_pick is not None else candidate_pool[0]
        picks.append((pick, chosen_fmt))
        used_ids.add(str(pick.get("id")))
        used_formats.append(chosen_fmt)

    return picks
