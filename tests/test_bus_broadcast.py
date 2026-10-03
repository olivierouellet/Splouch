"""Both buses encode a broadcast frame once and send the same text to everyone.

`send_json` re-ran json.dumps per socket — the same frame thousands of times on a
busy meet (docs/architecture/scaling.md, stage 0). The bytes on the wire must not
change: clients parse them, and Starlette's encoding is compact and non-ASCII-safe.
"""

import asyncio
import json

import pytest

import bus
import cloud_bus


class FakeWS:
    def __init__(self, fail=False):
        self.fail = fail
        self.texts = []

    async def send_text(self, text):
        if self.fail:
            raise ConnectionError("gone")
        self.texts.append(text)


def _starlette_encoding(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


DATA = {"lane_name1": "Zoé Côté", "lane_time1": "58.21"}


@pytest.fixture(params=["pi", "cloud"])
def setup(request):
    """A manager with two good sockets and one dead one in a single channel."""
    good, dead = [FakeWS(), FakeWS()], FakeWS(fail=True)
    if request.param == "pi":
        mgr, channel = bus.ConnectionManager(), "/scoreboard"
        mgr.channels[channel] = {*good, dead}
    else:
        mgr, channel = cloud_bus.ConnectionManager(), cloud_bus.ch("scoreboard", "m1")
        for ws in (*good, dead):
            mgr.join(ws, channel)
    return mgr, channel, good, dead


def test_every_socket_gets_the_same_starlette_encoded_text(setup):
    mgr, channel, good, _ = setup
    asyncio.run(mgr.broadcast(channel, "update_scoreboard", DATA))
    want = _starlette_encoding({"event": "update_scoreboard", "data": DATA})
    assert [ws.texts for ws in good] == [[want], [want]]


def test_the_frame_is_encoded_once_not_per_socket(setup, monkeypatch):
    mgr, channel, _, _ = setup
    calls = []
    real = json.dumps
    monkeypatch.setattr(json, "dumps", lambda *a, **k: calls.append(1) or real(*a, **k))
    asyncio.run(mgr.broadcast(channel, "update_scoreboard", DATA))
    assert len(calls) == 1


def test_a_failed_socket_is_dropped_and_the_rest_still_receive(setup):
    mgr, channel, good, dead = setup
    asyncio.run(mgr.broadcast(channel, "update_scoreboard", DATA))
    assert dead not in mgr.channels[channel]
    assert all(ws in mgr.channels[channel] for ws in good)
