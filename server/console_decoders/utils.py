import re


def split_step(touchpad_sides) -> int:
    """Lengths one counted split is worth, from Settings → Meet → Touchpads.

    A property of the **pool**, not of the console: a swimmer who does not touch a
    pad cannot be seen by any of them. With pads at one end only there are four
    observations in a 200m whatever brand is on the deck, so the count moves in twos
    — which is why `splits + split_step >= expected_splits` is the last-length test
    rather than `splits + 1`.

    Lives here, beside the decoders, so the CTS Gen6's own inference (`feed`) and the
    value published to every client (`worker._on_event_changed`) cannot drift. A
    decoder cannot reach `state` — `state` imports *it* — so the rule is a pure
    function of the setting and each caller supplies it.
    """
    return 2 if int(touchpad_sides or 1) == 1 else 1


def first_lane_of(cfg: dict) -> int:
    """The pool's first lane number (Settings → Meet → Pool): 0 or 1."""
    try:
        return 0 if int(cfg.get("first_lane", 1)) == 0 else 1
    except (TypeError, ValueError):
        return 1


def lane_digit_row(c: str, first_lane: int, zero_is_ten: bool = True) -> int:
    """Board row for a one-character lane on the wire, 0 when it is not a lane.

    In a pool numbered from lane 0, `0` is lane 0. Otherwise `0` is lane 10 — the
    usual way a one-character field shows the tenth lane — unless *zero_is_ten* is
    off. A pool numbered 0-10 cannot be told apart in one character.
    """
    c = c.strip()
    if not c.isdigit():
        return 0
    lane = int(c)
    if lane == 0 and first_lane == 1:
        if not zero_is_ten:
            return 0
        lane = 10
    return lane - first_lane + 1


def parse_time_hundredths(s: str):
    """Parse a time string to integer hundredths of a second.

    Handles:
      SS.HH          →  e.g. "58.21"
      M:SS.HH        →  e.g. "0:58.21"
      HH:MM:SS.hh    →  e.g. "00:00:58.21"  (Lenex entrytime format)

    Returns None for empty, zero, or unparseable input.
    """
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$", s)
    if m:
        val = (
            int(m.group(1)) * 360000
            + int(m.group(2)) * 6000
            + int(m.group(3)) * 100
            + int(m.group(4))
        )
        return val if val > 0 else None
    m = re.match(r"^(?:(\d+):)?(\d{1,2})\.(\d{2})$", s)
    if not m:
        return None
    val = (
        (int(m.group(1)) if m.group(1) else 0) * 6000
        + int(m.group(2)) * 100
        + int(m.group(3))
    )
    return val if val > 0 else None
