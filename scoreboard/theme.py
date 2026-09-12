"""Theme + config helpers.

``GET /config`` returns exactly what ``web._globals()`` injects into the browser
templates, so the Qt display themes itself from the same settings the operator
edits in the admin UI — no second source of truth.

The defaults here are a *fallback only*, for the seconds between kiosk boot and
the server answering. They intentionally duplicate ``state.DEFAULT_THEME_*``
rather than importing it: the display is a remote client of the API, not a
sibling of the server package.
"""
from .fonts import resolve_family

DEFAULT_COLORS = {
    'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
    'header_label': '#ffffff', 'header_value': '#e0e0e0',
    'th_text': '#666666', 'th_bg': '#1a1a1a',
    'row_odd': '#141414', 'row_even': '#202020', 'row_text': '#e0e0e0',
    'time': '#FFD700', 'delta_better': '#4CAF50', 'delta_worse': '#808080',
    'podium_gold': '#545454', 'podium_silver': '#424242', 'podium_bronze': '#343434',
    'connection_lost': '#ef5350', 'connection_lost_text': '#0d0d0d',
    'schedule_event': '#3b9eff', 'schedule_time': '#FFD700',
    'schedule_name': '#e0e0e0', 'schedule_club': '#666666',
}

DEFAULT_FONTS = {
    'family': 'Overpass Mono', 'digits': 'DSEG7Classic', 'timing': 'Overpass Mono',
}

# Lane and place are short here and stay short whatever the label style is — they are
# the two narrow columns (docs/app.md `T-09`, server `state.STYLED_LABEL_KEYS`).
DEFAULT_LABELS = {
    'event': 'EVENT', 'heat': 'HEAT', 'lane': 'LN', 'place': 'PL',
    'time': 'TIME', 'name': 'NAME', 'club': 'CLUB', 'delta': 'DELTA',
    'chrono': 'CHRONO',
}

# Status messages, from the locale file's [display] section. Translated by the
# server per Settings → Display → Scoreboard language; these English values are
# the floor for a first-ever boot with no cached config yet.
DEFAULT_STRINGS = {
    'waiting_server':  'Waiting for the timing server',
    # A dropped link is badged, never a full-screen message, so this is pill-sized
    # rather than a sentence. Only a cold boot — where there is nothing to cover —
    # takes the whole screen, and that uses `waiting_server`.
    'connection_lost': '⚠ CONNECTION LOST',
    'retrying':        'retrying',
    'test_session':    '⚠ TEST SESSION',
}

# Column visibility flags, and the header-label flags that are independent of them
# (the browser hides a column and its header separately — mirror that).
_SHOW_FLAGS = (
    'show_name', 'show_club', 'show_delta', 'show_position', 'show_podium',
    'show_lane_header', 'show_name_header', 'show_club_header',
    'show_time_header', 'show_delta_header', 'show_position_header',
)


class Config:
    """Normalised view of ``GET /config`` with defaults filled in.

    Every field the board reads goes through here, so a server that predates a
    given key (or a first-boot fallback with no server at all) degrades to a
    sensible default instead of raising mid-render.
    """

    def __init__(self, raw: dict | None = None):
        raw = raw or {}
        self.raw        = raw
        self.meet_title = raw.get('meet_title', '') or ''
        self.num_lanes  = max(1, min(12, int(raw.get('num_lanes', 8) or 8)))
        self.locale     = raw.get('locale') or 'en'
        self.colors     = {**DEFAULT_COLORS,  **(raw.get('theme_colors')    or {})}
        self.fonts      = {**DEFAULT_FONTS,   **(raw.get('theme_fonts')     or {})}
        self.labels     = {**DEFAULT_LABELS,  **(raw.get('labels')          or {})}
        self.strings    = {**DEFAULT_STRINGS, **(raw.get('display_strings') or {})}
        # Carousel overlay (Settings → Display → Splash Screen).
        self.carousel_images = list(raw.get('carousel_images') or [])
        self.carousel_interval = max(1, int(raw.get('carousel_interval') or 10))
        for flag in _SHOW_FLAGS:
            setattr(self, flag, bool(raw.get(flag, True)))

    def color(self, key: str) -> str:
        return self.colors.get(key) or DEFAULT_COLORS.get(key, '#ffffff')

    @property
    def family(self) -> str:
        return resolve_family(self.fonts.get('family') or DEFAULT_FONTS['family'])

    @property
    def timing_family(self) -> str:
        """Font for lane times/deltas (Settings → Display → Theme → Timing Font)."""
        timing = self.fonts.get('timing')
        return resolve_family(timing) if timing else self.family

    @property
    def digits_family(self) -> str:
        """Font for the running clock (Settings → Display → Theme → Digit Font).

        Separate from ``timing_family`` because the browser treats them
        separately: the live chrono is usually a seven-segment face (DSEG7) while
        lane times stay legible in a mono face. Matching that split keeps the TV
        looking like the page it replaced.
        """
        digits = self.fonts.get('digits')
        return resolve_family(digits) if digits else self.timing_family
