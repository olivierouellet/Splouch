"""What a console keeps sending after the last swimmer has touched.

The Qt board's header clock is guarded by `heat_is_done()` — a `running_time`
frame arriving with the heat over is ignored. That guard only earns its place if
consoles really do keep streaming their clock past the end of a race, so this
pins the premise against the recordings the project ships rather than against an
assumption about hardware nobody here can plug in.

They do, and by a wide margin: a few hundred frames per heat, counting on past the
winning time until the operator resets the console. Without the guard the header
blanked as the last lane stopped and the very next frame started it running again.

Qt-free: the decoder and the worker's framing are driven directly.
"""
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

RECORDINGS = os.path.join(REPO, 'server', 'console_recordings')
# The CTS recordings with a finished heat in them — the authored ones. The captured
# `.raw` never reaches a finish, so it has nothing to say here.
WITH_A_RACE = ['50m_sprint.cts', '100m_freestyle.cts',
               '200m_medley_2heats.cts', '50m_sprint_2heats.cts']


def _replay(name, monkeypatch):
    """Every `update_scoreboard` payload the worker would emit for a recording."""
    import state
    from console_decoders import make_decoder

    frames = []
    monkeypatch.setitem(state.settings, 'num_lanes', 8)
    monkeypatch.setattr(state, '_decoder', make_decoder('cts_gen6', state.settings),
                        raising=False)
    monkeypatch.setattr(state, 'update', {}, raising=False)
    monkeypatch.setattr(state, '_running_lanes', set(), raising=False)

    import worker
    monkeypatch.setattr(worker.bus, 'emit',
                        lambda ch, ev, d=None: frames.append(d)
                        if ev == 'update_scoreboard' else None)
    monkeypatch.setattr(worker.relay, 'relay_emit', lambda ev, d=None: None)
    monkeypatch.setattr(worker.bus, 'run_bg', lambda fn, *a, **k: None)
    monkeypatch.setattr(worker, 'send_event_info', lambda: None)

    text = open(os.path.join(RECORDINGS, name), encoding='utf-8').read()
    packet = []
    for match in re.finditer(r'\[([0-9.]+)\]\s*|([0-9a-fA-F]{2})', text):
        if not match.group(1):
            packet = worker._ingest_byte(int(match.group(2), 16), packet)
    if packet:
        worker._handle_packet(packet)
    return frames


def _tail_clock_frames(frames):
    """`running_time` values that arrive with no lane running, after a race."""
    running, seen_a_race, tail = {}, False, []
    for frame in frames:
        for key, value in frame.items():
            if key.startswith('lane_running'):
                running[key] = bool(value)
        if any(running.values()):
            seen_a_race = True
        elif seen_a_race and 'running_time' in frame:
            tail.append(frame['running_time'])
    return tail


@pytest.mark.parametrize('name', WITH_A_RACE)
def test_the_console_keeps_its_clock_running_after_the_heat(name, monkeypatch):
    tail = _tail_clock_frames(_replay(name, monkeypatch))
    assert len(tail) > 50, (
        f'{name} carries only {len(tail)} post-race clock frames — if consoles have '
        'stopped doing this, the guard in BoardWindow.apply_update can go')
    assert tail[0] != tail[-1], 'the clock is frozen, not counting on'


def test_the_recordings_are_actually_being_decoded(monkeypatch):
    """A guard against this file passing because the replay produced nothing."""
    frames = _replay('50m_sprint.cts', monkeypatch)
    assert len(frames) > 100, f'only {len(frames)} frames — the framing is wrong'
    assert any('lane_time1' in f for f in frames), 'no lane times were decoded'
    assert any(f.get('current_event') for f in frames), 'no event was decoded'
