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
    monkeypatch.setattr(relay, "_assignment", None)
    monkeypatch.setattr(
        relay,
        "_assign",
        lambda url, key, uid: {
            "for": (url, key, uid),
            "meet_id": "m1",
            "relay_url": "wss://cloud.example/ws/relay?meet=m1",
            "ticket": "t",
            "region": "ca",
            "until": 9e12,
        },
    )
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


# ── Where to publish (`POST /api/assign`) ──────────────────────────────────────
#
# The Pi asks the cloud which worker carries its meet before connecting, and keeps
# the answer: a dropped link reconnects to the same worker with the same ticket.
# It asks again for a new meet, key or server, when a worker refuses the ticket,
# after repeated failed connects, and — while the cloud cannot be reached — keeps
# using a ticket that has not run out, which the worker admits on its own.


@pytest.fixture
def assigning(monkeypatch):
    calls = []

    def assign(url, key, uid):
        calls.append(uid)
        return {
            "for": (url, key, uid),
            "meet_id": "m-" + uid,
            "relay_url": "wss://w1.example/ws/relay",
            "ticket": f"ticket-{len(calls)}",
            "region": "ca",
            "until": relay.time.time() + 86400,
        }

    monkeypatch.setattr(relay, "_assign", assign)
    monkeypatch.setattr(relay, "_assignment", None)
    monkeypatch.setattr(state, "meet_uid", lambda: "uid-1")
    relay._reassign.clear()
    yield calls
    relay._reassign.clear()


def test_an_assignment_is_kept_across_reconnects(assigning):
    a = relay._current_assignment("https://c", "k")
    assert relay._current_assignment("https://c", "k") is a
    assert assigning == ["uid-1"]


def test_a_new_meet_asks_again(assigning, monkeypatch):
    relay._current_assignment("https://c", "k")
    monkeypatch.setattr(state, "meet_uid", lambda: "uid-2")
    assert relay._current_assignment("https://c", "k")["meet_id"] == "m-uid-2"


def test_a_refused_ticket_asks_again(assigning):
    relay._current_assignment("https://c", "k")
    relay._reassign.set()
    assert relay._current_assignment("https://c", "k")["ticket"] == "ticket-2"
    assert not relay._reassign.is_set()


def test_an_unreachable_cloud_keeps_a_live_ticket(assigning, monkeypatch):
    kept = relay._current_assignment("https://c", "k")
    relay._reassign.set()

    def down(*a):
        raise OSError("connection refused")

    monkeypatch.setattr(relay, "_assign", down)
    assert relay._current_assignment("https://c", "k") is kept


def test_an_unreachable_cloud_and_no_ticket_waits(assigning, monkeypatch):
    def down(*a):
        raise OSError("connection refused")

    monkeypatch.setattr(relay, "_assign", down)
    assert relay._current_assignment("https://c", "k") is None


def test_the_register_carries_the_ticket_and_the_location(assigning, monkeypatch):
    monkeypatch.setattr(relay, "_get_metadata", dict)
    monkeypatch.setitem(state.settings, "cloud_country", "CA")
    monkeypatch.setitem(state.settings, "cloud_province", "QC")
    a = relay._current_assignment("https://c", "k")
    payload = relay._register_payload("k", a)
    assert payload["ticket"] == "ticket-1" and payload["key"] == "k"
    assert payload["organizer_location"] == {"country": "CA", "province": "QC"}


def test_switching_meets_drops_the_link_rather_than_re_registering(
    assigning, monkeypatch
):
    """A ticket is for one meet: a new one needs a new ticket, maybe another worker."""

    class Sock:
        closed = False

        def close(self):
            self.closed = True

    sock = Sock()
    monkeypatch.setitem(state.settings, "cloud_relay_url", "https://c")
    monkeypatch.setitem(state.settings, "cloud_relay_key", "k")
    monkeypatch.setattr(state, "_test_local_only", False, raising=False)
    relay._current_assignment("https://c", "k")
    monkeypatch.setattr(relay, "_client", sock)
    monkeypatch.setattr(relay, "_connected", True)
    monkeypatch.setattr(state, "meet_uid", lambda: "uid-2")
    relay.update_metadata()
    assert sock.closed and relay._reassign.is_set()


def test_assign_is_asked_of_the_server_url(monkeypatch):
    assert relay._api_url("https://splouch.org/") == "https://splouch.org/api/assign"
    assert relay._api_url("wss://splouch.org") == "https://splouch.org/api/assign"
    assert relay._api_url("splouch.org") == "https://splouch.org/api/assign"


def test_the_pi_sends_its_session_days_and_utc_offset(monkeypatch):
    monkeypatch.setattr(
        state.meet,
        "meet_info",
        {
            "sessions": [
                {"date": "2026-10-12"},
                {"date": "2026-10-11"},
                {"date": "2026-10-11"},
            ]
        },
        raising=False,
    )
    assert relay._session_dates() == ["2026-10-11", "2026-10-12"]
    offset = relay._utc_offset_minutes()
    assert isinstance(offset, int) and -720 <= offset <= 840


def test_a_stopped_relay_thread_ends_even_when_restarted_at_once(monkeypatch):
    """`start()` swaps in a fresh `_stop`. A thread still waiting when the relay was
    stopped and started again (the Cloud toggle, a short test session) read the
    new, unset event and kept going: two relay threads publishing one meet."""
    import state

    monkeypatch.setitem(state.settings, "cloud_relay_url", "")  # idle: waits on stop
    relay.start()
    first = relay._thread
    assert first is not None
    relay.stop()
    relay.start()
    try:
        first.join(timeout=3)
        assert not first.is_alive(), "the stopped thread is still running"
    finally:
        relay.stop()
        assert relay._thread is not None
        relay._thread.join(timeout=3)
