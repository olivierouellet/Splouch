from .base import SerialConfig
from .rtd import RtdSwimmingDecoder


class Omnisport2000Decoder(RtdSwimmingDecoder):
    """Decoder for Daktronics Omnisport 2000 timing consoles (RTD port, J5).

    Serial protocol: 19200 baud, 8-N-1, Daktronics RTD (see rtd.py). The buffer
    layout is Daktronics' own Input Template File for this sport mode,
    OS2-Swimming.itf: header prefix "004210", result lines from offset 222.

    A line is a row on the board, not a lane: the console may sort lines by place,
    so each one carries its own lane number, and only that is trusted.

    See console_decoders/omnisport_2000_serial.md for the full protocol reference.
    """

    HEADER_PREFIX = "004210"
    LINE_BASE = 222

    def __init__(self, cfg: dict):
        self._serial_config = SerialConfig(
            baud=19200, bytesize=8, parity="N", stopbits=1
        )
        super().__init__(cfg)

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config
