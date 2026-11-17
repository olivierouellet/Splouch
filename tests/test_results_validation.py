"""The two ways a validation wait ends without the console's validation.

The validated path itself is the OmniSport capture, in `test_console_recordings`.
Here: the operator moving to the next heat first, and the console never saying it.
"""

import pytest

import bus
import relay
import state
import worker
from console_decoders.omnisport_2000 import Omnisport2000Decoder


def _rtd(offset: int, data: str) -> list[int]:
    body = [*b"00000000", 0x01, *f"004210{offset:04d}".encode(), 0x02]
    body += [*data.encode(), 0x04]
    return [0x16, *body, *f"{sum(body) & 0xFF:02X}".encode(), 0x17]


def _line(lane: int, place: str = "", time: str = "", lengths: str = "") -> str:
    return f"{'SWIMMER':<15}{'':5}{lane:>2}{place:>3}{time:<9}{lengths:>2}"


@pytest.fixture
def pool(monkeypatch):
    events, tasks = [], []
    monkeypatch.setattr(bus, "emit", lambda ch, ev, d=None: events.append((ev, d)))
    monkeypatch.setattr(bus, "run_bg", lambda fn, *a: tasks.append(fn))
    monkeypatch.setattr(relay, "relay_emit", lambda ev, d=None: None)
    monkeypatch.setitem(state.settings, "num_lanes", 2)
    monkeypatch.setitem(state.settings, "first_lane", 1)
    monkeypatch.setattr(state, "update", {}, raising=False)
    monkeypatch.setattr(state, "_running_lanes", set(), raising=False)
    monkeypatch.setattr(state, "_results_prev_race_finished", False)
    monkeypatch.setattr(state, "_awaiting_validation", False)
    d = Omnisport2000Decoder({"num_lanes": 2, "rtd_validates": True})
    monkeypatch.setattr(state, "_decoder", d, raising=False)

    def feed(offset, data):
        worker._apply_updates(d.feed(_rtd(offset, data)))

    feed(99, "  5 1")
    feed(126, " 2")  # two lengths
    feed(0, "    0.1  ")
    feed(222, _line(1, "1", "30.10", " 2"))
    feed(258, _line(2, "2", "31.20", " 2"))
    return feed, events, tasks


def _confirmed(events):
    return [d for ev, d in events if ev == "results_snapshot"]


def test_a_finished_heat_waits(pool):
    _, events, tasks = pool
    assert state._awaiting_validation and not _confirmed(events)
    assert ("update_scoreboard", {"results_pending": True}) in events
    assert tasks, "the fallback timer was not armed"


def test_the_next_heat_publishes_the_heat_left_with_its_corrections(pool):
    feed, events, _ = pool
    feed(222, _line(1, "2", "31.90", " 2"))  # corrected after the finish
    feed(258, _line(2, "1", "31.20", " 2"))
    feed(99, "  5 2")
    (snap,) = _confirmed(events)
    assert snap["heat"] == "1"
    assert {r["channel"]: r["place"] for r in snap["lanes"]} == {1: "2", 2: "1"}
    assert not state._awaiting_validation


def test_no_validation_publishes_after_the_timeout_and_a_late_one_republishes(pool):
    feed, events, _ = pool
    worker._validation_timeout(state._finish_timer_gen)
    assert len(_confirmed(events)) == 1 and not state._awaiting_validation
    feed(222, _line(1, "2", "31.90", " 2"))
    feed(258, _line(2, "1", "31.20", " 2"))
    feed(1484, "Official Results")
    late = _confirmed(events)[-1]
    assert len(_confirmed(events)) == 2, "a validation after the fallback is final"
    assert {r["channel"]: r["place"] for r in late["lanes"]} == {1: "2", 2: "1"}


def test_a_stale_timeout_does_nothing(pool):
    feed, events, _ = pool
    gen = state._finish_timer_gen
    feed(1484, "Official Results")
    worker._validation_timeout(gen)
    assert len(_confirmed(events)) == 1
