"""A relay reconnect hands the cloud the board, not just the schedule.

The cloud's join replay (`cloud_server.ws_scoreboard`) is whatever it has merged
into `last_scoreboard` since the relay registered. After a dropped link — or the
relay restarting at the end of a local-only test — that starts empty, and the Pi
used to re-send only the schedule and the results. A spectator joining then got
finals with no event, heat or names until the next heat change.
"""

import pytest
import websocket

import relay
import state


class _Socket:
    """One connect: the first `recv` stops the thread and closes the link."""

    def settimeout(self, _):
        pass

    def recv(self):
        relay._stop.set()
        return ""

    def close(self):
        pass


@pytest.fixture
def connect(monkeypatch):
    sent = []
    monkeypatch.setattr(relay, "_send_raw", lambda ws, ev, d: sent.append((ev, d)))
    monkeypatch.setattr(relay, "_get_metadata", dict)
    monkeypatch.setattr(relay, "send_schedule", lambda client=None: None)
    monkeypatch.setattr(websocket, "create_connection", lambda *a, **k: _Socket())
    monkeypatch.setattr(state, "_test_local_only", False, raising=False)
    monkeypatch.setattr(state, "_last_results_snapshot", {}, raising=False)
    monkeypatch.setitem(state.settings, "cloud_relay_url", "wss://cloud.example")
    monkeypatch.setitem(state.settings, "cloud_relay_key", "k")
    monkeypatch.setattr(state, "board", {})
    relay._stop.clear()

    def run():
        relay._run()
        relay._stop.clear()
        return sent

    return run


def test_the_board_is_replayed_on_connect(connect):
    state.record_board(
        {
            "current_event": "3",
            "current_heat": "2",
            "event_name": "200 IM",
            "lane_name1": "Ledecky",
            "running_time": "1:02.3",
        }
    )

    boards = [d for ev, d in connect() if ev == "update_scoreboard"]

    assert boards == [
        {
            "current_event": "3",
            "current_heat": "2",
            "event_name": "200 IM",
            "lane_name1": "Ledecky",
        }
    ], "the cloud was not handed the header and names, or was handed a stale clock"


def test_an_empty_board_sends_nothing(connect):
    assert [ev for ev, _ in connect() if ev == "update_scoreboard"] == []
