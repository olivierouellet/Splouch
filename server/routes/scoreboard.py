import os

from fastapi import APIRouter, Request

import state
from console_decoders import console_info_for
from console_decoders.utils import split_step
from meet_data import build_heats
from web import client_strings, display_config, redirect, remember_prefs, render

router = APIRouter(tags=['Scoreboard'])


@router.get('/config')
def route_config():
    """Display config as JSON — for native clients (Qt TV) that render natively."""
    return display_config()


@router.get('/')
def route_index():
    return redirect('/live')


@router.get('/live')
def route_live(request: Request):
    lanes = int(state.settings.get('num_lanes', 8))
    carousel_images = sorted(
        f for f in os.listdir(state.IMAGES_DIR)
        if os.path.isfile(os.path.join(state.IMAGES_DIR, f))
    )
    # No `meet_title`: the header's title cell is gone — it only ever showed on a
    # cold board, and the title lives on the splash on both displays.
    return render(request, 'live.html',
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
    return remember_prefs(request, render(request, 'mobile.html', app_title=app_title,
                                          # No Results tab under a console that times
                                          # nothing — see the template (app.md `A-11`).
                                          show_results=state.console_state()['timed'],
                                          **client_strings(request)))


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
    # The ± buttons move the count by whatever one observation is worth in this pool,
    # so they agree with what the console (or the Gen6's inference) would have added
    # on its own. Same helper the boards' `split_step` comes from — docs/api.md §5.1.
    return render(request, 'operator.html',
                  num_lanes=int(state.settings.get('num_lanes', 8)),
                  split_step=split_step(state.settings.get('touchpad_sides', 1)))


@router.get('/manual')
def route_manual(request: Request):
    """The manual console — drive event and heat by hand when the meet has none.

    The whole start list is server-rendered, exactly what `/schedule` embeds, so
    previewing a heat costs no round trip and the page keeps working on a phone that
    drops off the Wi-Fi between heats. The socket then carries only what is genuinely
    live: which heat is on now, and the operator's three commands.

    Not login-gated, like `/operator`. That would be theatre while `/ws/scoreboard`
    accepts `next_heat` from any client on the LAN; locking it down means authing the
    whole channel, which is a larger change than this page.
    """
    heats   = build_heats()
    ev, ht  = state._decoder.last_event_sent
    started = (ev, ht) != (0, 0)
    console = console_info_for(state.settings.get('console_type', 'cts_gen6')) or {}
    return render(request, 'manual.html',
                  heats=heats,
                  has_meet=bool(heats),
                  current_event=str(ev) if started else '',
                  current_heat=str(ht) if started else '',
                  # False when a real console is configured: the page still works, but
                  # the console will re-announce over it within a packet or two, so it
                  # says so rather than letting the operator wonder.
                  manual_active=not state._decoder.requires_serial,
                  console_label=console.get('label', ''),
                  meet_name=(state.meet.meet_info.get('name') or
                             state.settings.get('meet_title') or ''),
                  theme_colors={**state.DEFAULT_THEME_COLORS,
                                **state.settings.get('theme_colors', {})},
                  theme_fonts={**state.DEFAULT_THEME_FONTS,
                               **state.settings.get('theme_fonts', {})},
                  t=state.manual_strings())


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
