"""`meet_live` — does the console still feed this display?

The local twin of the cloud's relay-connected flag (docs/api.md §2). Clients gate
live-only UI on it: the mobile board's lane-number pulse is the whole progress
indication there, since the Pi's mobile view carries no running clock, so a flag
stuck ON makes a dead link look like a race in progress.

Keyed off packet arrival, not the serial port's state — an open port against a
powered-off console reports 'open' indefinitely.
"""
import asyncio
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import bus                              # noqa: E402
import state                            # noqa: E402
from app import _meet_live_watchdog     # noqa: E402


@pytest.fixture
def emitted(monkeypatch):
    monkeypatch.setattr(state, '_meet_live', False, raising=False)
    monkeypatch.setattr(state, '_last_packet_at', 0.0, raising=False)
    out = []
    monkeypatch.setattr(bus, 'emit', lambda ch, ev, data=None: out.append((ch, ev, data)))
    return out


def _tick(monkeypatch, now):
    """Run exactly one watchdog iteration at a pinned monotonic time.

    The loop is infinite by design, so the escape is the sleep: it raises instead
    of yielding, which ends the run after the first pass.
    """
    monkeypatch.setattr('app.time.monotonic', lambda: now)

    async def _stop(_):
        raise asyncio.CancelledError
    monkeypatch.setattr(asyncio, 'sleep', _stop)

    async def _once():
        with pytest.raises(asyncio.CancelledError):
            await _meet_live_watchdog()
    asyncio.run(_once())


def test_silence_reads_as_dead(emitted, monkeypatch):
    """No packet ever seen — the flag must not start out true."""
    _tick(monkeypatch, now=100.0)
    assert state._meet_live is False
    assert emitted == []          # already false; only transitions are broadcast


def test_packet_brings_the_link_up(emitted, monkeypatch):
    state._last_packet_at = 100.0
    _tick(monkeypatch, now=101.0)
    assert state._meet_live is True
    assert emitted == [('/scoreboard', 'meet_live', {'live': True}),
                       ('/results',    'meet_live', {'live': True})]


def test_link_drops_after_the_stale_window(emitted, monkeypatch):
    """A console that goes quiet must be reported dead, not left latched on."""
    state._meet_live      = True
    state._last_packet_at = 100.0
    _tick(monkeypatch, now=100.0 + state.MEET_LIVE_STALE + 0.1)
    assert state._meet_live is False
    assert emitted == [('/scoreboard', 'meet_live', {'live': False}),
                       ('/results',    'meet_live', {'live': False})]


def test_inside_the_window_stays_live_and_stays_quiet(emitted, monkeypatch):
    """Gaps shorter than the window are normal traffic, not an outage."""
    state._meet_live      = True
    state._last_packet_at = 100.0
    _tick(monkeypatch, now=100.0 + state.MEET_LIVE_STALE - 0.1)
    assert state._meet_live is True
    assert emitted == []          # no transition => no frame


def test_packet_handler_stamps_the_clock(monkeypatch):
    """The stamp must land even when the decoder rejects the packet — the link is
    alive either way, and a decoder that rejects everything is not an outage."""
    import worker

    class _Rejects:
        def feed(self, _):
            raise ValueError('unparseable')
    monkeypatch.setattr(state, '_decoder', _Rejects(), raising=False)
    monkeypatch.setattr(state, '_last_packet_at', 0.0, raising=False)
    monkeypatch.setattr(worker.time, 'monotonic', lambda: 500.0)

    worker._handle_packet([0x01, 0x02])
    assert state._last_packet_at == 500.0


def test_stale_window_matches_the_qt_display():
    """The board and the TV must give up on the link at the same moment; if they
    disagree the TV shows CONNECTION LOST while phones still pulse, or vice versa."""
    client = os.path.join(REPO, 'scoreboard', 'client.py')
    stale = next(line for line in open(client) if line.startswith('_STALE'))
    assert int(stale.split('=')[1].strip()) == state.MEET_LIVE_STALE


# ── Header on a board that has not seen the console yet ────────────────────────

def _header(last_event_sent, monkeypatch):
    """The current_event / current_heat a fresh client receives on connect."""
    import bus as bus_mod
    import relay
    import meet_data

    class _Decoder:
        def __init__(self, last):
            self.last_event_sent = last

    out = []
    monkeypatch.setattr(state, '_decoder', _Decoder(last_event_sent), raising=False)
    monkeypatch.setattr(bus_mod, 'emit', lambda ch, ev, d=None: out.append(d))
    monkeypatch.setattr(relay, 'relay_emit', lambda ev, d=None: None)
    meet_data.send_event_info()
    return out[0]


def test_no_event_yet_leaves_the_header_blank(monkeypatch):
    """(0, 0) is the decoder's "nothing yet" sentinel, not event 0 of heat 0.
    Clients write these straight into the header, so sending str(0) painted a
    literal "0" under EVENT and HEAT before the console had reported anything."""
    u = _header((0, 0), monkeypatch)
    assert u['current_event'] == ''
    assert u['current_heat'] == ''
    assert u['event_name'] == ''


def test_a_real_event_still_comes_through(monkeypatch):
    u = _header((3, 1), monkeypatch)
    assert u['current_event'] == '3'
    assert u['current_heat'] == '1'


def test_every_lane_a_twelve_lane_pool_can_have_is_covered(monkeypatch):
    """Settings offers a 12-lane pool, and this frame is what a reconnecting client
    and `next_heat` get.

    Everything else that writes these keys covers 1-12 — `worker._load_heat_names`
    and every decoder's `reset_lanes()`. This stopped at 10, so lanes 11 and 12 kept
    the previous heat's swimmers while the rest of the board moved on.
    """
    u = _header((3, 1), monkeypatch)
    for lane in range(1, 13):
        assert f'lane_name{lane}' in u, f'lane {lane} never gets a name'
        assert f'lane_club{lane}' in u
        assert f'lane_delta_seconds{lane}' in u, f'lane {lane} keeps a stale delta'
