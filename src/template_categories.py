"""Template categorization — joke-format taxonomy for the trending pool.

Every Imgflip template (and any web-sourced extras) is mapped to a *format*
that describes the joke shape it carries best. The matcher uses this so we
pick the template AFTER deciding what kind of joke we want to tell — not the
other way around.

## Formats

- ``comparison``    — A vs B, two-column or two-state contrast
                      (Drake, Two Buttons, Buff Doge vs Cheems, …).
- ``escalation``    — multi-tier evolution / ascending levels
                      (Expanding Brain, Panik Kalm Panik, Domino Effect, …).
- ``reaction``      — single emotional response to a stimulus
                      (This Is Fine, Surprised Pikachu, Hide The Pain Harold, …).
- ``labeling``      — labels stuck on parts of a scene
                      (Distracted Boyfriend, Running Away Balloon, …).
- ``declaration``   — bold statement / sign / proclamation
                      (Change My Mind, X X Everywhere, One Does Not Simply, …).
- ``confrontation`` — yelling, slapping, accusing, mocking
                      (Woman Yelling At Cat, Batman Slapping Robin, Mocking Spongebob).
- ``multi_panel``   — sequential 4-panel narrative
                      (Anakin Padme, Gru's Plan, American Chopper Argument, …).

A single template can fit several formats; we pick the dominant one.
Anything not in ``TEMPLATE_FORMATS`` falls back to ``classify_by_name`` —
fuzzy keyword detection on the template name — and finally to ``reaction``.
"""
from __future__ import annotations

from typing import Any, Iterable

# Canonical format list — used by the matcher to rotate.
ALL_FORMATS: tuple[str, ...] = (
    "comparison",
    "escalation",
    "reaction",
    "labeling",
    "declaration",
    "confrontation",
    "multi_panel",
)

FORMAT_DESCRIPTIONS: dict[str, str] = {
    "comparison":    "A-vs-B or before/after contrast",
    "escalation":    "ascending tiers / evolving levels",
    "reaction":      "single emotional reaction shot",
    "labeling":      "labels stuck on parts of a scene",
    "declaration":   "bold statement, sign, or proclamation",
    "confrontation": "yelling, accusing, slapping, denying",
    "multi_panel":   "sequential 4-panel narrative",
}

# Hand-curated format mapping for every template currently in Imgflip's top 100
# (as of 2026-05). Add more as new templates show up — `classify_by_name` is
# the safety net for anything not pinned here.
TEMPLATE_FORMATS: dict[str, str] = {
    # --- comparison ---
    "181913649": "comparison",      # Drake Hotline Bling
    "87743020":  "comparison",      # Two Buttons (dilemma)
    "124822590": "comparison",      # Left Exit 12 Off Ramp
    "247375501": "comparison",      # Buff Doge vs. Cheems
    "178591752": "comparison",      # Tuxedo Winnie The Pooh
    "180190441": "comparison",      # They're The Same Picture
    "354700819": "comparison",      # Two guys on a bus
    "309668311": "comparison",      # Two Paths
    "129315248": "comparison",      # No - Yes (sleeping/woke variant)
    "99683372":  "comparison",      # Sleeping Shaq
    "110133729": "comparison",      # spiderman pointing at spiderman
    "533936279": "comparison",      # Bell Curve (3-tier)

    # --- escalation ---
    "93895088":  "escalation",      # Expanding Brain
    "226297822": "escalation",      # Panik Kalm Panik
    "162372564": "escalation",      # Domino Effect
    "195515965": "escalation",      # Clown Applying Makeup

    # --- reaction ---
    "97984":     "reaction",        # Disaster Girl
    "80707627":  "reaction",        # Sad Pablo Escobar
    "4087833":   "reaction",        # Waiting Skeleton
    "55311130":  "reaction",        # This Is Fine
    "124055727": "reaction",        # Y'all Got Any More Of That
    "148909805": "reaction",        # Monkey Puppet
    "505705955": "reaction",        # Absolute Cinema
    "177682295": "reaction",        # You Guys are Getting Paid
    "370867422": "reaction",        # Megamind peeking
    "67452763":  "reaction",        # Squidward window
    "27813981":  "reaction",        # Hide the Pain Harold
    "259237855": "reaction",        # Laughing Leo
    "61520":     "reaction",        # Futurama Fry
    "119139145": "reaction",        # Blank Nut Button
    "155067746": "reaction",        # Surprised Pikachu
    "5496396":   "reaction",        # Leonardo Dicaprio Cheers
    "166969924": "reaction",        # Flex Tape
    "316466202": "reaction",        # where monkey
    "114585149": "reaction",        # Inhaling Seagull
    "208915813": "reaction",        # George Bush 9/11
    "101288":    "reaction",        # Third World Skeptical Kid
    "61556":     "reaction",        # Grandma Finds The Internet
    "21735":     "reaction",        # The Rock Driving
    "221578498": "reaction",        # Grant Gustin over grave
    "101956210": "reaction",        # Whisper and Goosebumps
    "123999232": "reaction",        # The Scroll Of Truth
    "61544":     "reaction",        # Success Kid
    "371619279": "reaction",        # Megamind no bitches
    "145139900": "reaction",        # Scooby doo mask reveal
    "50421420":  "reaction",        # Disappointed Black Guy
    "247756783": "reaction",        # patrick to do list actually blank
    "6235864":   "reaction",        # Finding Neverland

    # --- labeling ---
    "112126428": "labeling",        # Distracted Boyfriend
    "131087935": "labeling",        # Running Away Balloon
    "135256802": "labeling",        # Epic Handshake
    "79132341":  "labeling",        # Bike Fall
    "100777631": "labeling",        # Is This A Pigeon
    "252758727": "labeling",        # Mother Ignoring Kid Drowning In A Pool
    "110163934": "labeling",        # I Bet He's Thinking About Other Women
    "206151308": "labeling",        # Spider Man Triple
    "171305372": "labeling",        # Soldier protecting sleeping child
    "234202281": "labeling",        # AJ Styles & Undertaker
    "284929871": "labeling",        # They don't know
    "187102311": "labeling",        # Three-headed Dragon
    "247113703": "labeling",        # A train hitting a school bus
    "224514655": "labeling",        # Anime Girl Hiding from Terminator
    "119215120": "labeling",        # Types of Headaches meme
    "142009471": "labeling",        # is this butterfly
    "104893621": "labeling",        # Grim Reaper Knocking Door

    # --- declaration ---
    "217743513": "declaration",     # UNO Draw 25 Cards
    "222403160": "declaration",     # Bernie Asking For Support
    "224015000": "declaration",     # Bernie Sanders Once Again Asking
    "252600902": "declaration",     # Always Has Been
    "91538330":  "declaration",     # X, X Everywhere
    "129242436": "declaration",     # Change My Mind
    "309868304": "declaration",     # Trade Offer
    "101470":    "declaration",     # Ancient Aliens
    "61579":     "declaration",     # One Does Not Simply
    "3218037":   "declaration",     # Trophy If I Had One
    "427308417": "declaration",     # 0 days without
    "161865971": "declaration",     # Marked Safe From
    "28251713":  "declaration",     # Oprah You Get A
    "77045868":  "declaration",     # Pawn Stars Best I Can Do
    "163573":    "declaration",     # Imagination Spongebob
    "137501417": "declaration",     # Friendship ended
    "216523697": "declaration",     # All My Homies Hate
    "29617627":  "declaration",     # Look At Me
    "14371066":  "declaration",     # Star Wars Yoda
    "91545132":  "declaration",     # Trump Bill Signing
    "101716":    "declaration",     # Yo Dawg Heard You
    "72525473":  "declaration",     # say the line bart!
    "92084495":  "declaration",     # Charlie Conspiracy
    "29562797":  "declaration",     # I'm The Captain Now

    # --- confrontation ---
    "188390779": "confrontation",   # Woman Yelling At Cat
    "102156234": "confrontation",   # Mocking Spongebob
    "438680":    "confrontation",   # Batman Slapping Robin
    "84341851":  "confrontation",   # Evil Kermit
    "135678846": "confrontation",   # Who Killed Hannibal
    "360597639": "confrontation",   # when i'm in a competition...

    # --- multi_panel ---
    "322841258": "multi_panel",     # Anakin Padme 4 Panel
    "131940431": "multi_panel",     # Gru's Plan
    "1035805":   "multi_panel",     # Boardroom Meeting Suggestion
    "134797956": "multi_panel",     # American Chopper Argument
}


# Keyword → format hints for templates not pinned in TEMPLATE_FORMATS.
# Order matters: first hit wins, so put the more specific patterns first.
_NAME_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("vs.", " vs ", "vs", "two paths", "two buttons", "no - yes"), "comparison"),
    (("expanding brain", "galaxy brain", "panik", "domino"),         "escalation"),
    (("yelling", "slapping", "mocking", "argument", "evil kermit"),  "confrontation"),
    (("4 panel", "4-panel", "anakin padme", "boardroom"),            "multi_panel"),
    (("change my mind", "one does not simply", "i am once again",
      "everywhere", "trade offer", "always has been", "say the line",
      "scroll of truth", "captain now"),                              "declaration"),
    (("distracted boyfriend", "running away", "is this a pigeon",
      "bike fall", "epic handshake"),                                 "labeling"),
)


def classify_by_name(name: str) -> str:
    """Best-effort format guess from a template name (used as a fallback)."""
    if not name:
        return "reaction"
    n = name.lower()
    for keywords, fmt in _NAME_HINTS:
        if any(k in n for k in keywords):
            return fmt
    return "reaction"


def get_format(template: dict[str, Any]) -> str:
    """Return the joke format for an Imgflip / web template record."""
    tid = str(template.get("id") or "")
    if tid in TEMPLATE_FORMATS:
        return TEMPLATE_FORMATS[tid]
    explicit = template.get("format")
    if explicit:
        return str(explicit)
    return classify_by_name(template.get("name") or "")


def templates_by_format(
    templates: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group templates by format. Empty buckets are still returned (as []) so
    callers can rotate through formats deterministically."""
    out: dict[str, list[dict[str, Any]]] = {fmt: [] for fmt in ALL_FORMATS}
    for t in templates:
        fmt = get_format(t)
        out.setdefault(fmt, []).append(t)
    return out


# --------------------------------------------------------------------------- #
# Topic → suggested format heuristic                                           #
# --------------------------------------------------------------------------- #
# Light keyword routing. The matcher consults this first; if nothing fires it
# falls back to "rotate to whichever format hasn't been used recently".
_TOPIC_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("vs", "versus", "instead of", "or ", "old vs new", "before vs after",
      "junior vs senior"),                                               "comparison"),
    (("level up", "evolve", "step by step", "tier list", "ascend",
      "galaxy brain", "next level", "enlightenment"),                    "escalation"),
    (("hype", "drama", "argue", "fight", "called out", "callout",
      "yell", "slap"),                                                   "confrontation"),
    (("rant", "manifesto", "controversial", "hot take", "unpopular",
      "thesis"),                                                         "declaration"),
    (("cast of characters", "who's who", "label", "scene",
      "the real", "starring"),                                           "labeling"),
    (("plot twist", "story", "journey", "saga", "four-panel",
      "4-panel", "step 1 step 2"),                                       "multi_panel"),
)


def suggest_format(topic: str) -> str | None:
    """Heuristic: look at the topic text and suggest a format.

    Returns ``None`` when nothing fires — caller should rotate or random-pick.
    """
    if not topic:
        return None
    t = topic.lower()
    for keywords, fmt in _TOPIC_HINTS:
        if any(k in t for k in keywords):
            return fmt
    return None
