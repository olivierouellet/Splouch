import os

from fastapi import APIRouter, Request

import state
from web import client_strings, display_config, redirect, render

router = APIRouter(tags=['Scoreboard'])


@router.get('/config')
def route_config():
    """Display config as JSON — for native clients (Qt TV) that render natively."""
    return display_config()


@router.get('/')
def route_index():
    return redirect('/live')


@router.get('/scoreboard')
def route_scoreboard_default(request: Request):
    lanes = int(state.settings.get('num_lanes', 8))
    return render(request, 'scoreboard.html',
                  meet_title=state.settings['meet_title'],
                  num_lanes=lanes,
                  nosplash='nosplash' in request.query_params,
                  test_background='test' in request.query_params)


@router.get('/live')
def route_live(request: Request):
    lanes = int(state.settings.get('num_lanes', 8))
    carousel_images = sorted(
        f for f in os.listdir(state.IMAGES_DIR)
        if os.path.isfile(os.path.join(state.IMAGES_DIR, f))
    )
    return render(request, 'live.html',
                  meet_title=state.settings['meet_title'],
                  num_lanes=lanes,
                  nosplash='nosplash' in request.query_params,
                  test_background='test' in request.query_params,
                  carousel_images=carousel_images,
                  carousel_interval=int(state.settings.get('carousel_interval', 10)),
                  theme_fonts={**state.DEFAULT_THEME_FONTS,
                               **state.settings.get('theme_fonts', {})})


@router.get('/live-mobile')
def route_live_mobile(request: Request):
    """The phone board, from shared/templates — the same page the cloud serves.

    No carousel images and no meet title: this view carries neither, so nothing
    here reads `state.IMAGES_DIR`. The kiosk (`/live`) still does."""
    return render(request, 'live-mobile.html',
                  num_lanes=int(state.settings.get('num_lanes', 8)),
                  theme_fonts={**state.DEFAULT_THEME_FONTS,
                               **state.settings.get('theme_fonts', {})},
                  **client_strings(request))


@router.get('/mobile')
def route_mobile(request: Request):
    """The phone shell, from shared/templates — the same page the cloud serves.

    No `meet_id`: this server has one meet, so the template drops the back-to-meets
    link and points the tabs at the local routes instead of the per-meet ones."""
    app_title = (state.settings.get('app_window_title') or
                 state.settings.get('meet_title') or 'Splouch')
    return render(request, 'mobile.html', app_title=app_title,
                  **client_strings(request))


@router.get('/results')
def route_results(request: Request):
    return render(request, 'results.html',
                  num_lanes=int(state.settings.get('num_lanes', 8)),
                  theme_colors={**state.DEFAULT_THEME_COLORS,
                                **state.settings.get('theme_colors', {})},
                  theme_fonts={**state.DEFAULT_THEME_FONTS,
                               **state.settings.get('theme_fonts', {})},
                  **client_strings(request))


@router.get('/operator')
def route_operator(request: Request):
    sides      = int(state.settings.get('touchpad_sides', 1))
    split_step = 2 if sides == 1 else 1
    return render(request, 'operator.html',
                  num_lanes=int(state.settings.get('num_lanes', 8)),
                  split_step=split_step)


@router.get('/console')
def route_console(request: Request):
    return render(request, 'console.html',
                  num_lanes=max(int(state.settings.get('num_lanes', 8)), 12))


@router.get('/next_heats')
def route_next_heats(request: Request):
    return render(request, 'next_heats.html',
                  theme_colors={**state.DEFAULT_THEME_COLORS,
                                **state.settings.get('theme_colors', {})},
                  theme_fonts={**state.DEFAULT_THEME_FONTS,
                               **state.settings.get('theme_fonts', {})},
                  num_lanes=int(state.settings.get('num_lanes', 8)),
                  t=state._mobile_strings())
