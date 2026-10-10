import re

from .base import SerialConfig
from .rtd import RtdSwimmingDecoder

_EVENT_TITLE = (69, 30)


def _parse_event_heat(data: str) -> tuple[int, int]:
    ev_m = re.search(r"[Ee]vent\s+(\d+)", data)
    ht_m = re.search(r"[Hh]eat\s+(\d+)", data)
    ev = int(ev_m.group(1)) if ev_m else 0
    ht = int(ht_m.group(1)) if ht_m else 0
    return ev, ht


class Ares21Decoder(RtdSwimmingDecoder):
    """Decoder for Swiss Timing Omega Ares 21 timing consoles.

    Serial protocol: 9600 baud, 8-N-1, RS-485. The Ares feeds Daktronics "Venus"
    display controllers ("Venus ERTD"), i.e. Daktronics RTD — see rtd.py.

    What is known, from fvishram/SRAYSScoreboard (AresDataHandler.cs, MIT): header
    prefix "004010"; running time at 0; event title at 69; swimmer names at
    200 + 36·(n−1) and results 20 characters further on. Every one of those matches
    the Daktronics swimming template's shape (OS2-Swimming.itf), so the result line
    is read with the template's layout, starting at 200. That part is **inferred**,
    hence the two fallbacks:

    * a line with no lane number is lane n, as SRAYS assumes;
    * a line whose place/time are not at the template offsets is searched for a time
      and the number before it, as this decoder did before (`1 00:54.32`).

    Event/heat: the template's number fields (99, 103) when they hold digits, else
    "Event N … Heat N" in the title text, which is what SRAYS parses. Swimmer names
    are ignored — Lenex supplies them.

    See console_decoders/swiss_timing_ares21_serial.md.
    """

    HEADER_PREFIX = "004010"
    LINE_BASE = 200
    LINE_IS_LANE = True
    RESULT_FALLBACK = True

    def __init__(self, cfg: dict) -> None:
        self._serial_config = SerialConfig(
            baud=9600, bytesize=8, parity="N", stopbits=1
        )
        super().__init__(cfg)

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config

    def _event_heat(self, touched) -> tuple[int, int] | None:
        numbers = super()._event_heat(touched)
        if numbers and numbers[0] > 0 and numbers[1] > 0:
            return numbers
        if touched(*_EVENT_TITLE):
            return _parse_event_heat(self._field(*_EVENT_TITLE))
        return numbers
