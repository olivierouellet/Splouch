import re

import bus
import relay
import state
from console_decoders.utils import parse_time_hundredths


def _delta_hundredths(finish_str, seed_str):
    """Signed (finish − seed) in hundredths of a second, or None if unparseable."""
    finish = parse_time_hundredths(finish_str)
    seed   = parse_time_hundredths(seed_str)
    if finish is None or seed is None:
        return None
    return finish - seed


def _delta_html(delta):
    abs_d  = abs(delta)
    h      = abs_d % 100
    s      = (abs_d // 100) % 60
    m_part = abs_d // 6000
    sign   = '-' if delta < 0 else '+'
    text   = (f'{sign}{m_part}:{s:02d}.{h:02d}') if m_part else (f'{sign}{s}.{h:02d}')
    cls    = 'delta-better' if delta < 0 else 'delta-worse'
    return f'<span class="{cls}">{text}</span>'


def delta_fields(finish_str, seed_str):
    """Return (html, seconds, better) for a finish vs its seed time.

    `seconds` is the signed difference as a float (negative = faster than seed) and
    `better` is a bool — structured values for native clients that can't parse the
    HTML `html` the browser scoreboard renders. Returns ('', None, None) if either
    time is missing/unparseable.
    """
    delta = _delta_hundredths(finish_str, seed_str)
    if delta is None:
        return '', None, None
    return _delta_html(delta), round(delta / 100.0, 2), delta < 0


def get_lane_seed_time(event_num, heat_num, lane):
    m = state.meet
    try:
        return m.start_list[event_num][heat_num][lane].get('seed_time', '')
    except (KeyError, TypeError):
        return m.event_info.get_seed_time(event_num, heat_num, lane)


def _raw_event_name(event_num):
    m = state.meet
    return m.event_names.get(event_num) or m.event_info.get_event_name(event_num)


def get_event_name_display(event_num):
    return state.translate_event_name(_raw_event_name(event_num),
                                      state.load_event_translations())


def get_event_name_parts(event_num):
    """The same name, language-neutral, for a client rendering in its own language.

    Travels beside `event_name` rather than replacing it (api.md §5.1): a client
    that does not compose keeps rendering the string, which is already correct for
    anyone who has not chosen a language (docs/app.md `T-04`).
    """
    return state.parse_event_name(_raw_event_name(event_num))


def get_lane_parts(event_num, heat_num, lane):
    """Return (name, club) tuple for display."""
    m = state.meet
    try:
        entry = m.start_list[event_num][heat_num][lane]
        return entry['name'], (entry['club'] or '')
    except (KeyError, TypeError):
        s = m.event_info.get_display_string(event_num, heat_num, lane)
        if len(s) > 5 and s[4] == ' ':
            return s[5:], s[:4].strip()
        return '', s.strip()


def get_lane_alt(event_num, heat_num, lane):
    """Return alternate display string for relay lanes (first names), else ''."""
    try:
        entry    = state.meet.start_list[event_num][heat_num][lane]
        swimmers = entry.get('swimmers', [])
        if not swimmers:
            return ''
        return ' · '.join(sw.get('first', '') or sw['name'].split()[-1] for sw in swimmers)
    except (KeyError, TypeError):
        return ''


def _get_next_heats(after_event=0, after_heat=0, n=3, num_lanes=8):
    m = state.meet
    if not m.start_list:
        return []
    ordered = [(ev, ht)
               for ev in sorted(m.start_list)
               for ht in sorted(m.start_list[ev])]
    start = 0
    if after_event:
        for i, (ev, ht) in enumerate(ordered):
            if ev == after_event and ht == after_heat:
                start = i + 1
                break
    result = []
    for ev, ht in ordered[start:start + n]:
        lanes_data = m.start_list[ev][ht]
        swimmers = []
        for ln in range(1, num_lanes + 1):
            if ln in lanes_data:
                swimmers.append({'lane': ln,
                                 'name': lanes_data[ln].get('name', ''),
                                 'club': lanes_data[ln].get('club', ''),
                                 'alt':  get_lane_alt(ev, ht, ln)})
            else:
                swimmers.append({'lane': ln, 'name': '', 'club': '', 'alt': ''})
        result.append({
            'event':      ev,
            'heat':       ht,
            'event_name': get_event_name_display(ev),
            'event_name_parts': get_event_name_parts(ev),
            'time':       m.heat_times.get(ev, {}).get(ht, ''),
            'swimmers':   swimmers,
        })
    return result


def _build_results_snapshot():
    ev, ht = state._decoder.last_event_sent if state._decoder.last_event_sent != (0, 0) else (0, 0)
    lanes  = []
    for ch in range(1, 11):
        time_str = state._decoder.get_lane_time(ch)
        if not time_str:
            continue
        place_str = state._decoder.get_lane_place(ch)
        place_int = int(place_str) if place_str.strip().isdigit() else 99
        name, club = get_lane_parts(ev, ht, ch) if ev else ('', '')
        alt   = get_lane_alt(ev, ht, ch) if ev else ''
        delta, delta_seconds, delta_better = '', None, None
        if time_str and ch in state._decoder.lane_seed_times:
            delta, delta_seconds, delta_better = delta_fields(
                time_str, state._decoder.lane_seed_times[ch])
        lanes.append({
            'channel':      ch,
            'place':        place_str,
            'place_int':    place_int,
            'time':         time_str,
            'name':         name,
            'club':         club,
            'alt':          alt,
            'delta':        delta,
            'delta_seconds': delta_seconds,
            'delta_better':  delta_better,
        })
    sort = state.settings.get('results_sort', 'lane')
    if sort == 'place':
        lanes.sort(key=lambda r: r['place_int'])
    else:
        lanes.sort(key=lambda r: r['channel'])
    return {
        'event':      str(ev) if ev else '',
        'heat':       str(ht) if ht else '',
        'event_name': get_event_name_display(ev) if ev else '',
        'event_name_parts': get_event_name_parts(ev) if ev else None,
        # Lanes without a final time are omitted above; 'sort' lets the client
        # place each result in the row matching its lane (lane mode) so a missing
        # lane leaves a blank row instead of shifting the lanes below it up.
        'sort':       sort,
        'lanes':      lanes,
    }


def _build_meet_data():
    """Normalize Lenex or Hytek data into a unified structure for meet/schedule views."""
    m = state.meet
    if m.start_list:
        ev_trans    = state.load_event_translations()
        event_names = {num: state.translate_event_name(name, ev_trans)
                       for num, name in m.event_names.items()}
        event_name_parts = {num: state.parse_event_name(name)
                            for num, name in m.event_names.items()}
        events_grouped = [(ev, sorted(m.start_list[ev]))
                          for ev in sorted(m.start_list)]
        return dict(events_grouped=events_grouped, event_names=event_names,
                    event_name_parts=event_name_parts,
                    start_list=m.start_list,
                    heat_times=m.heat_times,
                    meet_info=m.meet_info)
    else:
        info = m.event_info
        by_ev = {}
        for (ev, ht) in sorted(info.events.keys()):
            by_ev.setdefault(ev, []).append(ht)
        events_grouped = list(sorted(by_ev.items()))
        start_list = {}
        for (ev, ht), lane_data in info.events.items():
            sl_ht = start_list.setdefault(ev, {}).setdefault(ht, {})
            for lane, display in lane_data.items():
                if len(display) > 5 and display[4] == ' ':
                    name, club = display[5:], display[:4].strip()
                else:
                    name, club = '', display.strip()
                seed = info.seed_times.get((ev, ht), {}).get(lane, '')
                sl_ht[lane] = {'name': name, 'club': club, 'seed_time': seed, 'swimmers': []}
        return dict(events_grouped=events_grouped,
                    event_names=dict(info.event_names),
                    start_list=start_list, heat_times={}, meet_info={})


def send_event_info():
    ev, ht = state._decoder.last_event_sent
    # (0, 0) is the decoder's "nothing yet" sentinel, not event 0 of heat 0. Send it
    # as blank: a client writes these straight into the header, so str(0) painted a
    # literal "0" under EVENT and HEAT before the console had reported anything.
    started = (ev, ht) != (0, 0)
    u = {
        'current_event': str(ev) if started else '',
        'current_heat':  str(ht) if started else '',
        'event_name':    get_event_name_display(ev) if started else '',
        'event_name_parts': get_event_name_parts(ev) if started else None,
    }
    for i in range(1, 11):
        name, club = get_lane_parts(ev, ht, i)
        u[f'lane_name{i}']          = name
        u[f'lane_club{i}']          = club
        u[f'lane_delta{i}']         = ''
        u[f'lane_delta_seconds{i}'] = None
        u[f'lane_delta_better{i}']  = None
        u[f'lane_name_alt{i}']      = get_lane_alt(ev, ht, i)
    bus.emit('/scoreboard', 'update_scoreboard', u)
    relay.relay_emit('update_scoreboard', u)
