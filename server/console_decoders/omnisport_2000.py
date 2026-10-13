from .base import DecoderOption, SerialConfig, serial_from
from .rtd import (
    RTD_LINE_BASE_OPTION,
    RTD_OFFSET_OPTION,
    RTD_SUB_CODE_OPTION,
    RtdSwimmingDecoder,
)


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
    OPTIONS = (
        RTD_OFFSET_OPTION,
        RTD_LINE_BASE_OPTION,
        RTD_SUB_CODE_OPTION,
        DecoderOption(
            key="serial_baud",
            label="Baud rate",
            choices=(("9600", "9600"), ("19200", "19200"), ("38400", "38400")),
            default="19200",
            help="19200 on an OmniSport. Undocumented for other consoles sending RTD.",
        ),
        DecoderOption(
            key="serial_format",
            label="Serial format",
            choices=(("8N1", "8-N-1"), ("7E1", "7-E-1")),
            default="8N1",
            help="If nothing decodes, try the other format.",
        ),
    )

    def __init__(self, cfg: dict):
        self._serial_config = SerialConfig(
            baud=19200, bytesize=8, parity="N", stopbits=1
        )
        super().__init__(cfg)

    def configure(self, cfg: dict) -> None:
        super().configure(cfg)
        self._serial_config = serial_from(
            cfg.get("serial_baud", 19200), str(cfg.get("serial_format", "8N1"))
        )

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config
