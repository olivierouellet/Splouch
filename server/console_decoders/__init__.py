import importlib.util
import os
import sys
from dataclasses import dataclass, field

from .base import ConsoleDecoder, DecoderOption
from .cts_gen6 import CTSGen6Decoder
from .cts_gen7 import CTSGen7Decoder
from .dolphin import DolphinDecoder
from .manual import ManualDecoder
from .omnisport_2000 import Omnisport2000Decoder
from .quantum import QuantumDecoder
from .swiss_scoreboard import SwissScoreboardDecoder
from .swiss_timing_ares21 import Ares21Decoder

# One class per wire protocol. Several consoles — and several outputs of one
# console — share a class; what differs between them is the preset and the options
# their `CONSOLES` entry gives it.
DECODERS: dict[str, type[ConsoleDecoder]] = {
    "cts_legacy": CTSGen6Decoder,
    "cts_gen7": CTSGen7Decoder,
    "cts_dolphin": DolphinDecoder,
    "dak_rtd": Omnisport2000Decoder,
    "ares_ertd": Ares21Decoder,
    "swiss_scb": SwissScoreboardDecoder,
    "swiss_osm6": QuantumDecoder,
    "manual": ManualDecoder,
}


@dataclass(frozen=True)
class ConsoleEntry:
    """One choice in Settings → Timing: a console model and one of its outputs.

    `key` is what settings.json stores as `console_type` and what the relay reports.
    `model` is the first menu, `output` the second ('' for a console with one way
    out). `preset` is fixed decoder configuration this output implies — the
    OmniSport's line base, say — and `options` the `DecoderOption` keys its menu
    shows, each with this console's default (None keeps the decoder's).
    """

    key: str
    model: str
    output: str
    decoder: str
    adapter: str
    wiring: str
    protocol: str
    tested: bool
    doc: str
    preset: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.model} — {self.output}" if self.output else self.model


_CTS = "Colorado Timing System"
_DAK = "OmniSport 2000 (Daktronics)"
_ARES = "ARES 21 (Swiss Timing Omega)"


def _cts_legacy(key: str, model: str, output: str = "") -> ConsoleEntry:
    """A CTS console's legacy scoreboard output: one wiring, one protocol."""
    return ConsoleEntry(
        key,
        model,
        output,
        "cts_legacy",
        adapter="USB-to-RS232 (DB9)",
        wiring='1/4" Y-cable — tip → DB9 pin 2 (RX), sleeve → pin 5 (GND)',
        protocol="RS-232 · 9600 baud · 8-E-1",
        tested=True,
        doc="cts-gen6.md",
    )


CONSOLES: list[ConsoleEntry] = [
    _cts_legacy("cts_sys5", f"System 5 ({_CTS})"),
    _cts_legacy("cts_sys6", f"System 6 ({_CTS})"),
    _cts_legacy("cts_gen7_legacy", f"Gen7 ({_CTS})", "Legacy scoreboard output"),
    ConsoleEntry(
        "cts_gen7_serial",
        f"Gen7 ({_CTS})",
        "Gen7 serial output",
        "cts_gen7",
        adapter="USB-to-RS485",
        wiring="Connect to the RS-485 port on the Gen7 console",
        protocol="RS-485 · 115200 baud · 8-N-1",
        tested=False,
        doc="cts-gen7.md",
    ),
    ConsoleEntry(
        "cts_gen7_rs485_legacy",
        f"Gen7 ({_CTS})",
        "RS-485 output set to CTS Legacy",
        "cts_legacy",
        adapter="USB-to-RS485",
        wiring="Blue Square or Black Diamond scoreboard output (RS-485)",
        protocol="RS-485 · 9600 baud · Colorado legacy (unconfirmed)",
        tested=False,
        doc="cts-gen7.md",
        options={"serial_parity": None},
    ),
    ConsoleEntry(
        "cts_gen7_rs485_rtd",
        f"Gen7 ({_CTS})",
        "RS-485 output set to Daktronics RTD",
        "dak_rtd",
        adapter="USB-to-RS485",
        wiring="Blue Square or Black Diamond scoreboard output (RS-485)",
        protocol="RS-485 · Daktronics RTD (layout and serial settings unconfirmed)",
        tested=False,
        doc="cts-gen7.md",
        options={
            "rtd_line_base": None,
            "rtd_sub_code": None,
            "serial_baud": None,
            "serial_format": None,
            "rtd_offset": None,
        },
    ),
    ConsoleEntry(
        "cts_gen7_rs485_omega",
        f"Gen7 ({_CTS})",
        "RS-485 output set to Omega",
        "swiss_scb",
        adapter="USB-to-RS485",
        wiring="Blue Square or Black Diamond scoreboard output (RS-485)",
        protocol="RS-485 · Swiss Timing scoreboard lines (unconfirmed)",
        tested=False,
        doc="cts-gen7.md",
        options={"scb_line_mode": "rank", "serial_baud": None, "serial_format": None},
    ),
    ConsoleEntry(
        "cts_dolphin",
        f"Dolphin ({_CTS})",
        "",
        "cts_dolphin",
        adapter="Network — the PC running the Dolphin software",
        wiring="Dolphin software, Settings → TCP/IP Streaming turned on",
        protocol="TCP/IP · Dolphin streaming API",
        tested=False,
        doc="dolphin.md",
        options={
            "tcp_port": None,
            "dolphin_results_hold": None,
        },
    ),
    ConsoleEntry(
        "dak_os2000_rtd",
        _DAK,
        "J5 RTD — Results set to OMNI2000",
        "dak_rtd",
        adapter="USB-to-RS232 (DB9)",
        wiring="DB9 to the J5 RTD Port (J6 Results is for Meet Manager)",
        protocol="RS-232 · 19200 baud · 8-N-1 · Daktronics RTD",
        tested=False,
        doc="omnisport-2000.md",
        preset={"rtd_line_base": 222},
        options={"rtd_offset": None},
    ),
    ConsoleEntry(
        "dak_os2000_rtd_omni6000",
        _DAK,
        "J5 RTD — Results set to OMNI6000",
        "dak_rtd",
        adapter="USB-to-RS232 (DB9)",
        wiring="DB9 to the J5 RTD Port (J6 Results is for Meet Manager)",
        protocol="RS-232 · 19200 baud · 8-N-1 · Daktronics RTD",
        tested=False,
        doc="omnisport-2000.md",
        preset={"rtd_line_base": 200},
        options={"rtd_offset": None},
    ),
    ConsoleEntry(
        "dak_os2000_cts",
        _DAK,
        "J5 RTD port set to CTS",
        "cts_legacy",
        adapter="USB-to-RS232 (DB9)",
        wiring="DB9 to the J5 RTD Port, console menu RTD Port → CTS",
        protocol="RS-232 · 9600 baud · Colorado legacy (unconfirmed)",
        tested=False,
        doc="omnisport-2000.md",
        options={"serial_parity": None},
    ),
    ConsoleEntry(
        "dak_os2000_omega",
        _DAK,
        "Scoreboard port set to OMEGA",
        "swiss_scb",
        adapter="USB-to-RS232 (DB9)",
        wiring="Scoreboard port, console menu Scbrd Port → OMEGA",
        protocol="Swiss Timing scoreboard lines · serial settings unconfirmed",
        tested=False,
        doc="omnisport-2000.md",
        options={"scb_line_mode": None, "serial_baud": None, "serial_format": None},
    ),
    ConsoleEntry(
        "ares21_ertd",
        _ARES,
        "SCB — Venus ERTD",
        "ares_ertd",
        adapter="USB-to-RS485",
        wiring="IF-ARES SCB connector: pin 4 → B+, pin 3 → A−, pin 7 → GND",
        protocol="RS-485 · 9600 baud · 8-N-1 · Daktronics RTD",
        tested=False,
        doc="ares-21.md",
        options={"ertd_event_field": None},
    ),
    ConsoleEntry(
        "ares21_unt4",
        _ARES,
        "SCB — UNT4 / Calypso / Piccolo / GALACTICA",
        "swiss_scb",
        adapter="USB-to-RS485",
        wiring="IF-ARES SCB connector: pin 4 → B+, pin 3 → A−, pin 7 → GND",
        protocol="RS-485 · Swiss Timing scoreboard lines",
        tested=False,
        doc="ares-21.md",
        options={"scb_line_mode": None, "serial_baud": None, "serial_format": None},
    ),
    ConsoleEntry(
        "ares21_gp_osm6",
        _ARES,
        "GP — OSM6 data handling",
        "swiss_osm6",
        adapter="USB-to-RS485",
        wiring="IF-ARES GP connector: pin 3 → B+ (TxD+), pin 7 → A− (TxD−), pin 8 → GND",
        protocol="RS-485 · 9600 baud · 7-E-1 · OSM6",
        tested=False,
        doc="ares-21.md",
        options={"serial_format": "7E1", "lane_zero": None},
    ),
    ConsoleEntry(
        "quantum_osm6",
        "Quantum (Swiss Timing Omega)",
        "OSM6 data output",
        "swiss_osm6",
        adapter="USB-to-RS485",
        wiring="Quantum pin 3 → A- · pin 4 → B+",
        protocol="RS-485 · 9600 baud · OSM6",
        tested=False,
        doc="quantum.md",
        options={"serial_format": None, "lane_zero": None},
    ),
    # Last, after every real console: not a console at all, but the answer for a meet
    # that has none — the operator sets event and heat by hand from /manual.
    ConsoleEntry(
        "manual",
        "Manual — no timing console",
        "",
        "manual",
        adapter="None",
        wiring="Nothing is connected to the server",
        protocol="None — event and heat are set by hand from /manual",
        tested=True,
        doc="manual.md",
    ),
]

DEFAULT_CONSOLE = "cts_sys6"

# Flat view for local plugins and older callers: (settings key, label, decoder key).
# A plugin appends to it (see load_custom_decoders); its entries become a model
# with a single output.
CONSOLE_OPTIONS: list[tuple[str, str, str]] = [
    (e.key, e.label, e.decoder) for e in CONSOLES
]

# Base URL for the full per-console setup guides on GitHub.
DOCS_BASE_URL = "https://github.com/olivierouellet/Splouch/blob/master/docs/consoles/"


def console_entry(console_type: str) -> ConsoleEntry | None:
    """The entry for a settings key — a built-in one, or a plugin's as a bare model."""
    for e in CONSOLES:
        if e.key == console_type:
            return e
    for key, label, dec in CONSOLE_OPTIONS:
        if key == console_type:
            return ConsoleEntry(
                key,
                label,
                "",
                dec,
                adapter="",
                wiring="",
                protocol="",
                tested=False,
                doc="",
            )
    return None


def console_models() -> list[tuple[str, list[tuple[str, str]]]]:
    """The two-level menu: [(model, [(key, output label), ...]), ...], in order."""
    models: dict[str, list[tuple[str, str]]] = {}
    for key, _, _ in CONSOLE_OPTIONS:
        e = console_entry(key)
        if e is not None:
            models.setdefault(e.model, []).append((e.key, e.output))
    return list(models.items())


def option_specs(console_type: str) -> list[tuple[DecoderOption, str]]:
    """The options this console's menu shows, each with this console's default."""
    e = console_entry(console_type)
    cls = DECODERS.get(e.decoder) if e else None
    if e is None or cls is None:
        return []
    by_key = {o.key: o for o in cls.OPTIONS}
    return [
        (by_key[k], default if default is not None else by_key[k].default)
        for k, default in e.options.items()
        if k in by_key
    ]


def option_values(console_type: str, settings: dict) -> dict[str, str]:
    """This console's option values: what the operator saved, else its defaults."""
    saved = (settings.get("console_options") or {}).get(console_type) or {}
    values = {}
    for opt, default in option_specs(console_type):
        v = saved.get(opt.key)
        values[opt.key] = v if v in {c for c, _ in opt.choices} else default
    return values


def decoder_config(console_type: str, settings: dict) -> dict:
    """The cfg a decoder for *console_type* is built and configured with."""
    e = console_entry(console_type)
    preset = e.preset if e else {}
    return {**settings, **preset, **option_values(console_type, settings)}


def same_decoding(a: str, b: str, settings: dict) -> bool:
    """Would consoles *a* and *b* decode a recording the same way?"""
    ea, eb = console_entry(a), console_entry(b)
    if ea is None or eb is None:
        return a == b
    return (
        DECODERS.get(ea.decoder) is DECODERS.get(eb.decoder)
        and ea.preset == eb.preset
        and option_values(a, settings) == option_values(b, settings)
    )


def console_info_for(console_type: str) -> dict | None:
    """Return the hardware/wiring summary for *console_type*, or None if unknown.

    The returned dict adds the human label and a full doc_url so templates can
    render a summary card plus a link to the complete guide. A plugin entry has no
    wiring notes, so it returns None like an unknown key.
    """
    e = console_entry(console_type)
    if e is None or not e.doc:
        return None
    # Read off the class, not an instance: Settings needs to know whether this console
    # has a wire before any decoder for it has been built, and the flag lives on the
    # decoder so a local-only plugin gets the same treatment without touching this
    # table. Default True for a plugin written against the older contract.
    cls = DECODERS.get(e.decoder)
    return {
        "adapter": e.adapter,
        "wiring": e.wiring,
        "protocol": e.protocol,
        "tested": e.tested,
        "doc": e.doc,
        "label": e.label,
        "doc_url": DOCS_BASE_URL + e.doc,
        "requires_serial": getattr(cls, "requires_serial", True),
        "transport": getattr(cls, "transport", "serial"),
    }


def load_custom_decoders(folder: str) -> None:
    """Load decoder plugins from *folder* and merge them into CONSOLE_OPTIONS / DECODERS.

    Each .py file in the folder must define:
        CONSOLE_OPTIONS = [('settings_key', 'Human Label', 'decoder_key'), ...]
        DECODERS        = {'decoder_key': MyDecoderClass, ...}

    Keys already present in the built-in registries are silently skipped so
    that calling this function multiple times is safe.
    """
    if not os.path.isdir(folder):
        return

    existing_keys = {k for k, _, _ in CONSOLE_OPTIONS}
    existing_decoders = set(DECODERS)

    for fname in sorted(os.listdir(folder)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        path = os.path.join(folder, fname)
        module_name = f"_splouch_custom_decoder_{fname[:-3]}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("no loader for this file")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)

            for entry in getattr(mod, "CONSOLE_OPTIONS", []):
                if entry[0] not in existing_keys:
                    CONSOLE_OPTIONS.append(entry)
                    existing_keys.add(entry[0])

            for key, cls in getattr(mod, "DECODERS", {}).items():
                if key not in existing_decoders:
                    DECODERS[key] = cls
                    existing_decoders.add(key)

        except Exception as e:
            print(f"[custom decoder] failed to load {fname}: {e}", flush=True)


def make_decoder(console_type: str, cfg: dict) -> ConsoleDecoder:
    """Build the decoder for a settings key, with that console's preset and options.

    A decoder key works too (the tests build decoders by protocol). Anything unknown
    falls back to the default console, a CTS.
    """
    e = console_entry(console_type)
    if e is None and console_type in DECODERS:
        return DECODERS[console_type](cfg)
    if e is None or e.decoder not in DECODERS:
        console_type = DEFAULT_CONSOLE
        e = console_entry(DEFAULT_CONSOLE)
    assert e is not None
    return DECODERS[e.decoder](decoder_config(console_type, cfg))
