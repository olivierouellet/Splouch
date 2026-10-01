"""Test → Record, in its two formats.

`.serial` is packets as the configured console's decoder splits them, each stamped
with its arrival time. `.raw` is the bytes as read, ahead of the decoder — for a
console it does not know yet, whose packet boundaries it would get wrong. Each format
must write only its own kind of line, and a `.raw` must read the way `cap-to-raw.py`
writes one, so the two sources of a `.raw` are interchangeable.

Qt-free: the routes are driven directly and the worker's byte tap called by hand.
"""

import os
import re

import pytest

import routes.debug as debug
import state
import worker


@pytest.fixture
def sessions_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "CUSTOM_SESSIONS_FOLDER", str(tmp_path))
    yield tmp_path
    debug._close_recording()


def _feed(packets):
    """What the live read loop does with each packet's bytes, in order."""
    for packet in packets:
        for c in packet:
            worker._record_raw_byte(c)
        worker._handle_packet(packet)


PACKETS = [[0xA6, 0x05, 0x15, 0x2E], [0xFC, 0x0E, 0x15]] * 3


def test_serial_writes_one_timed_line_per_packet(sessions_dir):
    started = debug.route_test_record_start(debug.RecordBody(name="Heat 1"))
    assert started["file"] == "heat_1.serial"
    _feed(PACKETS)
    debug.route_test_record_stop()

    lines = (sessions_dir / "heat_1.serial").read_text().splitlines()
    assert len(lines) == len(PACKETS)
    assert all(
        re.fullmatch(r"\[[0-9.]+\] [0-9A-F]{2}( [0-9A-F]{2})*", ln) for ln in lines
    )


def test_raw_writes_the_bytes_untimed_sixteen_a_line(sessions_dir):
    started = debug.route_test_record_start(debug.RecordBody(name="new", format="raw"))
    assert started["file"] == "new.raw"
    _feed(PACKETS)
    debug.route_test_record_stop()

    data = bytes(b for p in PACKETS for b in p)
    expected = "\n".join(
        " ".join(f"{b:02x}" for b in data[i : i + 16]) for i in range(0, len(data), 16)
    )
    assert (sessions_dir / "new.raw").read_text() == expected + "\n"


def test_raw_is_off_once_a_serial_recording_starts(sessions_dir):
    debug.route_test_record_start(debug.RecordBody(name="a", format="raw"))
    debug.route_test_record_start(debug.RecordBody(name="b"))
    _feed(PACKETS)
    debug.route_test_record_stop()

    assert not (sessions_dir / "b.serial").read_text().startswith(("a6", "fc"))
    assert (sessions_dir / "a.raw").read_text() == ""


def test_stop_detaches_before_closing(sessions_dir):
    debug.route_test_record_start(debug.RecordBody(name="x", format="raw"))
    debug.route_test_record_stop()
    assert state._record_handle is None
    worker._record_raw_byte(0x01)  # a byte arriving just after Stop: ignored
    assert os.path.getsize(sessions_dir / "x.raw") == 0


def test_raw_recording_is_listed_and_accepted(sessions_dir):
    assert ".raw" in debug.SESSION_UPLOAD_EXTS
    debug.route_test_record_start(debug.RecordBody(name="new", format="raw"))
    debug.route_test_record_stop()
    names = [s["name"] for s in worker._list_sessions() if s["source"] == "custom"]
    assert "new.raw" in names


def test_deleting_a_session_stays_inside_the_sessions_folder(sessions_dir):
    """The name is the request's: `../` must not reach a recording elsewhere."""
    inside = sessions_dir / "sessions"
    inside.mkdir()
    outside = sessions_dir / "elsewhere.raw"
    outside.write_text("")
    kept = inside / "kept.raw"
    kept.write_text("")
    state.CUSTOM_SESSIONS_FOLDER = str(inside)

    debug.route_test_session_delete(debug.NameBody(name="../elsewhere.raw"))
    assert outside.exists()

    debug.route_test_session_delete(debug.NameBody(name="kept.raw"))
    assert not kept.exists()
