"""Manual console — the meet with no timing console at all.

Every method here is the empty answer: there is no wire, no packet, no time and no
place. What actually puts names on the boards is not this class but
`worker._worker_set_heat`, which routes a hand-picked heat through the same
`_on_event_changed` a real console's `event_changed` key goes through — so the event
name, the heat time, the expected split count, the seed times and the next-heats list
all load exactly as they would under a CTS.

The decoder's only real state is therefore `last_event_sent`: which heat the operator
says is on. Everything else exists so that the rest of the app — `send_event_info`,
`_build_results_snapshot`, `_add_lane_deltas`, the `/operator` split buttons — can go
on talking to `state._decoder` without knowing there is nobody on the other end.
"""
from .base import ConsoleDecoder, SerialConfig


class ManualDecoder(ConsoleDecoder):
    """Event and heat set by hand from /manual. No serial port is ever opened."""

    requires_serial = False

    def __init__(self, cfg: dict) -> None:
        self.last_event_sent: tuple[int, int] = (0, 0)
        self.lane_seed_times: dict[int, str] = {}
        self.configure(cfg)

    # ── ConsoleDecoder interface ──────────────────────────────────────────────

    @property
    def serial_config(self) -> SerialConfig:
        """Never used — `requires_serial` is False, so no port is opened.

        Returns a default rather than None so that no caller has to learn a new
        nullable for the sake of one decoder.
        """
        return SerialConfig()

    @property
    def is_live(self) -> bool | None:
        """Live from the moment a heat is committed.

        There is no console here to fall silent, so the packet clock would call this
        link dead 8 seconds in and never revive it — which would leave the Results
        page and the mobile Results tab on "waiting" for the whole meet, `next_heats`
        included. The honest measure is what is on the boards: (0, 0) is the "nothing
        yet" sentinel, so before the first heat there is genuinely nothing running.
        """
        return self.last_event_sent != (0, 0)

    def is_packet_start(self, byte: int, _buffer: list[int]) -> bool:
        return False

    def feed(self, packet: list[int]) -> dict:
        """Never called — `_run_manual` reads no bytes. Empty for safety anyway.

        It can be reached one way: starting a Test-tab replay while this console is
        selected. The recording plays and produces nothing, which is why the Test tab
        says so rather than looking broken.
        """
        return {}

    def race_finished(self) -> bool:
        """Never. A hard False, not a computed one.

        Manual mode carries no times, so a True here would hand
        `_build_results_snapshot` a heat with no timed lanes and publish an empty
        result — blanking the Results page instead of leaving it waiting.
        """
        return False

    def reset_lanes(self) -> dict:
        updates: dict = {}
        for ln in range(1, 13):
            updates[f'lane_time{ln}']          = ''
            updates[f'lane_place{ln}']         = ' '
            updates[f'lane_running{ln}']       = False
            updates[f'lane_splits{ln}']        = 0
            # The structured pair travels with the HTML delta wherever
            # `send_event_info` sends it, so clear all three together — otherwise a
            # native client keeps the previous heat's delta after a heat change.
            updates[f'lane_delta{ln}']         = ''
            updates[f'lane_delta_seconds{ln}'] = None
            updates[f'lane_delta_better{ln}']  = None
        return updates

    def set_seed_times(self, times: dict) -> None:
        # Nothing here will ever read these back — there are no finish times to
        # compare them against. Stored anyway because the decoder contract is the
        # decoder contract, and `_build_results_snapshot` reads the attribute blind.
        self.lane_seed_times = dict(times)

    def configure(self, cfg: dict) -> None:
        self._num_lanes = int(cfg.get('num_lanes', 8))

    def get_lane_time(self, lane_idx: int) -> str:
        return ''

    def get_lane_place(self, lane_idx: int) -> str:
        return ' '

    # `adjust_splits` is the base class's no-op, which is the honest answer with no
    # lengths being counted — there is no wire here to disagree with the operator.
