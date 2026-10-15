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


_LANE_LETTERS = "ABC"  # lanes 10, 11, 12 on a board without two-digit lanes


def line_row(text: str, own_row: int, first_lane: int, rows: int) -> int:
    """The row a CTS scoreboard line belongs to, from the lane number it shows.

    A CTS line is a lane's in lane order, but a place's when the timer posts by
    place (Post Race by Place, Display Results in Place Order, Lane Module Order):
    line 1 then holds the winner, with the winner's lane in its lane digits. The
    digits decide; a blank or unreadable lane keeps the line's own row, as some
    consoles send no lane digit at all.

    A digit that can be the line's own lane keeps it there: a System 6 emulating a
    ten-lane board shows lane 11 as `1` and lane 10 as `0`, as `B` and `A` without
    it. So only a pool of 11-12 lanes posting by place with that emulation stays
    ambiguous — `1` reads as lane 1.
    """
    t = text.strip().upper()
    if not t:
        return own_row
    own_lane = own_row + first_lane - 1
    own = {str(own_lane), str(own_lane % 10)}
    if 10 <= own_lane <= 12:
        own.add(_LANE_LETTERS[own_lane - 10])
    if t in own:
        return own_row
    if t in _LANE_LETTERS:
        lane = 10 + _LANE_LETTERS.index(t)
    elif t.isdigit():
        lane = int(t)
        if lane == 0 and first_lane == 1:
            lane = 10
    else:
        return own_row
    row = lane - first_lane + 1
    return row if 1 <= row <= rows else own_row


_PLACES = {"", *map(str, range(1, 13)), "0", "A", "B", "C"}


def is_swim_line(mmsshh: str, place: str, running: bool) -> bool:
    """Can a CTS lane line be a swim, or is it a test the timer put on the board?

    *mmsshh* is the line's six time digits as shown (`' '` blank), *place* its place
    digit(s). The timer fills the board with patterns no swim can make: Self Test
    lights every segment (`88:88.88`, place 8), Show Definitions puts each module's
    own address in its hundredths (`01`, `17`), and Gen7 diagnostics write letters
    (`P`, `A`, `n`, `F`, `E`) in the place and time. Read as results they hold a
    heat open, or move to the lane their `8` names.

    So: no letter in the time, seconds under 60, a place a pool can have, and a
    stopped lane at least a second — a racing lane ticks through its first second.
    """
    if len(mmsshh) != 6 or any(c not in "0123456789 " for c in mmsshh):
        return False
    if int(mmsshh[2:4].replace(" ", "0")) >= 60:
        return False
    if place.strip().upper() not in _PLACES:
        return False
    return running or not (mmsshh[4:].strip() and not mmsshh[:4].strip())


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
