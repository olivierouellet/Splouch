import glob
import json
import os
import re
import traceback

import serial.tools.list_ports
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool

import bus
import relay
import state
from console_decoders import CONSOLE_OPTIONS, console_info_for, load_custom_decoders, make_decoder
from meet_data import send_event_info
from meet_parsers.lenex_parser import load_lenex
from web import render, require_login, save_upload
from worker import _restart_worker

router = APIRouter(tags=['Settings'])


def _render_home_icon(name):
    """Render source icon *name* (in ICONS_DIR) to the active home-icon files.

    Removes the active icon when *name* is empty or its source is missing.
    """
    src = os.path.join(state.ICONS_DIR, name) if name else ''
    if name and os.path.isfile(src):
        try:
            from PIL import Image
            img = Image.open(src).convert('RGBA')
            img.resize((192, 192), Image.Resampling.LANCZOS).save(state.HOME_ICON_PATH, 'PNG', optimize=True)
            img.resize((512, 512), Image.Resampling.LANCZOS).save(state.HOME_ICON_512_PATH, 'PNG', optimize=True)
            return
        except Exception:
            pass
    for p in (state.HOME_ICON_PATH, state.HOME_ICON_512_PATH):
        if os.path.exists(p):
            os.remove(p)


def _load_meet_file(path):
    """Clear current meet state and load *path* (Lenex or Hytek). Returns error string or None."""
    state.clear_meet()
    state._last_results_snapshot      = {}
    state._results_prev_race_finished = False
    state._finish_timer_gen          += 1
    try:
        if path.endswith('.csv'):
            state.load_event_info(path)
        else:
            state.set_lenex(load_lenex(path))
    except Exception:
        state.clear_meet()
        return traceback.format_exc()
    state._active_meet_file = os.path.basename(path)
    state.settings['last_meet_file'] = state._active_meet_file
    # Switch to this meet's cloud-appearance profile (title, image, icon, …) so
    # the Meet tab and the cloud publish reflect the loaded meet, not the last one.
    state._active_meet_uid = state.meet_uid()
    _render_home_icon(state.apply_meet_profile(state._active_meet_uid))
    state.save_settings()
    send_event_info()
    # Local Schedule tabs are showing the previous meet's heats until told.
    # The relay call below does the same for the cloud's.
    bus.emit('/schedule', 'schedule_update')
    relay.update_metadata()   # re-register under the new meet_uid before the schedule
    relay.send_schedule()
    return None


def _candidate_meet_uid(path, name):
    """meet_uid() the file at *path* would produce, without disturbing state.

    *name* is the original upload filename, used for the no-name fallback (Hytek
    CSV, or a Lenex with no meet name/dates) so the comparison matches what
    meet_uid() computed when the meet was first loaded.
    """
    base = os.path.basename(name)
    if path.endswith('.csv'):
        return state.uid_from_meet_info({}, base)
    data = load_lenex(path)
    return state.uid_from_meet_info(data.meet_info, base)


@router.post('/meet_update_file', dependencies=[Depends(require_login)])
async def route_meet_update_file(request: Request):
    """Replace the loaded meet's file in place, keeping its cloud link.

    Only a file that is the *same* meet (matching meet_uid — same name and
    session dates) is accepted; anything else is rejected so connected attendees
    are never orphaned onto a new picker card. The new file overwrites the
    current meet file in place, then is reloaded and republished.
    """
    file = (await request.form()).get('meet_file')
    # Saving + LENEX parsing + reload is blocking — run it off the event loop.
    return await run_in_threadpool(_meet_update_file, file)


def _meet_update_file(file):
    if state._test_session is not None:
        return {'ok': False, 'error': 'A test session is running.'}
    if not state._active_meet_file:
        return {'ok': False, 'error': 'No meet is loaded to update.'}
    if not file or not file.filename:
        return {'ok': False, 'error': 'No file selected.'}
    ext = os.path.splitext(file.filename)[1].lower()
    active_ext = os.path.splitext(state._active_meet_file)[1].lower()
    if ext != active_ext:
        return {'ok': False, 'error': f'The new file must be a {active_ext} file, like the current meet.'}

    tmp = os.path.join(state.MEET_FOLDER, '.update_candidate' + ext)
    save_upload(file, tmp)
    try:
        new_uid = _candidate_meet_uid(tmp, file.filename)
    except Exception:
        os.remove(tmp)
        return {'ok': False, 'error': 'Could not read that file.'}

    if new_uid != state._active_meet_uid:
        os.remove(tmp)
        return {'ok': False, 'error': (
            'This file is a different meet — its name or session dates differ, '
            'so it would create a separate cloud meet. Use “Add Meet File” to '
            'load it as a new meet.')}

    # Same meet: overwrite the current file in place and reload it.
    dest = os.path.join(state.MEET_FOLDER, state._active_meet_file)
    os.replace(tmp, dest)
    err = _load_meet_file(dest)
    if err:
        return {'ok': False, 'error': 'Failed to load the updated file.'}
    return {'ok': True}


@router.get('/settings', dependencies=[Depends(require_login)])
@router.post('/settings', dependencies=[Depends(require_login)])
async def route_settings(request: Request):
    form = await request.form() if request.method == 'POST' else None
    # POST parses uploads (PIL resize), enumerates serial ports and writes files;
    # the GET view also enumerates ports — all blocking, so run off the loop.
    return await run_in_threadpool(_settings_view, request, form)


def _coerce_like(current, raw):
    """Parse *raw* form text into the type *current* already has.

    Returns ``None`` when it cannot, so the caller leaves the setting alone rather
    than replacing a number with a string.
    """
    if isinstance(current, bool):
        return raw not in ('', '0', 'false', 'off', 'False')
    if isinstance(current, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    if isinstance(current, float):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    if isinstance(current, str):
        return raw
    return None                     # dicts/lists never come from a plain field


def _settings_view(request, form):
    if request.method == 'POST':
        modified = False
        icon_error = None
        console_changed = False
        # Snapshot for the generic sweep near the end of this function: it must
        # not touch anything a typed handler above has already dealt with.
        before = dict(state.settings)

        if 'meet_file' in form and state._test_session is None:
            file = form['meet_file']
            if file and file.filename:
                ext = os.path.splitext(file.filename)[1].lower()
                if ext in ('.csv', '.lxf'):
                    # Keep other meet files (e.g. each day of a split meet); only
                    # the uploaded name is overwritten. Manage the rest via Delete.
                    dest = os.path.join(state.MEET_FOLDER, os.path.basename(file.filename))
                    save_upload(file, dest)
                    err = _load_meet_file(dest)
                    if err:
                        return PlainTextResponse(err)
                    modified = True

        if 'meet_file_load_submit' in form and state._test_session is None:
            selected = form.get('meet_file_select', '').strip()
            if selected:
                dest = os.path.join(state.MEET_FOLDER, os.path.basename(selected))
                if os.path.isfile(dest):
                    err = _load_meet_file(dest)
                    if err:
                        return PlainTextResponse(err)
                    modified = True
            elif state._active_meet_file:
                state.clear_meet()
                state._active_meet_file = ''
                state.settings['last_meet_file'] = ''
                send_event_info()
                modified = True

        if 'pool_setup_submit' in form:
            for key, default in (('num_lanes', 8), ('pool_length', 25), ('touchpad_sides', 1)):
                try:
                    val = int(form.get(key, default))
                    if val != int(state.settings.get(key, default)):
                        state.settings[key] = val
                        modified = True
                except (ValueError, TypeError):
                    pass

        if 'timing_settings_submit' in form:
            changed = False
            for key in ('serial_port', 'console_type'):
                val = form.get(key, '').strip()
                if val and val != state.settings.get(key):
                    state.settings[key] = val
                    changed = True
                    modified = True
                    console_changed = console_changed or key == 'console_type'
            if changed:
                prev = state._decoder
                state._decoder_console_type = state.settings.get('console_type', 'cts_gen6')
                state._decoder = make_decoder(state._decoder_console_type, state.settings)
                # Keep the heat the boards are already showing. `make_decoder` builds
                # a blank decoder, so without this, changing the serial port — or
                # switching to or from the manual console mid-meet — silently drops
                # the event and heat off every board until something re-announces it.
                # A console eventually does; the manual one never will, since there is
                # no console there to re-announce.
                state._decoder.last_event_sent = getattr(prev, 'last_event_sent', (0, 0))
                state._decoder.set_seed_times(getattr(prev, 'lane_seed_times', {}))
                _restart_worker()

        if 'timing_tuning_submit' in form:
            # Console behaviour, not display behaviour: `finish_debounce` gates when a
            # results snapshot is published (worker.py), `split_min_duration` how long a
            # lane must stay stopped to count a length (the decoder). They lived in an
            # Appearance > Flow pane beside three timeouts that only `scoreboard.html`
            # read; that template is gone and these moved to Timing, where they belong.
            decoder_changed = False
            try:
                val = round(max(0.5, float(form.get('finish_debounce', 3.0))), 1)
                if val != state.settings.get('finish_debounce', 3.0):
                    state.settings['finish_debounce'] = val
                    modified = True
            except (ValueError, TypeError):
                pass
            try:
                val = round(max(0.5, float(form.get('split_min_duration', 1.0))), 1)
                if val != state.settings.get('split_min_duration', 1.0):
                    state.settings['split_min_duration'] = val
                    modified = decoder_changed = True
            except (ValueError, TypeError):
                pass
            if decoder_changed:
                # The decoder reads this in configure(), which otherwise runs only at
                # startup — so until now a changed value sat in settings.json and did
                # nothing until the server was restarted.
                state._decoder.configure(state.settings)

        if 'display_settings_submit' in form:
            for key in ('show_lane_header', 'show_name_header', 'show_club_header',
                        'show_time_header', 'show_delta_header', 'show_position_header',
                        'show_name', 'show_club', 'show_delta', 'show_position', 'show_podium',
                        'show_laps'):
                val = key in form
                if val != state.settings.get(key, True):
                    state.settings[key] = val
                    modified = True
            sort_val = form.get('results_sort', 'lane')
            if sort_val in ('lane', 'place') and sort_val != state.settings.get('results_sort', 'lane'):
                state.settings['results_sort'] = sort_val
                modified = True
            # Validated against the pair rather than stored as given: this reaches
            # every board through the relay's settings block, and an unknown value
            # would leave a client choosing its own meaning for the column.
            dir_val = form.get('lap_direction', 'up')
            if dir_val in ('up', 'down') and dir_val != state.settings.get('lap_direction', 'up'):
                state.settings['lap_direction'] = dir_val
                modified = True
            for key in ('locale', 'label_style'):
                if key in form and state.settings.get(key) != form.get(key):
                    state.settings[key] = form.get(key)
                    modified = True

        if 'splash_settings_submit' in form:
            # meet_title moved here with its input: it heads the splash screen on
            # the TV display, so it lives in that card rather than the column one.
            if 'meet_title' in form and state.settings.get('meet_title') != form.get('meet_title'):
                state.settings['meet_title'] = form.get('meet_title')
                modified = True
            splash_file = form.get('splash_file')
            if splash_file and splash_file.filename:
                filename = os.path.basename(splash_file.filename)
                save_upload(splash_file, os.path.join(state.IMAGES_DIR, filename))
                state.settings['splash_url'] = filename
                modified = True
            elif 'splash_url' in form:
                val = form.get('splash_url', '')
                if val != state.settings.get('splash_url', ''):
                    state.settings['splash_url'] = val
                    modified = True
            try:
                ci = max(3, int(form.get('carousel_interval', 10)))
                if ci != int(state.settings.get('carousel_interval', 10)):
                    state.settings['carousel_interval'] = ci
                    modified = True
            except (ValueError, TypeError):
                pass

        elif 'theme_update_submit' in form:
            file = form.get('theme_file')
            if file and file.filename and file.filename.endswith('.toml'):
                filename = os.path.basename(file.filename)
                save_upload(file, os.path.join(state.CUSTOM_THEME_FOLDER, filename))
                code = os.path.splitext(filename)[0]
                colors, fonts = state.load_theme(code)
                state.settings['active_theme'] = code
                state.settings['theme_colors'] = colors
                state.settings['theme_fonts']  = fonts
                modified = True
            else:
                code = form.get('theme_select',
                                state.settings.get('active_theme', 'default'))
                if code != state.settings.get('active_theme', 'default'):
                    colors, fonts = state.load_theme(code)
                    state.settings['active_theme'] = code
                    state.settings['theme_colors'] = colors
                    state.settings['theme_fonts']  = fonts
                    modified = True
                else:
                    colors = {}
                    for key in state.DEFAULT_THEME_COLORS:
                        form_key = 'color_' + key
                        if form_key in form:
                            colors[key] = form[form_key]
                    fonts = {}
                    val = form.get('font_family', '').strip()
                    if val:
                        fonts['family'] = val
                    val = form.get('font_digits', '').strip()
                    if val:
                        fonts['digits'] = val
                    val = form.get('font_timing', '').strip()
                    if val:
                        fonts['timing'] = val
                    state.settings['theme_colors'] = {**state.DEFAULT_THEME_COLORS, **colors}
                    state.settings['theme_fonts']  = {**state.DEFAULT_THEME_FONTS,  **fonts}
                    modified = True

        elif 'theme_save_submit' in form:
            name = form.get('theme_name', '').strip()
            if name:
                code   = re.sub(r'[^a-z0-9_-]', '_', name.lower())
                colors = {**state.DEFAULT_THEME_COLORS, **state.settings.get('theme_colors', {})}
                fonts  = {**state.DEFAULT_THEME_FONTS,  **state.settings.get('theme_fonts',  {})}
                lines  = [f'name = "{name}"\n', '\n', '[colors]\n']
                for k, v in colors.items():
                    lines.append(f'{k} = "{v}"\n')
                lines.append('\n[fonts]\n')
                for k, v in fonts.items():
                    lines.append(f'{k} = "{v}"\n')
                with open(os.path.join(state.CUSTOM_THEME_FOLDER, code + '.toml'), 'w') as fh:
                    fh.writelines(lines)
                state.settings['active_theme'] = code
                modified = True

        if 'cloud_settings_submit' in form:
            # Pi-global cloud connection (one relay key per box).
            for key in ('cloud_relay_url', 'cloud_relay_key'):
                val = form.get(key, '').strip()
                if val != state.settings.get(key, ''):
                    state.settings[key] = val
                    modified = True
            if modified:
                import relay as _relay
                _relay.update_metadata()

        if 'meet_appearance_submit' in form:
            # Per-meet cloud appearance — saved to the loaded meet's profile so
            # each meet (e.g. each day of a split meet) keeps its own.
            for key in ('meet_location', 'meet_sport', 'cloud_label_style',
                        'app_window_title', 'cloud_meet_title'):
                val = form.get(key, '').strip()
                if val != state.settings.get(key, ''):
                    state.settings[key] = val
                    modified = True

            # ── Picker image ──────────────────────────────────────────────────
            picker_image_error = None
            picker_image_file  = form.get('picker_image')
            if picker_image_file and picker_image_file.filename:
                try:
                    from PIL import Image
                    img      = Image.open(picker_image_file.file)
                    orig_name = os.path.basename(picker_image_file.filename)
                    img.save(os.path.join(state.PICKER_DIR, orig_name), optimize=True)
                    state.settings['active_picker_image'] = orig_name
                    modified = True
                except Exception as e:
                    picker_image_error = f'Could not process image: {e}'
            else:
                selected_pi = form.get('selected_picker_image', '').strip()
                if selected_pi != state.settings.get('active_picker_image', ''):
                    state.settings['active_picker_image'] = selected_pi
                    modified = True

            # ── Home screen icon ──────────────────────────────────────────────
            if form.get('remove_home_icon'):
                _render_home_icon('')
                state.settings['active_home_icon'] = ''
                modified = True
            icon_file = form.get('home_icon')
            if icon_file and icon_file.filename:
                try:
                    from PIL import Image
                    img = Image.open(icon_file.file)
                    if img.size not in [(512, 512), (1024, 1024)]:
                        icon_error = f'Icon must be 512×512 or 1024×1024 px (uploaded: {img.size[0]}×{img.size[1]}).'
                    else:
                        orig_name = os.path.basename(icon_file.filename)
                        img.save(os.path.join(state.ICONS_DIR, orig_name), 'PNG', optimize=True)
                        _render_home_icon(orig_name)
                        state.settings['active_home_icon'] = orig_name
                        modified = True
                except Exception as e:
                    icon_error = f'Could not process image: {e}'
            else:
                selected = form.get('selected_home_icon', '').strip()
                if selected != state.settings.get('active_home_icon', ''):
                    _render_home_icon(selected)
                    state.settings['active_home_icon'] = selected
                    modified = True

            if modified:
                state.save_meet_profile(state._active_meet_uid)
                import relay as _relay
                _relay.update_metadata()

        elif 'cloud_settings_submit' not in form:
            new_name = form.get('user_name', '').strip()
            if new_name and new_name != state.settings.get('username'):
                state.settings['username'] = new_name
                modified = True
            new_pass = form.get('password', '').strip()
            if new_pass and new_pass != state.settings.get('password'):
                state.settings['password'] = new_pass
                modified = True
            # Generic sweep, so a form field with no dedicated handler still
            # saves. Two rules keep it from doing damage:
            #
            # 1. Skip anything a typed handler already changed this request. This
            #    runs *after* them, and it used to overwrite their clamped value
            #    with the raw form text — silently undoing every `max(...)` bound
            #    above. A carousel interval of 1s got through a 3s minimum, and a
            #    finish debounce of 0.1s through a 0.5s one.
            # 2. Keep the setting's type. Comparing an int against form text is
            #    always unequal, so every save rewrote `10` as `'10'` and `True`
            #    as `'1'`, and settings.json drifted to strings. Readers coerce,
            #    so nothing broke — but relay.py ships these values to the cloud
            #    as JSON, where a client checking `=== true` would.
            for k in state.settings.keys():
                if k in ('username', 'password') or k not in form:
                    continue
                if state.settings[k] != before.get(k):
                    continue
                value = _coerce_like(before.get(k), form.get(k))
                if value is not None and value != state.settings[k]:
                    state.settings[k] = value
                    modified = True

        if modified:
            state.save_settings()
            # `splash_settings_submit` is in this list because /config carries the
            # title, the carousel image list and its interval — a native display
            # only re-reads them on `reload`, so without this it would keep showing
            # the old title and rotate a stale set of images.
            # A console change belongs here for the same reason: `/config` and the
            # relay's `settings` block both carry which console this is and whether
            # it times anything (docs/api.md §5.4, §6), and a client that has
            # already fetched either only re-reads it on `reload`. Switching to the
            # manual console mid-meet is exactly when a phone has to be told — it is
            # the moment its Results screen stops being able to fill (`app.md`
            # `A-11`). Re-register first, so the config the reload sends them back
            # for is the new one.
            if console_changed:
                relay.update_metadata()
            if ('display_settings_submit' in form or 'theme_update_submit' in form
                    or 'splash_settings_submit' in form or console_changed):
                bus.emit('/scoreboard', 'reload')
                bus.emit('/results', 'reload')
                relay.relay_emit('reload', {})

    load_custom_decoders(state.CUSTOM_DECODERS_FOLDER)

    comm_port_list = [(port, '%s: %s' % (port, desc))
                      for port, desc, id in serial.tools.list_ports.comports()]
    if state.settings['serial_port'] not in [port for port, desc in comm_port_list]:
        comm_port_list.insert(0, (state.settings['serial_port'], state.settings['serial_port']))

    splash_url_list = sorted(
        f for f in os.listdir(state.IMAGES_DIR)
        if os.path.isfile(os.path.join(state.IMAGES_DIR, f))
    )

    meet_file_list = sorted(
        os.path.basename(f)
        for f in glob.glob(os.path.join(state.MEET_FOLDER, '*.csv')) +
                 glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf'))
    )

    ui_lang = state.ui_locale(request)
    # Whether the user has explicitly pinned a panel language (vs. "Auto"). Only
    # an installed locale counts, so a stale cookie for a removed locale reads as
    # Auto — matching how ui_locale() resolves it.
    installed_ui = {c for c, _ in state.list_locales()}
    ui_lang_cookie = request.cookies.get('ui_lang', '')
    ui_lang_cookie = ui_lang_cookie if ui_lang_cookie in installed_ui else ''
    return render(
        request,
        'settings.html',
        t=state.settings_strings(ui_lang),
        ui_locale=ui_lang,
        ui_lang_cookie=ui_lang_cookie,
        meet_file_list=meet_file_list,
        active_meet_file=state._active_meet_file,
        meet_title=state.settings['meet_title'],
        serial_port=state.settings['serial_port'],
        serial_port_list=comm_port_list,
        console_type=state.settings.get('console_type', 'cts_gen6'),
        console_options=[(key, label) for key, label, _ in CONSOLE_OPTIONS],
        console_info=console_info_for(state.settings.get('console_type', 'cts_gen6')),
        # Whether this console has a wire at all. Gates the serial-port picker, the
        # Connection badge and the Serial Monitor: a manually driven console has
        # nothing to open, nothing to connect to and no packets to watch. Read off
        # the live decoder so a portless local-only plugin gets the same treatment.
        console_requires_serial=state._decoder.requires_serial,
        user_name=state.settings['username'],
        splash_url_list=splash_url_list,
        splash_url=state.settings.get('splash_url', ''),
        carousel_interval=int(state.settings.get('carousel_interval', 10)),
        locale=state.settings.get('locale', 'en'),
        locale_list=state.list_locales(),
        label_style=state.settings.get('label_style', 'short'),
        num_lanes=int(state.settings.get('num_lanes', 6)),
        show_lane_header=state.settings.get('show_lane_header', True),
        show_name_header=state.settings.get('show_name_header', True),
        show_club_header=state.settings.get('show_club_header', True),
        show_time_header=state.settings.get('show_time_header', True),
        show_delta_header=state.settings.get('show_delta_header', True),
        show_position_header=state.settings.get('show_position_header', True),
        show_name=state.settings.get('show_name', True),
        show_club=state.settings.get('show_club', True),
        show_delta=state.settings.get('show_delta', True),
        show_position=state.settings.get('show_position', True),
        show_laps=state.settings.get('show_laps', False),
        lap_direction=state.settings.get('lap_direction', 'up'),
        results_sort=state.settings.get('results_sort', 'lane'),
        active_theme=state.settings.get('active_theme', 'default'),
        theme_list=state.list_builtin_themes(),
        custom_theme_list=state.list_custom_themes(),
        custom_theme_codes=[c for c, _ in state.list_custom_themes()],
        theme_colors={**state.DEFAULT_THEME_COLORS, **state.settings.get('theme_colors', {})},
        # Baseline for the "changed from default" hint: the active theme's own
        # colours (built-in default theme → factory DEFAULT_THEME_COLORS).
        theme_color_defaults=state.load_theme(state.settings.get('active_theme', 'default'))[0],
        theme_fonts={**state.DEFAULT_THEME_FONTS,  **state.settings.get('theme_fonts', {})},
        cloud_relay_url=state.settings.get('cloud_relay_url', ''),
        cloud_relay_key=state.settings.get('cloud_relay_key', ''),
        meet_location=state.settings.get('meet_location', ''),
        meet_sport=state.settings.get('meet_sport', ''),
        cloud_label_style=state.settings.get('cloud_label_style', 'short'),
        app_window_title=state.settings.get('app_window_title', ''),
        cloud_meet_title=state.settings.get('cloud_meet_title', ''),
        has_home_icon=os.path.exists(state.HOME_ICON_PATH),
        icon_error=icon_error if request.method == 'POST' else None,
        active_home_icon=state.settings.get('active_home_icon', ''),
        icon_list=sorted(
            f for f in (os.listdir(state.ICONS_DIR) if os.path.isdir(state.ICONS_DIR) else [])
            if f.endswith('.png') and f not in ('home_icon.png', 'home_icon_512.png')
            and os.path.isfile(os.path.join(state.ICONS_DIR, f))
        ),
        active_picker_image=state.settings.get('active_picker_image', ''),
        picker_image_error=(locals().get('picker_image_error')
                            if request.method == 'POST' else None),
        picker_image_list=sorted(
            f for f in (os.listdir(state.PICKER_DIR) if os.path.isdir(state.PICKER_DIR) else [])
            if os.path.isfile(os.path.join(state.PICKER_DIR, f))
        ),
    )


@router.get('/home_icon')
def route_home_icon():
    if not os.path.exists(state.HOME_ICON_PATH):
        raise HTTPException(404)
    return FileResponse(state.HOME_ICON_PATH, media_type='image/png',
                        headers={'Cache-Control': 'no-cache'})


@router.get('/home_icon_512')
def route_home_icon_512():
    path = state.HOME_ICON_512_PATH if os.path.exists(state.HOME_ICON_512_PATH) else state.HOME_ICON_PATH
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-cache'})


@router.get('/picker_image')
def route_picker_image():
    active = state.settings.get('active_picker_image', '')
    if not active:
        raise HTTPException(404)
    path = os.path.join(state.PICKER_DIR, active)
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, headers={'Cache-Control': 'no-cache'})


@router.get('/manifest.json')
def route_manifest():
    app_title = (state.settings.get('app_window_title') or
                 state.meet.meet_info.get('name') or
                 state.settings.get('meet_title') or 'Splouch')
    icons = ([
        {'src': '/home_icon',     'sizes': '192x192', 'type': 'image/png'},
        {'src': '/home_icon_512', 'sizes': '512x512', 'type': 'image/png'},
    ] if os.path.exists(state.HOME_ICON_PATH) else [
        {'src': '/static/img/default_mobile_icon.png', 'sizes': '1024x1024', 'type': 'image/png'},
    ])
    manifest = {
        'name':             app_title,
        'short_name':       app_title,
        'start_url':        '/',
        'display':          'standalone',
        'background_color': '#000000',
        'theme_color':      '#000000',
        'icons':            icons,
    }
    return Response(json.dumps(manifest), media_type='application/manifest+json')
