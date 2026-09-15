"""A test session must leave the meet and the cloud exactly as it found them.

Running a recording used to be a manual dance: delete the meet file, play the
session, re-upload the meet. The UI enforced it — the Play buttons were disabled
whenever a meet was loaded — because a recording's event and heat numbers only
line up with the start lists in the companion `.lxf` beside it.

Two things made that dance load-bearing rather than merely annoying:

* `_cleanup_test_meet()` deleted **every** `.lxf` and `.csv` in `MEET_FOLDER`.
  That folder holds the list of uploaded meet files with one of them active, so
  it was deleting meets the test had never touched. The "no meet loaded" guard is
  all that stood between an operator and losing the lot.
* `state.apply_meet_profile()` seeds a profile for any unknown meet uid, so
  loading the companion through the normal path would have written a
  `meet_profiles` entry for the recording and overwritten the real meet's cloud
  title, picker image and home icon.

So the test meet now lives in `TEST_MEET_FOLDER` and only ever in memory: the
real files are never touched, and ending the session is a re-read from disk.

The other half is the cloud. A replay is invented times, and publishing them
under a live meet's identity would show spectators a recording as if it were the
race in front of them. The relay is stopped for the duration rather than
filtered — the cloud already derives `meet_live` from relay connect/disconnect,
so it shows the meet offline with no cloud-side change, and there is no open
socket for a frame to escape on.

Qt-free: the routes are driven directly.
"""
import asyncio
import io
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import relay                     # noqa: E402
import routes.debug as debug     # noqa: E402
import state                     # noqa: E402
import worker                    # noqa: E402

RECORDINGS = os.path.join(REPO, 'server', 'console_recordings')
# A built-in recording that ships with a companion .lxf beside it. The companion
# holds event 1; the file standing in for the operator's own meet below holds
# event 3, so "which meet is loaded" is a question the assertions can actually ask.
SESSION        = '50m_sprint.cts'
COMPANION      = '50m_sprint.lxf'
COMPANION_EVENT = 1
REAL_MEET       = '200m_medley_2heats.lxf'
REAL_EVENT      = 3


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """A meet folder, a test-meet folder, and no threads or sockets.

    `_restart_worker` is stubbed out: it sleeps 0.3s and starts a worker thread
    that would read a real recording. Everything under test here happens on the
    calling thread, before and after that.
    """
    meet_dir = tmp_path / 'meet'
    test_dir = tmp_path / 'test_meet'
    meet_dir.mkdir()
    test_dir.mkdir()
    for mod in (state, debug.state, worker.state, relay.state):
        monkeypatch.setattr(mod, 'MEET_FOLDER', str(meet_dir), raising=False)
        monkeypatch.setattr(mod, 'TEST_MEET_FOLDER', str(test_dir), raising=False)

    started = []
    monkeypatch.setattr(debug, '_restart_worker', lambda *a, **k: None)
    monkeypatch.setattr(debug.bus, 'run_bg', lambda fn, *a, **k: started.append((fn, a)))
    emitted = []
    for mod in (debug, worker):
        monkeypatch.setattr(mod.bus, 'emit',
                            lambda ch, ev, d=None: emitted.append((ch, ev, d)))
    monkeypatch.setattr(worker, 'send_event_info', lambda: None)
    monkeypatch.setattr(debug, 'send_event_info', lambda: None)

    monkeypatch.setattr(state, '_test_session', None, raising=False)
    monkeypatch.setattr(state, '_test_meet_active', False, raising=False)
    monkeypatch.setattr(state, '_test_meet_name', '', raising=False)
    monkeypatch.setattr(state, '_test_local_only', False, raising=False)
    monkeypatch.setattr(state, '_test_relay_was_running', False, raising=False)
    monkeypatch.setattr(state, '_test_saved_results', None, raising=False)
    monkeypatch.setattr(state, '_active_meet_file', '', raising=False)
    monkeypatch.setattr(state, 'in_speed', 1.0, raising=False)

    class Rig:
        meet_folder = meet_dir
        test_folder = test_dir
        events      = emitted

    return Rig()


def _load_real_meet(rig, name='provincials.lxf'):
    """Put a meet file in MEET_FOLDER and make it the active one.

    A different meet from the recording's companion, so `state.meet` says which
    of the two is loaded at any moment.
    """
    import shutil
    dest = rig.meet_folder / name
    shutil.copy2(os.path.join(RECORDINGS, REAL_MEET), dest)
    state._active_meet_file = name
    state.set_lenex(debug.load_lenex(str(dest)))
    assert sorted(state.meet.start_list) == [REAL_EVENT]
    return dest


def _loaded_events():
    return sorted(state.meet.start_list)


# ── The meet is set aside, not deleted ─────────────────────────────────────────

def test_a_session_starts_with_a_meet_loaded(rig):
    """The whole point: no delete-and-re-upload.

    And the replay gets the recording's own start lists — the reason the meet had
    to come out in the first place, since its event numbers are the ones the
    recording's packets refer to.
    """
    _load_real_meet(rig)
    out = debug._test_play(SESSION)

    assert out == {'ok': True}
    assert state._test_meet_active, 'the recording brought no start lists'
    assert state._test_meet_name == COMPANION
    assert _loaded_events() == [COMPANION_EVENT], \
        'the replay is running against the operator\'s meet, not its own'


def test_the_real_meet_file_is_never_touched(rig):
    """It stays in MEET_FOLDER for the whole session, which is what makes a power
    cut mid-test a non-event: `load_settings()` finds it at boot where it was."""
    dest = _load_real_meet(rig)
    before = dest.read_bytes()

    debug._test_play(SESSION)
    assert dest.exists(), 'the meet file was moved or deleted'
    assert state._active_meet_file == 'provincials.lxf', 'the meet stopped being active'

    worker.end_test_session()
    assert dest.read_bytes() == before
    assert state._active_meet_file == 'provincials.lxf'


def test_the_meet_comes_back_when_the_session_ends(rig):
    _load_real_meet(rig)
    debug._test_play(SESSION)
    assert _loaded_events() == [COMPANION_EVENT]

    worker.end_test_session()

    assert not state._test_meet_active
    assert state._test_meet_name == ''
    assert _loaded_events() == [REAL_EVENT], 'the meet was not reloaded'


def test_no_meet_before_means_no_meet_after(rig):
    """Nothing loaded is a state to restore too — the companion must not linger."""
    debug._test_play(SESSION)
    assert _loaded_events() == [COMPANION_EVENT]

    worker.end_test_session()
    assert _loaded_events() == [], 'the recording\'s start lists outlived the test'


def test_the_cleanup_no_longer_deletes_other_meet_files(rig):
    """The regression that motivated the rewrite.

    Several meet files uploaded, none of them active — which passed the old
    "no meet loaded" guard — and a test session used to take all of them.
    """
    import shutil
    names = ['march.lxf', 'april.lxf', 'club_champs.csv']
    for name in names:
        shutil.copy2(os.path.join(RECORDINGS, COMPANION), rig.meet_folder / name)

    debug._test_play(SESSION)
    worker.end_test_session()

    assert sorted(p.name for p in rig.meet_folder.iterdir()) == sorted(names)


def test_nothing_of_the_test_is_left_in_the_meet_folder(rig):
    """The companion is loaded from where it lives, never copied in."""
    debug._test_play(SESSION)
    assert list(rig.meet_folder.iterdir()) == []


def test_the_meet_profile_is_not_disturbed(rig, monkeypatch):
    """`apply_meet_profile` seeds an entry for an unknown uid, so routing the
    companion through the normal load path would invent a profile for the
    recording and overwrite the real meet's cloud title and images."""
    monkeypatch.setitem(state.settings, 'meet_profiles', {})
    monkeypatch.setitem(state.settings, 'cloud_meet_title', 'Provincial Championship')
    _load_real_meet(rig)
    state._active_meet_uid = 'realuid123'

    debug._test_play(SESSION)
    assert state.settings['meet_profiles'] == {}, 'the recording got a profile'
    assert state.settings['cloud_meet_title'] == 'Provincial Championship'
    assert state._active_meet_uid == 'realuid123', 'the meet changed identity'

    worker.end_test_session()
    assert state.settings['cloud_meet_title'] == 'Provincial Championship'


def test_an_uploaded_test_meet_lands_outside_the_meet_folder(rig):
    class _Upload:
        filename = 'improvised.lxf'

        def __init__(self):
            with open(os.path.join(RECORDINGS, COMPANION), 'rb') as f:
                self.file = io.BytesIO(f.read())

    state._test_session = 'anything.cts'
    out = debug._test_meet_upload(_Upload())

    assert out['ok'] is True, out
    assert (rig.test_folder / 'improvised.lxf').exists()
    assert list(rig.meet_folder.iterdir()) == [], 'a test meet reached MEET_FOLDER'

    worker.end_test_session()
    assert list(rig.test_folder.iterdir()) == [], 'the test meet outlived the test'


# ── Keeping the session off the cloud ──────────────────────────────────────────

@pytest.fixture
def cloud(monkeypatch):
    """A relay that records what it would have sent, and whether it was running."""
    sent    = []
    running = {'on': True}
    monkeypatch.setattr(relay, '_send_raw',
                        lambda ws, event, data: sent.append((event, data)))
    monkeypatch.setattr(relay, 'status', lambda: {'running': running['on'],
                                                  'connected': running['on'],
                                                  'url': '', 'stats': None})
    monkeypatch.setattr(relay, 'stop', lambda: running.update(on=False))
    monkeypatch.setattr(relay, 'start', lambda: running.update(on=True))
    monkeypatch.setattr(relay, '_client', object(), raising=False)
    monkeypatch.setattr(relay, '_connected', True, raising=False)

    class Cloud:
        pass
    Cloud.sent    = sent
    Cloud.running = running
    return Cloud


def test_local_only_sends_the_cloud_nothing(rig, cloud):
    debug._test_play(SESSION, local_only=True)
    assert state._test_local_only
    assert not cloud.running['on'], 'the relay was left connected'

    # Belt and braces: the guards hold even if the thread is mid-reconnect.
    relay.relay_emit('update_scoreboard', {'lane_time1': '58.12'})
    relay.update_metadata()
    relay.send_schedule()
    assert cloud.sent == []


def test_the_cloud_comes_back_only_if_it_was_there(rig, cloud):
    debug._test_play(SESSION, local_only=True)
    worker.end_test_session()
    assert cloud.running['on'], 'the cloud was not restored'
    assert not state._test_local_only

    cloud.running['on'] = False          # operator had the cloud switched off
    debug._test_play(SESSION, local_only=True)
    worker.end_test_session()
    assert not cloud.running['on'], 'a test switched the cloud on behind the operator'


def test_a_replay_result_does_not_reach_the_cloud_on_the_next_connect(rig, cloud,
                                                                      monkeypatch):
    """`relay._run` re-sends `_last_results_snapshot` on every reconnect, so a
    replay's results would arrive at the cloud long after the test ended."""
    real = {'event': 3, 'lanes': [{'lane': 1, 'time': '58.12'}]}
    monkeypatch.setattr(state, '_last_results_snapshot', real, raising=False)

    debug._test_play(SESSION, local_only=True)
    state._last_results_snapshot = {'event': 99, 'lanes': [{'lane': 1, 'time': '1.00'}]}
    worker.end_test_session()

    assert state._last_results_snapshot == real


def test_a_meet_loaded_forces_local_only(rig, cloud):
    """Not the operator's choice: a replay under a live meet's identity would show
    spectators invented times as the race in front of them."""
    _load_real_meet(rig)
    debug._test_play(SESSION, local_only=False)
    assert state._test_local_only
    assert not cloud.running['on']


def test_without_a_meet_the_choice_is_honoured(rig, cloud):
    debug._test_play(SESSION, local_only=False)
    assert not state._test_local_only
    assert cloud.running['on'], 'the cloud was dropped for a session meant to reach it'


def test_the_status_reports_what_the_checkbox_should_show(rig, monkeypatch):
    monkeypatch.setitem(state.settings, 'test_local_only', True)
    assert debug.route_test_status()['local_only'] is True
    assert debug.route_test_status()['local_only_forced'] is False

    monkeypatch.setitem(state.settings, 'test_local_only', False)
    assert debug.route_test_status()['local_only'] is False

    _load_real_meet(rig)
    status = debug.route_test_status()
    assert status['local_only'] is True and status['local_only_forced'] is True, \
        'the checkbox would offer a choice the route overrides'


# ── Ending a session ───────────────────────────────────────────────────────────

def test_ending_wipes_the_boards_and_resets_the_speed(rig):
    state.in_speed = 10.0
    debug._test_play(SESSION)
    worker.end_test_session()

    events = [(ch, ev) for ch, ev, _ in rig.events]
    assert ('/scoreboard', 'reset') in events, 'the replay was left on the boards'
    assert ('/scoreboard', 'test_mode') in events
    assert state.in_speed == 1.0


def test_the_wipe_lands_after_the_meet_is_restored(rig, monkeypatch):
    """Order matters: a board told to reset repaints from whatever meet is loaded
    at that moment, so a wipe sent first would repaint the recording's start
    lists — the very thing the wipe exists to get rid of."""
    _load_real_meet(rig)
    debug._test_play(SESSION)

    seen = []
    monkeypatch.setattr(worker.bus, 'emit',
                        lambda ch, ev, d=None: seen.append((ev, _loaded_events())))
    worker.end_test_session()

    resets = [events for ev, events in seen if ev == 'reset']
    assert resets, 'no wipe was sent'
    assert resets[0] == [REAL_EVENT], \
        'the boards were wiped while the recording was still loaded'


def test_stop_and_a_recording_running_out_end_the_same_way(rig):
    """An operator should not be able to tell which ending they got."""
    debug._test_play(SESSION)
    debug.route_test_stop()
    from_stop = [(ch, ev) for ch, ev, _ in rig.events if ev in ('reset', 'test_mode')]

    rig.events.clear()
    debug._test_play(SESSION)
    worker.end_test_session()          # what _run_test_session calls
    natural = [(ch, ev) for ch, ev, _ in rig.events if ev in ('reset', 'test_mode')]

    assert from_stop == natural


def test_a_second_play_does_not_lose_the_real_state(rig, cloud, monkeypatch):
    """The Play buttons are disabled while a session runs; the API is not.

    A second start used to save the *first test's* results as the thing to restore,
    and record the relay as already stopped — so it never came back on.
    """
    real = {'event': 3, 'lanes': []}
    monkeypatch.setattr(state, '_last_results_snapshot', real, raising=False)

    debug._test_play(SESSION, local_only=True)
    state._last_results_snapshot = {'event': 99, 'lanes': []}     # the replay's
    debug._test_play(SESSION, local_only=True)
    worker.end_test_session()

    assert state._last_results_snapshot == real
    assert cloud.running['on'], 'the cloud never came back'


# ── The heat that belonged to the last session ─────────────────────────────────
# `send_event_info` asks the *decoder* which event and heat is current, then looks
# the lanes up in whatever meet is loaded now. Swapping the meet without clearing
# that publishes the previous session's numbers against a meet that does not have
# them: a plausible-looking EV/HT over eight blank lanes.
#
# It showed up as "only the first recording I play shows names". The first one after
# a restart found the decoder on its `(0, 0)` sentinel and was fine; every one after
# it opened on the last session's heat, and the names appeared only once the
# recording announced its own event.

def _decoder(monkeypatch, at=(0, 0)):
    import state
    from console_decoders import make_decoder
    decoder = make_decoder('cts_gen6', {**state.settings, 'num_lanes': 8})
    decoder.last_event_sent = at
    monkeypatch.setattr(state, '_decoder', decoder, raising=False)
    return decoder


def _name_frames(rig):
    """Frames carrying lane names, and whether each one actually has any."""
    out = []
    for _, event, data in rig.events:
        if event == 'update_scoreboard' and any(k.startswith('lane_name') for k in data):
            out.append(any(v for k, v in data.items() if k.startswith('lane_name')))
    return out


def test_a_second_session_does_not_open_on_the_previous_heat(rig, monkeypatch):
    """The decoder is left where the last recording finished. This one is a
    different event, so its number over this meet's lanes is nobody at all."""
    import state
    _decoder(monkeypatch, at=(3, 2))          # where 200m_medley_2heats ends

    emitted = []
    monkeypatch.setattr(debug.bus, 'emit',
                        lambda ch, ev, d=None: emitted.append((ev, d)))
    monkeypatch.setattr(debug, 'send_event_info', __import__(
        'meet_data').send_event_info)
    monkeypatch.setattr(__import__('meet_data'), 'bus',
                        type('B', (), {'emit': staticmethod(
                            lambda ch, ev, d=None: emitted.append((ev, d)))})())
    monkeypatch.setattr(__import__('meet_data'), 'relay',
                        type('R', (), {'relay_emit': staticmethod(
                            lambda ev, d=None: None)})())

    debug._test_play(SESSION)

    assert state._decoder.last_event_sent == (0, 0), (
        'the decoder still points at the previous session')
    burst = [d for ev, d in emitted if ev == 'update_scoreboard']
    assert burst, 'no start-of-session broadcast at all'
    assert burst[0].get('current_event') == '', (
        f"published {burst[0].get('current_event')!r} — the last session's event")


def test_the_console_gets_its_own_heat_back_when_the_test_ends(rig, monkeypatch):
    """The way out is not a clear, and the difference matters on real hardware.

    A CTS re-announces its event and heat several times a second, so forgetting
    costs it nothing. A Quantum announces once, when the heat is readied (`A='0'`,
    "ready at start") — a board told to forget would sit with no event and no names
    until somebody readied the next heat, so a test session run mid-meet would cost
    the operator the heat they were on.

    What the console last said is restored instead; the replay's heat is what goes.
    """
    import state
    _decoder(monkeypatch, at=(7, 3))          # where the console was, mid-meet
    _load_real_meet(rig)

    debug._test_play(SESSION)
    assert state._decoder.last_event_sent == (0, 0), 'the replay inherited a heat'
    state._decoder.last_event_sent = (1, 1)   # where the recording got to

    worker.end_test_session()
    assert state._decoder.last_event_sent == (7, 3), (
        'the console lost the heat it was on')


def test_the_recording_s_times_do_not_survive_the_restore(rig, monkeypatch):
    """Keeping the heat must not mean keeping the replay's lanes with it."""
    import state
    decoder = _decoder(monkeypatch, at=(7, 3))
    debug._test_play(SESSION)
    decoder.lane_times = {1: '1:02.47'} if hasattr(decoder, 'lane_times') else {}
    decoder._slots[1][2] = 0x2F               # a time in lane 1's buffer

    worker.end_test_session()
    assert all(not any(slot for slot in decoder._slots[lane][2:8])
               for lane in range(1, 9)), 'the replay left times in the decoder'


def test_nothing_is_carried_over_into_the_next_session(rig, monkeypatch):
    """The saved heat is consumed, not kept — two sessions in a row must not make
    the second restore the first one's console state."""
    import state
    _decoder(monkeypatch, at=(7, 3))
    debug._test_play(SESSION)
    worker.end_test_session()
    assert state._test_saved_heat is None

    debug._test_play(SESSION)
    worker.end_test_session()
    assert state._decoder.last_event_sent == (7, 3)


def test_the_next_announcement_always_counts_as_a_change(rig, monkeypatch):
    """Clearing it does a second job: a recording that opens on the same event and
    heat the last one ended with would otherwise be *no change* to the decoder, so
    `event_changed` never fires and the names never load at all."""
    import state
    decoder = _decoder(monkeypatch, at=(1, 1))
    debug._test_play(SESSION)                  # 50m_sprint — announces (1, 1)
    assert state._decoder.last_event_sent == (0, 0)

    # Feed the recording's own announcement: it must register as a change.
    updates = {}
    for packet in _announcement_packets('50m_sprint'):
        updates = decoder.feed(packet)
        if 'event_changed' in updates:
            break
    assert updates.get('event_changed') == (1, 1), updates


def _announcement_packets(name):
    """The packets of a recording, up to and including its event announcement."""
    import re
    path = os.path.join(RECORDINGS, name + '.cts')
    packets, packet = [], []
    for match in re.finditer(r'\[[0-9.]+\]\s*|([0-9a-fA-F]{2})', open(path).read()):
        if not match.group(1):
            continue
        byte = int(match.group(1), 16)
        if byte & 0x80 and packet:
            packets.append(packet)
            packet = []
        packet.append(byte)
        if len(packets) > 3:
            break
    return packets
