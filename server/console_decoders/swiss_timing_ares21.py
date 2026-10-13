import re

from .base import DecoderOption, SerialConfig
from .rtd import RtdSwimmingDecoder

_EVENT_TITLE = (69, 30)

# Where the ARES writes the event number: 3 wide at 99, right after the 30-char
# title, or at 100 in a 5-char event+heat block with the heat at 103. Which one a
# given scoreboard set-up uses is not known, hence the option.
_EVENT_FIELDS = {"99": (99, 3), "100": (100, 3)}


def _parse_event_heat(data: str) -> tuple[int, int]:
    ev_m = re.search(r"[Ee]vent\s+(\d+)", data)
    ht_m = re.search(r"[Hh]eat\s+(\d+)", data)
    ev = int(ev_m.group(1)) if ev_m else 0
    ht = int(ht_m.group(1)) if ht_m else 0
    return ev, ht


class Ares21Decoder(RtdSwimmingDecoder):
    """Decoder for the Swiss Timing Omega ARES 21 "Venus ERTD" scoreboard output.

    Serial protocol: 9600 baud, 8-N-1, RS-485, IF-ARES SCB connector. ERTD is the
    ARES scoreboard kind "designed to drive DAKTRONICS scoreboards running with the
    VENUS software" (ARES Swimming manual 3330.560.02 §11), i.e. Daktronics RTD —
    see rtd.py.

    Layout of the ARES Swimming ERTD scoreboard set-up (vertical offsets
    200;225;225;108;200;69, line strides 36;36;36;23;36;30): running time at 0,
    title at 69, event at 99, records at 108 + 23·n, result lines at 200 + 36·n —
    the OmniSport template shifted by −22 from offset 105 on, which is the
    OmniSport's own OMNI6000 layout. Header "004" + "01" (or "00").

    Inside a line the ARES puts the name block (20), lane (2) at +20, rank (3) at
    +22, a space, then the time from +26 — read by the template's (25, 9) field
    after stripping. It sends no lengths, so the line never moves a lap count. Two
    fallbacks stay:

    * a line with no lane number is lane n;
    * a line whose place/time are not at the template offsets is searched for a time
      and the number before it (`1 00:54.32`).

    Event/heat: see `ertd_event_field`; a blank number falls back to "Event N …
    Heat N" in the title text. Swimmer names are ignored — Lenex supplies them.

    See console_decoders/swiss_timing_ares21_serial.md.
    """

    SUB_CODES = ("01", "00")
    LINE_BASE = 200
    LINE_IS_LANE = True
    RESULT_FALLBACK = True

    OPTIONS = (
        DecoderOption(
            key="ertd_event_field",
            label="Event number position",
            choices=(
                ("99", "After the title (99–101)"),
                ("100", "Event/heat block (100–102)"),
                ("title", "Only from the title text"),
            ),
            default="99",
            help="Where this ARES writes the event number. Change it if the event "
            "shown is wrong (e.g. 1 instead of 12).",
        ),
    )

    def __init__(self, cfg: dict) -> None:
        self._serial_config = SerialConfig(
            baud=9600, bytesize=8, parity="N", stopbits=1
        )
        super().__init__(cfg)

    def configure(self, cfg: dict) -> None:
        super().configure(cfg)
        self.event_field = str(cfg.get("ertd_event_field", "99"))

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config

    def _event_heat(self, touched) -> tuple[int, int] | None:
        field = _EVENT_FIELDS.get(self.event_field)
        if field is not None and (touched(*field) or touched(*self.HEAT_NUMBER)):
            numbers = (
                int(d) if (d := self._field(*field).strip()).isdigit() else 0,
                int(h)
                if (h := self._field(*self.HEAT_NUMBER).strip()).isdigit()
                else 0,
            )
            if numbers[0] > 0 and numbers[1] > 0:
                return numbers
        if touched(*_EVENT_TITLE):
            return _parse_event_heat(self._field(*_EVENT_TITLE))
        return None
