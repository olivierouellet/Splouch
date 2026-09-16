import glob
import json
import os

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

import state
from meet_data import _build_meet_data, build_heats, send_event_info
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


@router.get('/schedule.json')
def route_schedule_json():
    """The start list as JSON — what `/schedule` embeds, for native clients.

    The Pi's twin of the cloud's `GET /meet/{id}/schedule` (docs/api.md §4): one
    meet, so no id in the path. An empty `heats` means no meet file is loaded,
    which is not an error — the client shows its no-schedule state and waits for
    `schedule_update` on `/ws/schedule`."""
    return {'heats': build_heats()}


@router.get('/schedule')
def route_schedule(request: Request):
    heats_out = build_heats()
    meet_name = (state.meet.meet_info.get('name') or
                 state.settings.get('meet_title') or '')

    return render(request, 'schedule.html',
                  heats_json=json.dumps(heats_out),
                  has_meet=bool(heats_out),
                  meet_name=meet_name,
                  theme_colors={**state.DEFAULT_THEME_COLORS,
                                **state.settings.get('theme_colors', {})},
                  theme_fonts={**state.DEFAULT_THEME_FONTS,
                               **state.settings.get('theme_fonts', {})},
                  **client_strings(request))


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
