
from typing import Any

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


def heat_order() -> list[tuple[int, int]]:
    """Every (event, heat) in the loaded meet, in running order.

    The one place that knows the difference between a Lenex meet (`start_list`) and a
    Hytek one (`event_info.events`). Three callers wanted this list and two of them
    grew their own half of it: `_get_next_heats` read `start_list` and returned []
    for a CSV meet, while `worker._worker_next_heat` read `event_info.events` and was
    blind to a Lenex one — so on any `.lxf` meet, the only meet most clubs have, the
    Next Heat button silently jumped to the (0, 0) sentinel and blanked the board.
    """
    m = state.meet
    if m.start_list:
        return [(ev, ht)
                for ev in sorted(m.start_list)
                for ht in sorted(m.start_list[ev])]
    return sorted(m.event_info.events.keys())


def heat_step(event_num, heat_num, delta=1):
    """The heat `delta` positions along from (event_num, heat_num), or None.

    None means *do not move*: either no meet is loaded, or the operator is already at
    the first or last heat. A current heat the loaded meet does not contain — the
    (0, 0) sentinel on a cold start, or a heat left over from the previous meet file
    — lands on the first heat going forward and the last going back, so Next always
    does something useful rather than nothing.
    """
    order = heat_order()
    if not order:
        return None
    try:
        i = order.index((event_num, heat_num))
    except ValueError:
        return order[0] if delta > 0 else order[-1]
    j = i + delta
    return order[j] if 0 <= j < len(order) else None


def has_heat(event_num, heat_num):
    """Is this heat in the loaded meet? The validation gate for a hand-picked heat."""
    return (event_num, heat_num) in heat_order()


def _get_next_heats(after_event=0, after_heat=0, n=3, num_lanes=8):
    m = state.meet
    ordered = heat_order()
    start = 0
    if after_event:
        for i, (ev, ht) in enumerate(ordered):
            if ev == after_event and ht == after_heat:
                start = i + 1
                break
    result = []
    for ev, ht in ordered[start:start + n]:
        swimmers = []
        for ln in range(1, num_lanes + 1):
            # Via the accessors rather than `start_list[ev][ht]` directly: they fall
            # back to `event_info`, which is what makes this list appear at all on a
            # Hytek CSV meet. It never used to — the function returned [] for one.
            name, club = get_lane_parts(ev, ht, ln)
            swimmers.append({'lane': ln, 'name': name, 'club': club,
                             'alt': get_lane_alt(ev, ht, ln)})
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
    lanes: list[dict[str, Any]] = []
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


def build_heats():
    """The start list as the phone Schedule tab consumes it — every heat in running
    order, each with its lanes. One builder for the HTML page and the JSON endpoint,
    so the two cannot drift (docs/app.md §0.2); the shape is the cloud's
    `GET /meet/{id}/schedule` (docs/api.md §5.8).

    Lives here beside `_build_meet_data`, which it is a thin shaping layer over,
    rather than in `routes/meet.py` where it started: `/manual` needs the same list,
    and a route module importing another route module to get at meet logic is the
    wrong direction.
    """
    data           = _build_meet_data()
    events_grouped = data.get('events_grouped', [])
    start_list     = data.get('start_list', {})
    event_names    = data.get('event_names', {})
    name_parts     = data.get('event_name_parts', {})
    heat_times     = data.get('heat_times', {})

    heats_out = []
    for ev, heats in events_grouped:
        for ht in heats:
            lanes_out = []
            for lane in sorted(start_list.get(ev, {}).get(ht, {})):
                entry = start_list[ev][ht][lane]
                lanes_out.append({
                    'lane':      lane,
                    'name':      entry.get('name', ''),
                    'club':      entry.get('club', ''),
                    'seed_time': entry.get('seed_time', ''),
                    'swimmers':  [{'pos': s.get('pos', 0),
                                   'name': s.get('name', ''),
                                   'first': s.get('first', '')}
                                  for s in entry.get('swimmers', [])],
                })
            heats_out.append({
                'event':      ev,
                'heat':       ht,
                'event_name': event_names.get(ev, ''),
                'event_name_parts': name_parts.get(ev),
                'time':       heat_times.get(ev, {}).get(ht, ''),
                'lanes':      lanes_out,
            })
    return heats_out


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
    # 12, not 10: Settings offers a 12-lane pool and every other producer of these
    # keys covers 1-12 (`worker._load_heat_names`, each decoder's `reset_lanes`).
    # Stopping at 10 left lanes 11 and 12 un-named on a reconnect or a `next_heat`,
    # still showing the previous heat's swimmers while the rest of the board moved on.
    for i in range(1, 13):
        name, club = get_lane_parts(ev, ht, i)
        u[f'lane_name{i}']          = name
        u[f'lane_club{i}']          = club
        u[f'lane_delta{i}']         = ''
        u[f'lane_delta_seconds{i}'] = None
        u[f'lane_delta_better{i}']  = None
        u[f'lane_name_alt{i}']      = get_lane_alt(ev, ht, i)
    state.record_board(u)
    bus.emit('/scoreboard', 'update_scoreboard', u)
    relay.relay_emit('update_scoreboard', u)
