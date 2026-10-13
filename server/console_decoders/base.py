from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SerialConfig:
    baud: int = 9600
    bytesize: int = 8
    parity: str = "E"
    stopbits: int = 1


# "8N1" → 8 data bits, no parity, 1 stop bit: the spelling the option menus use.
SERIAL_FORMATS: dict[str, tuple[int, str, int]] = {
    "8N1": (8, "N", 1),
    "8E1": (8, "E", 1),
    "7E1": (7, "E", 1),
}


def serial_from(baud, fmt: str, fallback: str = "8N1") -> SerialConfig:
    """A SerialConfig from an option's baud and "8N1"-style format strings."""
    bytesize, parity, stopbits = SERIAL_FORMATS.get(fmt, SERIAL_FORMATS[fallback])
    try:
        baud = int(baud)
    except (TypeError, ValueError):
        baud = 9600
    return SerialConfig(baud=baud, bytesize=bytesize, parity=parity, stopbits=stopbits)


@dataclass(frozen=True)
class DecoderOption:
    """One part of the decoding the evidence does not settle, left to the operator.

    A decoder lists them in `OPTIONS`; a console entry in `CONSOLES` says which ones
    its menu shows and may change the default (the same OSM6 decoder starts at 7-E-1
    on an ARES and 8-N-1 on a Quantum). Settings → Timing shows the exposed ones
    only while that console is selected, and stores each console's choices apart, so
    switching consoles never carries a value across.

    The value reaches the decoder as `cfg[key]` — always a string, one of `choices`.
    `label`, `help` and each choice's label are English; the panel file can replace
    them (`console_opt_<key>`, `console_opt_<key>_help`, `console_opt_<key>_<value>`).
    """

    key: str
    label: str
    choices: tuple[tuple[str, str], ...]
    default: str
    help: str = ""


class ConsoleDecoder(ABC):
    """Abstract base for timing-console serial decoders.

    Implementors receive raw byte packets and return structured update dicts
    whose keys match the scoreboard's update_scoreboard event payload.
    No web-framework, transport, or app-layer dependencies belong here.
    """

    #: Does this decoder read a wire — a serial port, or a socket (see `transport`)?
    #:
    #: False for a decoder the operator drives by hand rather than off a wire (see
    #: manual.py). The worker reads it to decide whether to open a port at all, and
    #: Settings → Timing reads it *off the class* — via console_info_for() — to decide
    #: whether a port picker and a Connection badge mean anything for this console.
    #: A plain attribute rather than a property for exactly that reason: it has to be
    #: answerable before any decoder for the console has been built.
    requires_serial: bool = True

    #: Where the bytes come from when `requires_serial` is True: "serial" (a port the
    #: worker opens with `serial_config`) or "tcp" (a socket to `tcp_endpoint`, for a
    #: console whose PC software serves its data over the network). Same packet
    #: pipeline either way. Read off the class, like `requires_serial`.
    transport: str = "serial"

    @property
    def tcp_endpoint(self) -> tuple[str, int]:
        """(host, port) to connect to when `transport` is "tcp"."""
        return ("", 0)

    def tick(self) -> dict:
        """Idle turn, on the worker thread, whenever no byte is waiting (~100 Hz).

        For a decoder whose state changes with time rather than with packets — the
        Dolphin decoder holds a finished race on the board before following the
        software to the next heat. Returns an update dict like `feed`; default none.
        """
        return {}

    #: Decoding choices this decoder cannot make from the wire alone — see
    #: `DecoderOption`. Read off the class, like `requires_serial`.
    OPTIONS: tuple[DecoderOption, ...] = ()

    #: The (event, heat) this decoder last announced, and the seed times it holds
    #: for that heat.
    #:
    #: Declared here, without values, because the app layer reads both off whatever
    #: decoder is configured — `worker`, `meet_data`, `routes/scoreboard` and
    #: `routes/debug` all touch them directly — so they are part of the contract,
    #: not an implementation detail. Every bundled decoder sets them in __init__,
    #: and a custom decoder dropped into CUSTOM_DECODERS_FOLDER has to as well.
    #:
    #: Annotations only, deliberately: a class-level `{}` would be one dict shared
    #: by every decoder ever built.
    last_event_sent: tuple[int, int]
    lane_seed_times: dict[int, str]

    @abstractmethod
    def __init__(self, cfg: dict) -> None:
        """Build a decoder from the settings dict (`state.settings`).

        Declared because `make_decoder` builds whatever class `DECODERS` holds and
        passes the config straight in, so taking `cfg` is part of the contract a
        custom decoder has to meet — not a convention the bundled six happen to
        share.
        """
        ...

    @property
    @abstractmethod
    def serial_config(self) -> SerialConfig:
        """Serial port parameters required by this console."""
        ...

    @property
    def is_live(self) -> bool | None:
        """Whether the link is up, when the decoder knows better than packet arrival.

        None — the default, and the right answer for every wired console — means
        "ask the packet clock", i.e. `app._meet_live_watchdog` falls back to
        `state._last_packet_at`. Override only when packets are the wrong measure:
        a manually driven console has none at all, yet is not dead.
        """
        return None

    # The `/` here and on `feed`, `get_lane_time` and `get_lane_place`: the worker
    # calls all four positionally, and decoders name their parameters to suit the
    # protocol they speak (`raw`, `_buffer`, `lane`). Positional-only says that is
    # fine, rather than binding every implementor to this file's choice of words.
    @abstractmethod
    def is_packet_start(self, byte: int, buffer: list[int], /) -> bool:
        """Return True if byte begins a new packet, flushing any buffered data first.

        Called by the worker for every incoming byte before it is appended to
        the buffer.  Implementations should inspect the byte (and optionally the
        current buffer contents) to detect packet boundaries.

        Examples:
          - CTS Gen6: return bool(byte & 0x80)   — high bit marks new packet
          - Omnisport 2000: return byte == 0x16  — SYN byte starts a frame
        """
        ...

    @property
    def post_open_bytes(self) -> bytes:
        """Bytes to write immediately after the serial port is opened.

        Override in decoders that must send an initialization sequence to the
        console before it starts streaming data (e.g. CTS Gen7).
        Default: empty (no initialization required).
        """
        return b""

    @property
    def max_packet_bytes(self) -> int:
        """Maximum number of bytes in one packet before forcing a flush.

        Override in subclasses when the protocol has a known fixed or maximum
        packet length.  The worker flushes the buffer when this limit is reached
        even if no packet-start byte was seen.  Defaults to 256.
        """
        return 256

    @abstractmethod
    def feed(self, packet: list[int], /) -> dict:
        """Decode one assembled packet.

        Returns an update dict (subset of keys from the update_scoreboard
        payload).  Special keys consumed by the app layer before emitting:
          - 'event_changed': (event_num, heat_num) — new event/heat detected;
            app should load names, seed times, heat_time, etc. then call
            set_seed_times().
          - 'dismiss_overlay': True — a lane just started running; app should
            dismiss any active overlay.

        For consoles that transmit split times natively, include
        'lane_splits{n}' in the returned dict with the cumulative split count.

        Returns {} when the packet produces nothing of interest.
        """
        ...

    @abstractmethod
    def reset_lanes(self) -> dict:
        """Reset per-race lane state.

        Returns the update dict for those resets so the app can emit them.
        Called by the app layer when a race restarts unexpectedly.
        """
        ...

    @abstractmethod
    def race_finished(self) -> bool:
        """True when every lane with a time has a final place and is stopped."""
        ...

    def adjust_splits(self, lane: int, delta: int) -> int:
        """Nudge a lane's lap count by hand; return the value after the change.

        Concrete rather than abstract, and a no-op by default: /operator's ± buttons
        emit `adjust_splits` for whatever console happens to be configured, and
        `worker._worker_adjust_splits` calls this blind. An abstract method would
        make every decoder that counts nothing carry a stub, and a missing one
        surfaced only as an AttributeError traceback on the worker thread.

        Override wherever there is a count to move — which means anywhere
        `lane_splits{n}` is ever emitted, since the reason the operator reaches for
        the buttons is that the count on the board is wrong.
        """
        return 0

    def set_heat_lanes(self, lanes) -> None:
        """Supply the lanes that have a swimmer in the current heat.

        Called by the app layer beside set_seed_times(), from the same start lists.
        Empty means "not known" — a heat missing from the meet file, or no meet at all.

        Only a console that cannot say per lane who is racing needs it. The Quantum,
        ARES 21 and Omnisport 2000 send one start for the whole pool, so they used to
        mark every lane running — and an empty lane, which never touches, kept
        `race_finished()` False for good. A CTS reports each lane's running state
        off the wire and never reads this. Concrete, and not cleared by
        reset_lanes(): a re-swim resets lanes without a new heat to reload it from.
        """
        self._heat_lanes = frozenset(int(n) for n in lanes)

    def lanes_to_start(self) -> list[int]:
        """The lanes a whole-pool start puts in the water.

        The heat's own lanes when the start lists say which they are, every lane up to
        `num_lanes` when they do not — the old behaviour, and the only safe guess.
        """
        num_lanes = int(getattr(self, "num_lanes", 0) or 0)
        known = getattr(self, "_heat_lanes", frozenset())
        lanes = range(1, num_lanes + 1)
        return [n for n in lanes if n in known] if known else list(lanes)

    @abstractmethod
    def set_seed_times(self, times: dict) -> None:
        """Supply seed times for the current heat.

        times: {lane_idx: time_str} — called by the app layer after an
        event_changed signal, once it has looked up the Lenex/HyTek data.
        """
        ...

    @abstractmethod
    def configure(self, cfg: dict) -> None:
        """Update runtime configuration (num_lanes, split thresholds, etc.)."""
        ...

    @abstractmethod
    def get_lane_time(self, lane_idx: int, /) -> str:
        """Return the formatted finish/split time for lane_idx (1-based)."""
        ...

    @abstractmethod
    def get_lane_place(self, lane_idx: int, /) -> str:
        """Return the place string (' ' if not yet placed) for lane_idx (1-based)."""
        ...
