"""The Schedule view — the heats still to swim, as many as fit whole.

Settings → Displays → TVs can put a TV on Schedule: the phones' Schedule tab
(docs/app.md §5) for a hall screen. It starts at the heat after the one on the
console — the first of `next_heats`, so heats already swum and the one in the
water are not shown — and adds each following heat only if the whole of it fits:
a heat cut off at the bottom of the screen reads as a heat with fewer swimmers.
Nothing scrolls.

The heats come from ``GET /schedule.json`` (docs/api.md §5.8), fetched again on
every `next_heats`, which the server sends on each heat change and whenever the
meet changes.

The pure half — which heats, and how they are drawn as lines — is Qt-free so CI
can test it without PySide6; the widget only paints those lines.
"""

# Lines the view is divided into, a heat's title counting as one and each of its
# swimmers as one. Fixed for a pool rather than fitted to what is on screen, so the
# text is one size whatever is shown and a short heat does not suddenly read larger
# than a long one — see lines_for.
LINES = 20


def lines_for(num_lanes: int) -> int:
    """Lines for a pool of *num_lanes*: room for three full heats up to 6 lanes and
    two above, never fewer than `LINES` — 21 for 6 lanes, 20 for 8 (two full heats
    and a short third), 26 for 12.

    Everything is a share of the screen's height, so what this costs is set by the
    TV's size, not its resolution. On a 55" TV, 20 lines draw capitals about 14 mm
    high and 26 lines about 11 mm; a hall reads those from roughly 4 m and 3 m.
    """
    try:
        lanes = max(1, int(num_lanes))
    except (TypeError, ValueError):
        lanes = 8
    heats = 3 if lanes <= 6 else 2
    return max(LINES, heats * (lanes + 1))


def display_time(text) -> str:
    """`00:01:02.40` → `01:02.40`, as the Schedule page shows a seed time."""
    text = str(text or "")
    return text[3:] if text.startswith("00:") and len(text) > 8 else text


def swimmers(heat: dict) -> list:
    """The lanes of *heat* with someone in them, in lane order."""
    lanes = [lane for lane in heat.get("lanes") or [] if isinstance(lane, dict)]
    return [lane for lane in lanes if str(lane.get("name") or "").strip()]


def heat_lines(heat: dict) -> int:
    """Lines *heat* takes: its title, then one per swimmer."""
    return 1 + len(swimmers(heat))


def upcoming(heats: list, start) -> list:
    """*heats* from the one keyed *start* (event, heat) on — ``[]`` without it."""
    if start is None:
        return []
    key = (str(start[0]), str(start[1]))
    for index, heat in enumerate(heats):
        if (str(heat.get("event")), str(heat.get("heat"))) == key:
            return heats[index:]
    return []


def fit(heats: list, lines: int = LINES) -> list:
    """The first heat, then each one after it while the whole of it fits.

    In order and stopping at the first that does not: skipping it for a shorter one
    further down would show the hall a heat out of its turn.
    """
    shown, used = [], 0
    for heat in heats:
        need = heat_lines(heat)
        if shown and used + need > lines:
            break
        shown.append(heat)
        used += need
    return shown


def layout(heats: list, lane_direction: str = "up") -> list:
    """The lines to draw, top to bottom: ``("title", heat)`` or ``("lane", lane,
    index)``, where *index* counts the heats for their alternating background.
    With the last lane on top, a heat's swimmers are listed that way round too."""
    out = []
    for index, heat in enumerate(heats):
        out.append(("title", heat, index))
        lanes = swimmers(heat)
        if lane_direction == "down":
            lanes = list(reversed(lanes))
        out.extend(("lane", lane, index) for lane in lanes)
    return out
