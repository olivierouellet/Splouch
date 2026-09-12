import glob
import os
import queue
import re
import time
import traceback

import serial

import bus
import relay
import state
from meet_data import (
    delta_fields, get_event_name_display, get_event_name_parts,
    get_lane_alt, get_lane_parts,
    get_lane_seed_time, _get_next_heats, _build_results_snapshot, send_event_info,
)


def _auto_dismiss_overlay():
    if state._overlay_active:
        state._overlay_active = False
        bus.emit('/scoreboard', 'display_overlay', {'active': False})


def _cleanup_test_meet():
    if not state._test_meet_active:
        return
    for f in glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf')) + \
             glob.glob(os.path.join(state.MEET_FOLDER, '*.csv')):
        try:
            os.remove(f)
        except Exception:
            pass
    state.clear_meet()
    state._test_meet_active = False
    send_event_info()


def _list_sessions():
    result = []
    for folder, source in [(state.SESSIONS_FOLDER, 'builtin'),
                           (state.CUSTOM_SESSIONS_FOLDER, 'custom')]:
        for ext in ('*.cts', '*.raw', '*.cap'):
            for path in sorted(glob.glob(os.path.join(folder, ext))):
                result.append({'name': os.path.basename(path), 'source': source, 'path': path})
    return result


# ── Packet handler helpers ─────────────────────────────────────────────────────

def _on_event_changed(updates, ev, ht):
    m = state.meet
    updates['event_name']       = get_event_name_display(ev)
    updates['event_name_parts'] = get_event_name_parts(ev)
    updates['heat_time']  = m.heat_times.get(ev, {}).get(ht, '')
    pool_len = int(state.settings.get('pool_length', 25))
    dist     = m.event_distances.get(ev, 0)
    updates['expected_splits'] = (dist // pool_len) if (dist and pool_len) else 0
    seed_times = {}
    for i in range(1, 13):
        name, club = get_lane_parts(ev, ht, i)
        updates[f'lane_name{i}']     = name
        updates[f'lane_club{i}']     = club
        updates[f'lane_name_alt{i}'] = get_lane_alt(ev, ht, i)
        st = get_lane_seed_time(ev, ht, i)
        if st:
            seed_times[i] = st
    state._decoder.set_seed_times(seed_times)
    print(f'[seed_times] event={(ev, ht)} loaded: {seed_times}', flush=True)
    next_heats_data = {'heats': _get_next_heats(ev, ht,
                                                num_lanes=int(state.settings.get('num_lanes', 8)))}
    bus.emit('/results', 'next_heats', next_heats_data)
    relay.relay_emit('next_heats', next_heats_data)


def _add_lane_deltas(updates):
    for i in range(1, 13):
        lane_time = updates.get(f'lane_time{i}')
        if not lane_time:
            continue
        if not updates.get(f'lane_place{i}', ' ').strip():
            continue
        if i not in state._decoder.lane_seed_times:
            continue
        html, secs, better = delta_fields(lane_time, state._decoder.lane_seed_times[i])
        updates[f'lane_delta{i}']         = html
        updates[f'lane_delta_seconds{i}'] = secs
        updates[f'lane_delta_better{i}']  = better


def _packet_summary(updates):
    """Return a one-line human-readable summary of the update for the serial debug panel."""
    s = ''
    if 'current_event' in updates:
        s = ' Event:' + updates['current_event'] + ' Heat:' + updates['current_heat']
    if 'running_time' in updates:
        s = 'Running Time: ' + updates['running_time']
    for i in range(1, 13):
        if f'lane_time{i}' not in updates and not updates.get(f'lane_running{i}'):
            continue
        lane_time = updates.get(f'lane_time{i}', '')
        place     = updates.get(f'lane_place{i}', ' ')
        running   = updates.get(f'lane_running{i}', False)
        if not s:
            s = '%4s: %s %s' % (i, place, 'running' if running else lane_time)
    return s


def _emit_scoreboard_update():
    has_update = ('current_event' in state.update or
                  'running_time'  in state.update or
                  any(key.startswith('lane_') for key in state.update))
    if not has_update:
        return
    try:
        data = dict(state.update)
        bus.emit('/scoreboard', 'update_scoreboard', data)
        relay.relay_emit('update_scoreboard', data)
    except Exception as e:
        print(f'[emit error] {e}', flush=True)
        traceback.print_exc()
    state.update.clear()


def _lane_log_summary(updates):
    """(changed-fields, per-lane-snapshot) for a race-state log line.

    `changed` is the subset of this packet that can flip the finished state — i.e.
    the likely trigger of a flip. The snapshot shows every timed/placed lane as
    'L<lane>:<place>/<time>' so a flapping lane stands out across consecutive flips.
    """
    changed = {k: v for k, v in (updates or {}).items()
               if k == 'running_time'
               or k.startswith(('lane_running', 'lane_time', 'lane_place', 'current_'))}
    lanes = []
    for i in range(1, int(state.settings.get('num_lanes', 8)) + 1):
        t = state._decoder.get_lane_time(i)
        p = state._decoder.get_lane_place(i).strip()
        if t or p:
            lanes.append(f'L{i}:{p or "-"}/{t or "-"}')
    return changed, ' '.join(lanes)


def _on_race_state_changed(now_finished, updates=None):
    prev = state._results_prev_race_finished

    if now_finished != prev:
        changed, lanes = _lane_log_summary(updates)
        print(f'[race-state] finished {prev}->{now_finished} '
              f'trigger={changed} lanes=[{lanes}]', flush=True)

    if now_finished and not prev:
        state._last_results_snapshot = _build_results_snapshot()
        state._finish_timer_gen += 1
        def _finish_task(gen=state._finish_timer_gen, snap=state._last_results_snapshot):
            time.sleep(float(state.settings.get('finish_debounce', 3.0)))
            if state._finish_timer_gen == gen:
                print('[race-state] results confirmed', flush=True)
                bus.emit('/scoreboard', 'race_finished', {})
                bus.emit('/results', 'results_snapshot', snap)
                relay.relay_emit('results_snapshot', snap)
        bus.run_bg(_finish_task)

    elif not now_finished and prev:
        # Race left the finished state. Debounce the board wipe: a transient blip
        # at race end (a stray finish/place artifact from the console) must not
        # blank the board and flicker the display. Only reset if a genuine
        # re-start is still un-finished after the debounce window.
        state._finish_timer_gen += 1
        def _reset_task(gen=state._finish_timer_gen):
            time.sleep(float(state.settings.get('reset_debounce', 1.0)))
            # Hand the wipe to the worker so it runs on the decoder-owning thread.
            state._worker_cmds.put(lambda g=gen: _do_board_reset(g))
        bus.run_bg(_reset_task)

    state._results_prev_race_finished = now_finished


# ── Worker command queue ───────────────────────────────────────────────────────
# Operations that must touch the decoder but originate on another thread (the WS
# adjust_splits/next_heat handlers, the reset debounce) are posted to
# state._worker_cmds and executed here, on the decoder-owning worker thread, so the
# decoder is never accessed concurrently — no lock required.

def _drain_cmds():
    while True:
        try:
            cmd = state._worker_cmds.get_nowait()
        except queue.Empty:
            return
        try:
            cmd()
        except Exception:
            traceback.print_exc()


def _worker_adjust_splits(lane, delta):
    new_val = state._decoder.adjust_splits(lane, delta)
    bus.emit('/scoreboard', 'update_scoreboard', {f'lane_splits{lane}': new_val})


def _worker_next_heat():
    event_list = sorted(state.meet.event_info.events.keys())
    try:
        event_tuple = event_list[event_list.index(state._decoder.last_event_sent) + 1]
    except Exception:
        event_tuple = event_list[0] if event_list else (0, 0)
    state._decoder.last_event_sent = event_tuple
    send_event_info()


def _do_board_reset(gen):
    """Board wipe on a genuine re-start; runs on the worker (the decoder owner)."""
    if (state._finish_timer_gen != gen or state._decoder.race_finished()
            or state._running_lanes):
        print('[race-state] reset skipped (race active or transient un-finish)', flush=True)
        return
    print('[race-state] board reset (sustained re-start)', flush=True)
    data = state._decoder.reset_lanes()
    bus.emit('/scoreboard', 'update_scoreboard', data)
    relay.relay_emit('update_scoreboard', data)


def _handle_packet(l):
    # Liveness stamp for the `meet_live` watchdog in app.py. Set before decoding, so
    # a packet the decoder rejects still counts — the link is alive either way.
    state._last_packet_at = time.monotonic()

    hex_str = ''
    if state._record_handle or state._debug_serial:
        hex_str = ' '.join(['%02X' % int(c) for c in l])
        log_line = '[%f] ' % time.time() + hex_str + '\n'
        if state._record_handle:
            state._record_handle.write(log_line)

    try:
        # Run any queued commands (adjust_splits / next_heat / board-reset) here,
        # on the decoder-owning thread, before feeding the new packet — so nothing
        # else ever touches the decoder concurrently. No lock needed.
        _drain_cmds()

        updates = state._decoder.feed(list(l))

        # Track which lanes are currently running (consoles only send the flag on
        # transitions), so the debounced board-wipe never clears a live heat.
        for ln in range(1, 13):
            key = f'lane_running{ln}'
            if key in updates:
                (state._running_lanes.add if updates[key]
                 else state._running_lanes.discard)(ln)

        if updates.pop('dismiss_overlay', False):
            _auto_dismiss_overlay()

        if 'event_changed' in updates:
            ev, ht = updates.pop('event_changed')
            print(f'[event] Event {ev} Heat {ht}', flush=True)
            _on_event_changed(updates, ev, ht)

        _add_lane_deltas(updates)
        state.update.update(updates)

        _emit_scoreboard_update()
        _on_race_state_changed(state._decoder.race_finished(), updates)

        if state._debug_serial and hex_str:
            bus.emit('/settings', 'debug_line', {'hex': hex_str, 'text': _packet_summary(updates)})

    except Exception:
        traceback.print_exc()
    finally:
        time.sleep(0)  # cheap yield after every packet


# ── Session playback ───────────────────────────────────────────────────────────

def _ingest_byte(c, l):
    """Append byte to packet buffer, flushing at packet boundaries. Returns updated buffer."""
    if not c:
        return l
    if state._decoder.is_packet_start(c, l) or (len(l) >= state._decoder.max_packet_bytes):
        if l:
            _handle_packet(l)
        l = []
    l.append(c)
    return l


def _play_cap_file(session_file, my_gen):
    """Loop-play a binary .cap capture until the worker is superseded."""
    raw_bytes = open(session_file, 'rb').read()
    delay = 0.0
    while state._worker_gen == my_gen:
        l = []
        for byte in raw_bytes:
            if state._worker_gen != my_gen:
                break
            l = _ingest_byte(byte, l)
            delay += 1 / 720.0
            if delay > 0.1:
                delay = 0
                time.sleep(0.1)


def _play_cts_file(session_file, my_gen):
    """Play a timestamped or looping .cts/.raw session file."""
    text           = open(session_file, 'rt').read()
    has_timestamps = bool(re.search(r'\[[0-9.]+\]', text))
    start_time     = None
    delay          = 0.0

    while state._worker_gen == my_gen:
        l = []
        for d in re.finditer(r'\[([0-9.]+)\]\s*|([0-9a-fA-F]{2})', text):
            if state._worker_gen != my_gen:
                break
            if d.group(1):
                ts = float(d.group(1))
                if start_time is None:
                    start_time = ts - state.in_speed * time.time()
                else:
                    delay = ts - state.in_speed * time.time() - start_time
                    if delay > 0:
                        _drain_cmds()   # process queued commands between events
                        time.sleep(delay)
                continue
            l = _ingest_byte(int(d.group(2), 16), l)
            if delay > 0.1:
                delay = 0
                time.sleep(0.1)
            else:
                delay += 1 / 720.0
        if has_timestamps:
            break


def _run_test_session(session_file, my_gen):
    """Play a recorded session file, then emit cleanup events if it finishes naturally."""
    if session_file.endswith('.cap'):
        _play_cap_file(session_file, my_gen)
    else:
        _play_cts_file(session_file, my_gen)

    if state._worker_gen != my_gen:   # superseded — the new worker owns cleanup
        return
    state._test_session = None
    _cleanup_test_meet()
    bus.emit('/scoreboard', 'test_mode', {'active': False})
    bus.emit('/settings', 'test_status', {})


def _run_live_serial(my_gen):
    """Read from the configured serial port until superseded, retrying on error."""
    def _set_serial_status(st, msg=''):
        state._serial_status = {'state': st, 'msg': msg}
        bus.emit('/settings', 'serial_log', {'state': st, 'msg': msg})

    port = state.settings['serial_port']

    while state._worker_gen == my_gen:
        cfg = state._decoder.serial_config
        _PARITY = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD}
        _STOPBITS = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}
        parity_label = f'{cfg.bytesize}-{cfg.parity}-{cfg.stopbits}'
        _set_serial_status('opening', f'Opening {port} ({cfg.baud} {parity_label})…')
        try:
            # ── Open port ─────────────────────────────────────────────────────
            with serial.Serial(port, cfg.baud,
                               bytesize=cfg.bytesize,
                               parity=_PARITY.get(cfg.parity, serial.PARITY_EVEN),
                               stopbits=_STOPBITS.get(cfg.stopbits, serial.STOPBITS_ONE),
                               timeout=0) as f:

                # ── Initialization ────────────────────────────────────────────
                init = state._decoder.post_open_bytes
                if init:
                    f.write(init)

                # ── Read loop ──────────────────────────────────────────────────
                _set_serial_status('open', f'Connected: {port}')
                l = []
                last_byte_time = time.time()
                while state._worker_gen == my_gen:
                    c = f.read(1)
                    if c:
                        # Accumulate bytes; _ingest_byte flushes completed packets.
                        l = _ingest_byte(c[0], l)
                        last_byte_time = time.time()
                    else:
                        # No data — flush any partial packet after 50 ms of silence,
                        # then yield to the event loop before polling again.
                        if l and (time.time() - last_byte_time) >= 0.05:
                            _handle_packet(l)
                            l = []
                        _drain_cmds()   # process queued commands while idle
                        time.sleep(0.01)

        except (serial.SerialException, OSError) as e:
            # ── Error: wait 5 s then retry unless the worker was stopped ──────
            err = str(e)
            print(f'Serial port error: {err}')
            _set_serial_status('error', err)
            for _ in range(50):
                if state._worker_gen != my_gen:
                    break
                time.sleep(0.1)
            continue  # retry the outer while loop (re-open the port)

    # ── Worker stopped cleanly ─────────────────────────────────────────────────
    _set_serial_status('idle', '')


# ── Worker lifecycle ───────────────────────────────────────────────────────────

def main_thread_worker():
    state._running_lanes.clear()            # fresh worker — no lanes carried over
    while not state._worker_cmds.empty():   # drop stale commands from a prior session
        try:
            state._worker_cmds.get_nowait()
        except queue.Empty:
            break
    my_gen = state._worker_gen
    try:
        if state._test_session:
            _run_test_session(state._test_session, my_gen)
        else:
            _run_live_serial(my_gen)
    finally:
        if state._worker_gen == my_gen:
            state.main_thread = None


def _restart_worker(session_path=None):
    state._test_session = session_path
    state._worker_gen  += 1   # supersede the current worker (see state._worker_gen)
    time.sleep(0.3)
    state.main_thread = bus.run_bg(main_thread_worker)
