import glob
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

import bus
import relay
import state
from web import redirect, require_login

router = APIRouter(tags=['Appearance'])


@router.get('/images/{filename:path}')
def serve_image(filename: str):
    path = os.path.normpath(os.path.join(state.IMAGES_DIR, filename))
    if not path.startswith(os.path.normpath(state.IMAGES_DIR)) or not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path)


@router.get('/splash_delete', dependencies=[Depends(require_login)])
def route_splash_delete():
    filename = state.settings.get('splash_url', '')
    if filename:
        path = os.path.join(state.IMAGES_DIR, filename)
        if os.path.isfile(path):
            os.remove(path)
        state.settings['splash_url'] = ''
        state.save_settings()
    return redirect('/settings#tab-display')


@router.get('/splash_delete_all', dependencies=[Depends(require_login)])
def route_splash_delete_all():
    for f in os.listdir(state.IMAGES_DIR):
        fp = os.path.join(state.IMAGES_DIR, f)
        if os.path.isfile(fp):
            os.remove(fp)
    state.settings['splash_url'] = ''
    state.save_settings()
    return redirect('/settings#tab-display')


@router.get('/icon_delete', dependencies=[Depends(require_login)])
def route_icon_delete(request: Request):
    filename = os.path.basename(request.query_params.get('file', '').strip())
    if filename and filename not in ('home_icon.png', 'home_icon_512.png'):
        path = os.path.join(state.ICONS_DIR, filename)
        if os.path.isfile(path):
            os.remove(path)
        if state.settings.get('active_home_icon') == filename:
            for p in (state.HOME_ICON_PATH, state.HOME_ICON_512_PATH):
                if os.path.exists(p):
                    os.remove(p)
            state.settings['active_home_icon'] = ''
            state.save_meet_profile(state._active_meet_uid)
            state.save_settings()
            relay.update_metadata()
    return redirect('/settings#tab-meet')


@router.get('/icon_delete_all', dependencies=[Depends(require_login)])
def route_icon_delete_all():
    if os.path.isdir(state.ICONS_DIR):
        for f in os.listdir(state.ICONS_DIR):
            fp = os.path.join(state.ICONS_DIR, f)
            if os.path.isfile(fp):
                os.remove(fp)
    state.settings['active_home_icon'] = ''
    state.save_meet_profile(state._active_meet_uid)
    state.save_settings()
    relay.update_metadata()
    return redirect('/settings#tab-meet')


@router.get('/theme_delete', dependencies=[Depends(require_login)])
def route_theme_delete():
    code = state.settings.get('active_theme', '')
    path = os.path.join(state.CUSTOM_THEME_FOLDER, code + '.toml')
    if os.path.exists(path):
        os.remove(path)
    state.settings['active_theme'] = 'default'
    colors, fonts = state.load_theme('default')
    state.settings['theme_colors'] = colors
    state.settings['theme_fonts']  = fonts
    state.save_settings()
    bus.emit('/scoreboard', 'reload')
    bus.emit('/results', 'reload')
    relay.relay_emit('reload', {})
    return redirect('/settings')


@router.get('/theme_delete_all', dependencies=[Depends(require_login)])
def route_theme_delete_all():
    for f in glob.glob(os.path.join(state.CUSTOM_THEME_FOLDER, '*.toml')):
        os.remove(f)
    state.settings['active_theme'] = 'default'
    colors, fonts = state.load_theme('default')
    state.settings['theme_colors'] = colors
    state.settings['theme_fonts']  = fonts
    state.save_settings()
    bus.emit('/scoreboard', 'reload')
    bus.emit('/results', 'reload')
    relay.relay_emit('reload', {})
    return redirect('/settings')


@router.get('/picker_image_delete', dependencies=[Depends(require_login)])
def route_picker_image_delete(request: Request):
    filename = os.path.basename(request.query_params.get('file', '').strip())
    if filename:
        path = os.path.join(state.PICKER_DIR, filename)
        if os.path.isfile(path):
            os.remove(path)
        if state.settings.get('active_picker_image') == filename:
            state.settings['active_picker_image'] = ''
            state.save_meet_profile(state._active_meet_uid)
            state.save_settings()
            relay.update_metadata()
    return redirect('/settings#tab-meet')


@router.get('/picker_image_delete_all', dependencies=[Depends(require_login)])
def route_picker_image_delete_all():
    if os.path.isdir(state.PICKER_DIR):
        for f in os.listdir(state.PICKER_DIR):
            fp = os.path.join(state.PICKER_DIR, f)
            if os.path.isfile(fp):
                os.remove(fp)
    state.settings['active_picker_image'] = ''
    state.save_meet_profile(state._active_meet_uid)
    state.save_settings()
    relay.update_metadata()
    return redirect('/settings#tab-meet')
