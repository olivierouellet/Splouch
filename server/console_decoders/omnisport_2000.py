from .base import SerialConfig
from .rtd import RTD_OFFSET_OPTION, RtdSwimmingDecoder


class Omnisport2000Decoder(RtdSwimmingDecoder):
    """Decoder for Daktronics Omnisport 2000 timing consoles (RTD port, J5).

    Serial protocol: 19200 baud, 8-N-1, Daktronics RTD (see rtd.py). The buffer
    layout is Daktronics' own Input Template File for this sport mode,
    OS2-Swimming.itf: header "004" "21", result lines from offset 222 — or 200 when
    the console's Results port is set to OMNI6000, which the console entry presets
    as `rtd_line_base`.

    A line is a row on the board, not a lane: the console may sort lines by place,
    so each one carries its own lane number, and only that is trusted.

    See console_decoders/omnisport_2000_serial.md for the full protocol reference.
    """

    SUB_CODES = ("21",)
    LINE_BASE = 222
    OPTIONS = (RTD_OFFSET_OPTION,)

    def __init__(self, cfg: dict):
        self._serial_config = SerialConfig(
            baud=19200, bytesize=8, parity="N", stopbits=1
        )
        super().__init__(cfg)

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config
