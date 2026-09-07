"""`_forward()` — what the relay passes on to a meet's attendees.

The interesting one is `running_time`, the race clock. The console sends it on
every timing tick; multiplying that by every phone watching every meet is the
traffic `notes/cloud_parity.md` refused, and the cloud's first answer was to drop
the field outright — which left the phone board with no sign at all that a race
was under way.

It is now throttled instead: one re-base every `_CLOCK_SYNC_SECS`, plus any frame
that also moves a `lane_running<i>` flag, because a start, a touch, the end of the
console's split hold and a finish are rare and are exactly where the value has to
be right. Clients tick
their own clock in between (`docs/mobile-features.md` `L-12`).

Two things are easy to get wrong and are what most of this file guards:
throttling the *clock* must never throttle the rest of the frame, and the clock
must never reach `last_scoreboard`, which is replayed to a joining client with no
way to say how old it is.
"""
import asyncio
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Before the import: the module reads DATA_DIR at import time and every path in it
# is derived from that. Point it at a throwaway so a test run cannot touch a real
# retained store.
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='splouch-cloud-test-'))
sys.path.insert(0, os.path.join(REPO, 'cloud'))

import cloud_server as cs               # noqa: E402

SID = 'relay-sid'


class Relay:
    """One registered meet, with everything it broadcasts captured."""

    def __init__(self, meet):
        self.meet = meet
        self.sent = []

    def send(self, **frame):
        """Push one `update_scoreboard` from the console; return what went out."""
        self.sent.clear()
        asyncio.run(cs._forward(SID, 'update_scoreboard', dict(frame)))
        assert len(self.sent) == 1, 'every frame is broadcast exactly once'
        return self.sent[0]

    @property
    def snapshot(self):
        """What a client joining right now would be replayed."""
        return self.meet['last_scoreboard']

    def advance(self, seconds):
        """Age the last re-base, as if `seconds` had passed since it went out.

        Rewinding the stamp rather than faking `time.monotonic()` keeps the real
        clock under the code being tested — and out of the event loop, which reads
        the same one.
        """
        self.meet['clock_at'] -= seconds


@pytest.fixture
def relay(monkeypatch):
    meet = {'last_scoreboard': {}, 'clock_at': 0.0}
    monkeypatch.setattr(cs, '_meets', {'m1': meet})
    monkeypatch.setattr(cs, '_relay_sids', {SID: 'm1'})

    r = Relay(meet)

    async def _broadcast(channel, event, data):
        r.sent.append(dict(data))
    monkeypatch.setattr(cs.manager, 'broadcast', _broadcast)
    return r


# ── The clock ─────────────────────────────────────────────────────────────────

def test_the_first_clock_frame_goes_out(relay):
    """Nothing has been sent yet, so there is no reason to hold it back."""
    assert relay.send(running_time='5.00')['running_time'] == '5.00'


def test_a_burst_of_ticks_is_reduced_to_one(relay):
    """The point of the exercise: 20 console ticks, one frame carrying a clock."""
    kept = ['running_time' in relay.send(running_time=f'{5 + i}.00')
            for i in range(20)]
    assert kept.count(True) == 1
    assert kept[0] is True, 'the first tick should be the one that gets through'


def test_the_clock_flows_again_after_the_interval(relay):
    relay.send(running_time='5.00')
    assert 'running_time' not in relay.send(running_time='5.50')
    relay.advance(cs._CLOCK_SYNC_SECS)
    assert relay.send(running_time='7.00')['running_time'] == '7.00'


def test_a_wall_always_carries_the_clock(relay):
    """A lane touching mid-interval: the flag drops and the split arrives.

    This is the frame a client re-bases off before freezing the lane, so it must
    not be the one the throttle eats.
    """
    relay.send(running_time='5.00')                       # opens the interval
    frame = relay.send(running_time='28.60',
                       lane_running1=False, lane_time1='28.41')  # the touch
    assert frame['running_time'] == '28.60'
    assert frame['lane_time1'] == '28.41'


def test_the_end_of_a_hold_always_carries_the_clock(relay):
    """The other edge of the same pause — the lane rejoins the race clock.

    The console ends the hold on its own timer, some seconds after the touch, so
    this frame lands mid-interval and would otherwise be throttled away.
    """
    relay.send(running_time='5.00')
    assert relay.send(running_time='30.00', lane_running1=True)['running_time']


def test_a_lane_frame_rebases_the_interval(relay):
    """Having just sent one, the throttle starts again from there.

    Without this a wall would let the next tick through as well, and a heat with
    eight lanes turning together would forward a burst at every length.
    """
    relay.send(running_time='28.60', lane_running1=False)
    assert 'running_time' not in relay.send(running_time='28.70')


def test_throttling_the_clock_keeps_the_rest_of_the_frame(relay):
    """Frames are partial (§5.1). Dropping one would lose a place or a time."""
    relay.send(running_time='5.00')
    frame = relay.send(running_time='5.50', lane_place3='2', lane_delta3='+1.20')
    assert 'running_time' not in frame
    assert frame == {'lane_place3': '2', 'lane_delta3': '+1.20'}


def test_a_meet_with_no_stamp_yet_still_forwards(relay):
    """Meets carried over from a reconnect predate the field."""
    del relay.meet['clock_at']
    assert relay.send(running_time='5.00')['running_time'] == '5.00'


# ── The join snapshot ─────────────────────────────────────────────────────────

def test_the_clock_never_enters_the_snapshot(relay):
    """A replayed clock has no age on it, so it would be wrong by any amount."""
    relay.send(running_time='5.00', lane_name1='TREMBLAY Marie')
    assert 'running_time' not in relay.snapshot
    assert relay.snapshot['lane_name1'] == 'TREMBLAY Marie'


def test_a_split_does_enter_the_snapshot(relay):
    """Unlike the clock it is a fact about the swim, and a joiner needs it."""
    relay.send(running_time='28.60', lane_running1=False, lane_time1='28.41')
    assert relay.snapshot['lane_time1'] == '28.41'
    assert relay.snapshot['lane_running1'] is False


def test_frames_accumulate_across_updates(relay):
    """`last_scoreboard` merges; it is not replaced (`L-10`)."""
    relay.send(current_event='3', current_heat='1')
    relay.send(lane_name1='TREMBLAY Marie')
    assert relay.snapshot == {'current_event': '3', 'current_heat': '1',
                              'lane_name1': 'TREMBLAY Marie'}


# ── Everything else ───────────────────────────────────────────────────────────

def test_a_frame_with_no_clock_is_untouched(relay):
    assert relay.send(lane_club1='CAMO') == {'lane_club1': 'CAMO'}


def test_an_unregistered_relay_broadcasts_nothing(relay, monkeypatch):
    """A socket that never registered, or one whose meet has been retired."""
    monkeypatch.setattr(cs, '_relay_sids', {})
    asyncio.run(cs._forward(SID, 'update_scoreboard', {'running_time': '5.00'}))
    assert relay.sent == []
