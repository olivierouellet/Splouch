import glob
import json
import os

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

import state
from meet_data import _build_meet_data, send_event_info
from web import client_strings, redirect, render, require_login

router = APIRouter(tags=['Meet'])


class MeetStatus(BaseModel):
    file_list: list[str]
    active: str
    preview_url: str
    playing: bool


@router.get('/meet')
def route_meet(request: Request):
    data = _build_meet_data()
    return render(request, 'meet.html',
                  strings=state.load_preview_strings(),
                  kiosk='kiosk' in request.query_params,
                  **data)


@router.get('/full_schedule')
def route_full_schedule(request: Request):
    data = _build_meet_data()
    return render(request, 'full_schedule.html',
                  t=state._mobile_strings(),
                  labels=state.load_locale(),
                  theme_colors={**state.DEFAULT_THEME_COLORS,
                                **state.settings.get('theme_colors', {})},
                  theme_fonts={**state.DEFAULT_THEME_FONTS,
                               **state.settings.get('theme_fonts', {})},
                  **data)


@router.get('/schedule')
def route_schedule(request: Request):
    data           = _build_meet_data()
    events_grouped = data.get('events_grouped', [])
    start_list     = data.get('start_list', {})
    event_names    = data.get('event_names', {})
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
                'time':       heat_times.get(ev, {}).get(ht, ''),
                'lanes':      lanes_out,
            })

    meet_name = (state.meet.meet_info.get('name') or
                 state.settings.get('meet_title') or '')

    return render(request, 'schedule.html',
                  heats_json=json.dumps(heats_out),
                  has_meet=bool(events_grouped),
                  meet_name=meet_name,
                  theme_colors={**state.DEFAULT_THEME_COLORS,
                                **state.settings.get('theme_colors', {})},
                  theme_fonts={**state.DEFAULT_THEME_FONTS,
                               **state.settings.get('theme_fonts', {})},
                  **client_strings(request))


@router.get('/search_suggestions')
def route_search_suggestions(request: Request):
    import unicodedata
    def fold(s):
        return unicodedata.normalize('NFD', s.lower()).encode('ascii', 'ignore').decode()

    q = fold(request.query_params.get('q', '').strip())
    if not q:
        return []

    swimmers = {}
    clubs    = set()
    for ev_heats in state.meet.start_list.values():
        for heat_lanes in ev_heats.values():
            for entry in heat_lanes.values():
                club = entry.get('club', '')
                if club:
                    clubs.add(club)
                name = entry.get('name', '')
                if name and not entry.get('swimmers'):
                    swimmers.setdefault(name, club)
                for s in entry.get('swimmers', []):
                    sname = s.get('name', '')
                    if sname:
                        swimmers.setdefault(sname, club)

    results = []
    for name in sorted(swimmers):
        if q in fold(name):
            results.append({'type': 'swimmer', 'name': name, 'club': swimmers[name]})
    for club in sorted(clubs):
        if q in fold(club):
            results.append({'type': 'club', 'name': club})

    return results[:20]


@router.get('/hytek_preview')
def route_hytek_preview():
    return redirect('/meet')


@router.get('/lenex_preview')
def route_lenex_preview(request: Request):
    return redirect('/meet' + ('?kiosk' if 'kiosk' in request.query_params else ''))


@router.get('/meet_status', response_model=MeetStatus,
            dependencies=[Depends(require_login)])
def route_meet_status():
    file_list = sorted(
        os.path.basename(f)
        for f in glob.glob(os.path.join(state.MEET_FOLDER, '*.csv')) +
                 glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf'))
    )
    return {
        'file_list':   file_list,
        'active':      state._active_meet_file,
        'preview_url': '/meet',
        'playing':     state._test_session is not None,
    }


@router.get('/meet_delete', dependencies=[Depends(require_login)])
def route_meet_delete(request: Request):
    filename = os.path.basename(request.query_params.get('file', '').strip())
    if filename:
        filepath = os.path.join(state.MEET_FOLDER, filename)
        if os.path.isfile(filepath):
            os.remove(filepath)
            if state._active_meet_file == filename:
                state.clear_meet()
                state._active_meet_file = ''
                state._active_meet_uid  = ''
                state.settings['last_meet_file'] = ''
                state.save_settings()
                send_event_info()
                import relay as _relay
                _relay.update_metadata()
    return redirect('/settings#tab-meet')


@router.get('/meet_clear', dependencies=[Depends(require_login)])
def route_meet_clear():
    for f in glob.glob(os.path.join(state.MEET_FOLDER, '*.csv')) + \
             glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf')):
        os.remove(f)
    state.clear_meet()
    state._active_meet_file = ''
    state._active_meet_uid  = ''
    state.settings['last_meet_file'] = ''
    state.save_settings()
    send_event_info()
    import relay as _relay
    _relay.update_metadata()
    return redirect('/settings#tab-meet')
