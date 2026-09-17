"""The recordings in `server/console_recordings/`, checked as data.

They are the project's only fixture for a real race: the Test tab replays them,
`test_console_tail_frames.py` reads a protocol fact off them, and an operator
learning the board sees one before they see a meet. Nothing else checks that what
they contain is coherent, so a typo in a hand-authored packet would surface as a
board that misbehaves for reasons nobody could trace back to the data.

The 100m and 200m carry 50m splits: the lane's running bit drops, the packet
carries the split time, the console holds it for three seconds, then the bit comes
back. Two properties of that are load-bearing:

* **The split time matches the race clock** at the moment of the touch. They come
  from different channels, so nothing but care keeps them in step.
* **A split carries no place.** A place is awarded at the finish, and the board
  reads "time but no place" as *still being placed* — which is what stops eight
  lanes resting at the same wall from satisfying `heat_is_done()` and tinting a
  podium in the middle of a race.

The `.raw` captures are deliberately not covered: they are a console's own output
rather than authored data, so there is nothing to hold them to beyond what they are.

Qt-free: the decoder and the worker's framing are driven directly.
"""
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from conftest import settings_markup  # noqa: E402
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

RECORDINGS = os.path.join(REPO, 'server', 'console_recordings')

# name -> (event number, heat count, lane count, title in the companion .lxf)
AUTHORED = {
    '50m_sprint':         (1, 1, 8, '50m Freestyle'),
    '50m_sprint_2heats':  (1, 2, 8, '50m Freestyle'),
    '100m_freestyle':     (2, 1, 6, '100m Freestyle'),
    '200m_medley_2heats': (3, 2, 8, '200m Medley'),
}
# How many 50m splits each race should carry, per lane.
SPLITS = {'50m_sprint': 0, '50m_sprint_2heats': 0,
          '100m_freestyle': 1, '200m_medley_2heats': 3}

# Seconds between the event announcement — which is what puts names on the board —
# and the first lane going active. Long enough to read a heat of eight names across
# a hall, which is most of what an operator is watching a replay to check.
START_LIST_SECONDS = {'50m_sprint': 8.0, '50m_sprint_2heats': 8.0,
                      '100m_freestyle': 11.0, '200m_medley_2heats': 8.0}

HOLD = 3.0          # seconds a split stays on the display


def _packets(name):
    """[(timestamp, [bytes])] for one recording."""
    out = []
    path = os.path.join(RECORDINGS, name + '.cts')
    for line in open(path, encoding='utf-8'):
        match = re.match(r'\[([0-9.]+)\]\s*(.*)', line.strip())
        if match:
            out.append((float(match.group(1)),
                        [int(b, 16) for b in match.group(2).split()]))
    return out


def _digit(byte):
    value = (byte & 0x0F) ^ 0x0F
    return ' ' if value > 9 else str(value)


def _decode(packet):
    """(channel, running, {slot: char}) — the wire format, spelled out."""
    header = packet[0]
    if header & 0x01:
        return None, False, {}
    channel = ((header & 0x3E) >> 1) ^ 0x1F
    slots = {}
    for byte in packet[1:]:
        slots[(byte >> 4) & 0x0F] = _digit(byte)
    return channel, bool(header & 0x40), slots


def _seconds(slots):
    """Slots 2-7 as seconds, or None when the time is blank."""
    text = ''.join(slots.get(i, ' ') for i in range(2, 8))
    if not text.strip():
        return None
    mins, secs, hund = text[0:2], text[2:4], text[4:6]
    return ((int(mins) if mins.strip() else 0) * 60
            + (int(secs) if secs.strip() else 0)
            + (int(hund) if hund.strip() else 0) / 100)


def _events(name):
    """Every lane packet and the race clock at that moment."""
    stops, clock = [], None
    for ts, packet in _packets(name):
        channel, running, slots = _decode(packet)
        if channel == 0:
            clock = _seconds(slots)
        elif channel in range(1, 11):
            stops.append({'at': ts, 'lane': channel, 'running': running,
                          'time': _seconds(slots), 'place': slots.get(1, ' '),
                          'clock': clock})
    return stops


def _races(name):
    """The lane packets of each race, split on the event/heat announcements.

    A two-heat recording is two races, and almost nothing below is true across the
    boundary — a lane finishes heat 1 with a place and then swims again.
    """
    races, current = [], None
    clock = None
    for ts, packet in _packets(name):
        channel, running, slots = _decode(packet)
        if channel == 12:
            ev = ''.join(slots.get(i, ' ') for i in range(3)).strip()
            ht = ''.join(slots.get(i, ' ') for i in range(5, 8)).strip()
            if ev and ht:
                current = []
                races.append(current)
        elif channel == 0:
            clock = _seconds(slots)
        elif channel in range(1, 11) and current is not None:
            current.append({'at': ts, 'lane': channel, 'running': running,
                            'time': _seconds(slots), 'place': slots.get(1, ' '),
                            'clock': clock})
    return races


def _holds(race):
    """One entry per *touch*, not per packet.

    A console repaints a held split every half second, so the raw stream carries
    the same time several times over. The touch is the first of them: it is what
    carries the moment, and the refreshes only keep it on the display.
    """
    out, last = [], {}
    for stop in race:
        if stop['running']:
            last.pop(stop['lane'], None)
            continue
        if last.get(stop['lane']) == stop['time']:
            continue                       # a refresh of the hold already recorded
        last[stop['lane']] = stop['time']
        out.append(stop)
    return out


def _resume_after(race, hold):
    """When *hold*'s lane starts running again, or None if that was its finish."""
    for stop in race:
        if stop['lane'] == hold['lane'] and stop['at'] > hold['at'] and stop['running']:
            return stop
    return None


# ── Titles and shape ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('name', sorted(AUTHORED))
def test_the_recording_matches_its_companion_meet_file(name):
    """A recording's event and heat numbers only mean anything against the start
    lists beside it — that pairing is the whole reason a test session loads one."""
    from meet_parsers.lenex_parser import load_lenex
    event, heats, lanes, title = AUTHORED[name]
    meet = load_lenex(os.path.join(RECORDINGS, name + '.lxf'))

    assert meet.event_names.get(event) == title, meet.event_names
    assert sorted(meet.start_list[event]) == list(range(1, heats + 1))
    for heat, entries in meet.start_list[event].items():
        assert len(entries) == lanes, f'heat {heat} has {len(entries)} lanes'
        for lane, entry in entries.items():
            assert entry['name'].strip(), f'lane {lane} has no swimmer'


@pytest.mark.parametrize('name', sorted(AUTHORED))
def test_the_packets_announce_that_event_and_those_heats(name):
    event, heats, _, _ = AUTHORED[name]
    seen = []
    for _, packet in _packets(name):
        channel, _, slots = _decode(packet)
        if channel == 12:
            ev = ''.join(slots.get(i, ' ') for i in range(3)).strip()
            ht = ''.join(slots.get(i, ' ') for i in range(5, 8)).strip()
            if ev and ht:
                seen.append((int(ev), int(ht)))
    assert seen == [(event, h) for h in range(1, heats + 1)], seen


@pytest.mark.parametrize('name', sorted(AUTHORED))
def test_every_lane_in_the_start_list_swims(name):
    _, heats, lanes, _ = AUTHORED[name]
    finishes = [s for s in _events(name) if not s['running'] and s['place'] != ' ']
    assert len(finishes) == heats * lanes
    assert sorted(s['lane'] for s in finishes) == \
        sorted(list(range(1, lanes + 1)) * heats)


@pytest.mark.parametrize('name', sorted(AUTHORED))
def test_the_places_agree_with_the_times(name):
    """A board that showed place 1 beside the third-fastest time would be obeying
    the data, and nobody would think to look here."""
    _, heats, lanes, _ = AUTHORED[name]
    finishes = [s for s in _events(name) if not s['running'] and s['place'] != ' ']
    for heat in range(heats):
        batch = finishes[heat * lanes:(heat + 1) * lanes]
        by_time = sorted(batch, key=lambda s: s['time'])
        for rank, stop in enumerate(by_time, start=1):
            assert int(stop['place']) == rank, (
                f"{name} heat {heat + 1}: lane {stop['lane']} at {stop['time']}s "
                f"is place {stop['place']}, should be {rank}")


# ── The splits ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('name', sorted(AUTHORED))
def test_each_lane_carries_the_splits_it_should(name):
    _, heats, lanes, _ = AUTHORED[name]
    races = _races(name)
    assert len(races) == heats
    for heat, race in enumerate(races, start=1):
        per_lane = {}
        for hold in _holds(race):
            if hold['place'] == ' ' and hold['time']:
                per_lane.setdefault(hold['lane'], []).append(hold)
        if not SPLITS[name]:
            assert not per_lane, f'{name} heat {heat} should carry no splits'
            continue
        assert sorted(per_lane) == list(range(1, lanes + 1))
        for lane, holds in per_lane.items():
            assert len(holds) == SPLITS[name], (
                f'{name} heat {heat} lane {lane}: {len(holds)} splits')


@pytest.mark.parametrize('name', ['100m_freestyle', '200m_medley_2heats'])
def test_a_split_never_carries_a_place(name):
    """What keeps eight lanes at the same wall from reading as a finished heat: the
    board treats a time with no place as *still being placed*, so `heat_is_done()`
    stays false and no podium is tinted in the middle of a race."""
    for heat, race in enumerate(_races(name), start=1):
        for hold in _holds(race):
            if hold['place'] == ' ':
                continue
            assert _resume_after(race, hold) is None, (
                f"{name} heat {heat}: lane {hold['lane']} was placed at "
                f"{hold['time']}s and then swam on")


@pytest.mark.parametrize('name', ['100m_freestyle', '200m_medley_2heats'])
def test_a_split_time_matches_the_race_clock(name):
    """They arrive on different channels. Nothing but care keeps them in step, and
    a split half a second off its own clock is the kind of thing an operator
    notices on the TV and cannot explain.

    A tenth of tolerance: the clock channel ticks at 10Hz and reports tenths, so the
    nearest reading is up to one tick behind the touch.
    """
    for race in _races(name):
        for hold in _holds(race):
            if hold['place'] != ' ' or not hold['time']:
                continue
            assert hold['clock'] is not None
            # In hundredths: these are decimal times read off a wire, and
            # 30.3 - 30.2 is 0.10000000000000142 in binary floating point.
            drift = round((hold['time'] - hold['clock']) * 100)
            assert 0 <= drift <= 10, (
                f"lane {hold['lane']} split {hold['time']}s against a clock "
                f"reading {hold['clock']}s")


@pytest.mark.parametrize('name', ['100m_freestyle', '200m_medley_2heats'])
def test_a_split_is_held_for_three_seconds(name):
    """Long enough to read across a hall, and longer than `split_min_duration`, or
    the decoder never counts the length behind it."""
    for race in _races(name):
        for hold in _holds(race):
            if hold['place'] != ' ':
                continue               # a finish is held until the next heat
            resume = _resume_after(race, hold)
            assert resume is not None, f"lane {hold['lane']} never resumed"
            held = resume['at'] - hold['at']
            assert abs(held - HOLD) < 0.01, (
                f"lane {hold['lane']} held its split {held:.2f}s, not {HOLD}s")


@pytest.mark.parametrize('name', ['100m_freestyle', '200m_medley_2heats'])
def test_the_splits_run_in_order_and_land_inside_the_race(name):
    for heat, race in enumerate(_races(name), start=1):
        per_lane, finals = {}, {}
        for hold in _holds(race):
            if hold['place'] == ' ':
                per_lane.setdefault(hold['lane'], []).append(hold['time'])
            else:
                finals[hold['lane']] = hold['time']
        for lane, times in per_lane.items():
            assert times == sorted(times), f'heat {heat} lane {lane} out of order'
            assert times[0] > 0, f'heat {heat} lane {lane} has a split at zero'
            assert times[-1] < finals[lane], (
                f'heat {heat} lane {lane} splits past its own final time')


@pytest.mark.parametrize('name', ['100m_freestyle', '200m_medley_2heats'])
def test_the_lanes_reach_the_wall_in_the_order_they_finish(name):
    """Not a rule of the sport — swimmers do change places — but these are authored
    files, and a split order that contradicts the finish would be an accident rather
    than a race."""
    for race in _races(name):
        splits, finals = {}, {}
        for hold in _holds(race):
            if hold['place'] == ' ':
                splits.setdefault(hold['lane'], []).append(hold['time'])
            else:
                finals[hold['lane']] = hold['time']
        by_final = sorted(finals, key=lambda ln: finals[ln])
        for index in range(len(splits[by_final[0]])):
            by_split = sorted(splits, key=lambda ln: splits[ln][index])
            assert by_split == by_final, (
                f'split {index + 1} order {by_split} against finish {by_final}')


# ── The retired binary format ──────────────────────────────────────────────────
# `.cap` was the same bytes as a `.raw`, binary rather than hex, and every capture
# was kept as both — so the Test tab listed each one twice, as two rows that played
# identically, with nothing to tell an operator which was which. The binary copies
# are gone and so is the player that needed them; `cap-to-raw.py` is the one step a
# capture straight off a tool now takes before it can be used here.

CONVERTER = os.path.join(RECORDINGS, 'cap-to-raw.py')


def test_no_cap_is_offered_anywhere():
    """Docs, dialog and server have to agree, or an operator picks a file the
    server drops — the failure this suite was started over."""
    import routes.debug as debug
    assert '.cap' not in debug.SESSION_UPLOAD_EXTS

    settings = settings_markup()
    accept = re.search(r'accept="([^"]*)"[^>]*testUpload', settings)
    assert accept, 'the session upload input moved'
    assert '.cap' not in accept.group(1)
    assert sorted(accept.group(1).split(',')) == sorted(debug.SESSION_UPLOAD_EXTS)

    assert not [f for f in os.listdir(RECORDINGS) if f.endswith('.cap')]

    import worker
    assert not hasattr(worker, '_play_cap_file'), 'the dead player is still here'


def test_every_listed_session_is_a_format_we_still_play():
    import worker
    for session in worker._list_sessions():
        assert session['name'].endswith(('.cts', '.raw')), session['name']


def test_a_capture_appears_once_in_the_list():
    """The whole reason `.cap` went: each capture was two rows, not one."""
    import worker
    stems = [os.path.splitext(s['name'])[0] for s in worker._list_sessions()]
    assert len(stems) == len(set(stems)), sorted(stems)


# ── The converter ──────────────────────────────────────────────────────────────

def _convert(tmp_path, data, *args):
    import subprocess
    source = tmp_path / 'session.cap'
    source.write_bytes(data)
    done = subprocess.run([sys.executable, CONVERTER, str(source), *args],
                          capture_output=True, text=True)
    return done, source


def test_it_writes_the_hex_these_files_use(tmp_path):
    done, source = _convert(tmp_path, bytes([0xB4, 0x0A, 0x17, 0x20]))
    assert done.returncode == 0, done.stderr
    out = (tmp_path / 'session.raw').read_text(encoding='utf-8')
    assert out == 'b4 0a 17 20\n', repr(out)


def _as_binary(name):
    """A tracked `.raw` back in the binary form a capture tool writes.

    Built here rather than read from git history: the real `.cap` files were
    deleted, so a test that reached for `HEAD:…cap` passed for one commit and then
    skipped for good — which looks like coverage and is not.
    """
    text = open(os.path.join(RECORDINGS, name + '.raw'), encoding='utf-8').read()
    return bytes(int(b, 16) for b in re.findall(r'[0-9a-fA-F]{2}', text))


@pytest.mark.parametrize('name', ['real_console6'])
def test_it_reproduces_a_real_capture_exactly(tmp_path, name):
    """The proof that matters: a real capture's bytes come back as the very hex
    this repo tracks. Both recordings, both directions."""
    done, _ = _convert(tmp_path, _as_binary(name))
    assert done.returncode == 0, done.stderr

    converted = re.findall(r'[0-9a-f]{2}',
                           (tmp_path / 'session.raw').read_text(encoding='utf-8'))
    expected = re.findall(r'[0-9a-fA-F]{2}',
                          open(os.path.join(RECORDINGS, name + '.raw'),
                               encoding='utf-8').read())
    assert converted == [b.lower() for b in expected]


def test_it_will_not_overwrite_without_being_told(tmp_path):
    """A capture is not reproducible. Clobbering one on a typo is not recoverable."""
    done, _ = _convert(tmp_path, b'\xb4\x0a')
    assert done.returncode == 0
    (tmp_path / 'session.raw').write_text('do not lose me\n', encoding='utf-8')

    again, _ = _convert(tmp_path, b'\xb4\x0a')
    assert again.returncode == 1
    assert 'exists' in again.stderr
    assert (tmp_path / 'session.raw').read_text(encoding='utf-8') == 'do not lose me\n'

    forced, _ = _convert(tmp_path, b'\xb4\x0a', '--force')
    assert forced.returncode == 0
    assert (tmp_path / 'session.raw').read_text(encoding='utf-8') == 'b4 0a\n'


def test_it_says_when_there_is_no_meet_file_beside_it(tmp_path):
    """A capture carries no start lists, so the replay would run with blank names —
    which reads as a broken recording rather than a missing companion."""
    done, _ = _convert(tmp_path, b'\xb4\x0a')
    assert 'lxf' in done.stderr and 'names' in done.stderr


def test_an_empty_or_missing_file_is_refused(tmp_path):
    import subprocess
    done, _ = _convert(tmp_path, b'')
    assert done.returncode == 1 and 'empty' in done.stderr

    missing = subprocess.run([sys.executable, CONVERTER, str(tmp_path / 'nope.cap')],
                             capture_output=True, text=True)
    assert missing.returncode == 1 and missing.stderr.strip()


def test_the_converted_file_actually_replays(tmp_path):
    """Hex the player's own regex accepts, not just hex that looks right — and the
    same race out the far end."""
    done, _ = _convert(tmp_path, _as_binary('real_console6'))
    assert done.returncode == 0, done.stderr

    import state
    import worker
    from console_decoders import make_decoder
    state.settings['num_lanes'] = 8
    state._decoder = make_decoder('cts_gen6', state.settings)
    clock, packet = [], []

    def collect(updates):
        value = (updates or {}).get('running_time')
        if value and value.strip():
            clock.append(value)

    original = worker._emit_scoreboard_update
    text = (tmp_path / 'session.raw').read_text(encoding='utf-8')
    for match in re.finditer(r'[0-9a-fA-F]{2}', text):
        byte = int(match.group(0), 16)
        if byte & 0x80 and packet:
            collect(state._decoder.feed(packet))
            packet = []
        packet.append(byte)
    if packet:
        collect(state._decoder.feed(packet))

    # real_console6 is seventeen seconds of starts and resets and never reaches a
    # finish, so what proves the bytes survived is the running clock, not a result.
    assert clock, 'the converted capture produced no running time'
    assert max(clock) > '0:15', clock[-3:]


# ── Room to read the start list before the race ────────────────────────────────

@pytest.mark.parametrize('name', sorted(AUTHORED))
def test_the_start_list_is_on_screen_before_anyone_swims(name):
    """Pinned per file rather than as a floor: these are authored timings, and a
    regenerated recording that quietly went back to whatever the generator's default
    was would otherwise pass. The 100m carries longer than the rest on purpose.
    """
    announced = [ts for ts, packet in _packets(name) if _decode(packet)[0] == 12]
    for heat, (start, race) in enumerate(zip(announced, _races(name)), start=1):
        gap = min(stop['at'] for stop in race if stop['running']) - start
        assert abs(gap - START_LIST_SECONDS[name]) < 0.01, (
            f'{name} heat {heat}: {gap:.2f}s of start list, '
            f'expected {START_LIST_SECONDS[name]:.2f}s')


# ── Playback timing ────────────────────────────────────────────────────────────
# A packet is normally flushed by the arrival of the *next* packet's first byte
# (`worker._ingest_byte`). On a live wire that next byte is milliseconds away. On a
# recording it can be the whole gap — and every file here opens with its event
# announcement and then says nothing until the race starts. So the announcement sat
# in the buffer for the entire pre-race window, and the board showed no event and no
# names until the first lane went active.
#
# Driven through `worker._play_cts_file` itself, on a clock that only moves when the
# player sleeps. An earlier version of this check fed the bytes by hand and missed
# the bug twice: once by ignoring the timestamps, once by stamping them in the wrong
# order. The player is the thing under test, so the player is what runs.

class _FrozenClock:
    """`time` for the player: wall time only advances when it sleeps."""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        if seconds > 0:
            self.t += seconds


@pytest.fixture
def played(monkeypatch):
    """Play a recording for real and return [(recording time, frame), …]."""
    def run(name):
        import bus
        import relay
        import state
        import worker
        from console_decoders import make_decoder
        from meet_parsers.lenex_parser import load_lenex
        import meet_data

        clock = _FrozenClock()
        frames = []
        monkeypatch.setattr(worker, 'time', clock)
        # One patch, not two: `worker.bus` and `meet_data.bus` are the same module
        # object, so patching both replaced the collector with the second stub and
        # every frame vanished.
        monkeypatch.setattr(bus, 'emit',
                            lambda ch, ev, d=None: frames.append((clock.t, d))
                            if ev == 'update_scoreboard' else None)
        monkeypatch.setattr(relay, 'relay_emit', lambda ev, d=None: None)
        monkeypatch.setattr(worker, '_drain_cmds', lambda: None)
        monkeypatch.setitem(state.settings, 'num_lanes', 8)
        monkeypatch.setattr(state, '_worker_gen', 1, raising=False)
        monkeypatch.setattr(state, 'update', {}, raising=False)
        monkeypatch.setattr(state, '_running_lanes', set(), raising=False)
        monkeypatch.setattr(state, '_decoder',
                            make_decoder('cts_gen6', state.settings), raising=False)
        state.set_lenex(load_lenex(os.path.join(RECORDINGS, name + '.lxf')))

        worker._play_cts_file(os.path.join(RECORDINGS, name + '.cts'), 1)
        base = frames[0][0] if frames else 0.0
        return [(round(t - base, 2), f) for t, f in frames]
    return run


def _first(frames, predicate):
    return next((t for t, frame in frames if predicate(frame)), None)


@pytest.mark.parametrize('name', sorted(AUTHORED))
def test_the_names_arrive_with_the_announcement_not_with_the_race(name, played):
    """The symptom an operator sees: an empty board until somebody dives in."""
    frames = played(name)
    names = _first(frames, lambda f: f.get('lane_name1'))
    race = _first(frames, lambda f: any(k.startswith('lane_running') and v
                                        for k, v in f.items()))
    assert names is not None, 'no names were ever sent'
    assert race is not None and race > 1.0, 'the race starts immediately?'
    assert names < race - 1.0, (
        f'{name}: names at {names}s, race at {race}s — nothing to read beforehand')


@pytest.mark.parametrize('name', sorted(AUTHORED))
def test_the_event_number_arrives_at_the_announcement(name, played):
    frames = played(name)
    assert _first(frames, lambda f: f.get('current_event')) == 0.0


@pytest.mark.parametrize('name', sorted(AUTHORED))
def test_the_start_list_really_is_on_screen_for_as_long_as_it_claims(name, played):
    """The gap `START_LIST_SECONDS` promises, measured through the player rather
    than off the file: the two disagreed, and the file was not the one lying."""
    frames = played(name)
    names = _first(frames, lambda f: f.get('lane_name1'))
    race = _first(frames, lambda f: any(k.startswith('lane_running') and v
                                        for k, v in f.items()))
    assert abs((race - names) - START_LIST_SECONDS[name]) < 0.2, (
        f'{name}: {race - names:.2f}s between names and race, '
        f'expected {START_LIST_SECONDS[name]}s')
