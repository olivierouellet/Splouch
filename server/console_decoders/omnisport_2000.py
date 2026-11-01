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
            key="udp_port",
            label="RTD network port",
            choices=(
                ("21000", "21000 (OMNI1)"),
                ("21001", "21001 (OMNI2)"),
                ("21002", "21002 (OMNI3)"),
                ("21003", "21003 (OMNI4)"),
                ("3002", "3002 (Galaxy)"),
            ),
            default="21000",
            help="The console's Setup Network → RTD Port # setting (menu 4-7-5). "
            "Custom: 1024 to 65535.",
            # The console's 2-CUST takes any port; below 1024 the server, not
            # running as root, could not listen anyway.
            custom_range=(1024, 65535),
        ),
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
        # The Ethernet entry presets "udp": the console broadcasts RTD datagrams.
        self.transport = str(cfg.get("transport", "serial"))
        port = str(cfg.get("udp_port", "21000"))
        self._udp_port = int(port) if port.isdigit() else 21000
        self._serial_config = serial_from(
            cfg.get("serial_baud", 19200), str(cfg.get("serial_format", "8N1"))
        )

    @property
    def udp_port(self) -> int:
        return self._udp_port

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config
