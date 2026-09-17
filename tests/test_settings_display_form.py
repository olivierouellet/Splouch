"""The Display tab's two forms, and which one owns the meet title.

The Title heads the splash screen on the TV display, so it sits at the top of the
Splash Screen card rather than with the column settings. That card is a *separate
form* posting `splash_settings_submit`, so moving the input means moving its
handler too — and making sure the move does not lose the `reload` broadcast that
tells displays to re-read `/config`.

Qt-free: this is the server, driven through the real route function.
"""
import asyncio
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import bus                                     # noqa: E402
import state                                   # noqa: E402
from routes.settings import route_settings     # noqa: E402

from conftest import settings_markup  # noqa: E402


class _FakeRequest:
    """Enough of a Starlette Request for the settings POST path."""

    def __init__(self, form):
        self._form = form
        self.method = 'POST'
        self.session = {'user': 'test'}
        self.url = type('U', (), {'path': '/settings'})()
        self.cookies = {}
        self.headers = {}

    async def form(self):
        return self._form


def _post(form, monkeypatch):
    """Run the settings route and report which reload channels were emitted."""
    emitted = []
    monkeypatch.setattr(bus, 'emit', lambda channel, event, data=None:
                        emitted.append((channel, event)))
    monkeypatch.setattr(state, 'save_settings', lambda: None)
    try:
        asyncio.run(route_settings(_FakeRequest(form)))
    except Exception:
        # Rendering the template needs a real Request; the settings-writing half
        # has already run by then, which is the half under test.
        pass
    return emitted


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setitem(state.settings, 'meet_title', 'Old title')
    monkeypatch.setitem(state.settings, 'carousel_interval', 10)
    return state.settings


# ── The form the input lives in ────────────────────────────────────────────────

def _form_body(form_id):
    html = settings_markup()
    return html.split(f'id="{form_id}"')[1].split('</form>')[0]


def test_the_title_input_is_in_the_splash_form():
    splash = _form_body('splash-settings-form')
    assert 'name="meet_title"' in splash
    assert 'name="meet_title"' not in _form_body('display-settings-form')


def test_the_title_is_the_first_field_of_the_splash_card():
    splash = _form_body('splash-settings-form')
    assert splash.index('name="meet_title"') < splash.index('t.disp_splash_screen')


def test_the_title_auto_submits_like_the_rest_of_that_card():
    """The Splash Screen card has no Update button — every field saves on change."""
    splash = _form_body('splash-settings-form')
    after_input = splash.split('name="meet_title"')[1][:250]
    assert '_fsubmit' in after_input


# ── The handler that reads it ──────────────────────────────────────────────────

def test_the_splash_form_saves_the_title(settings, monkeypatch):
    _post({'splash_settings_submit': '1', 'meet_title': 'Championnat provincial'},
          monkeypatch)
    assert settings['meet_title'] == 'Championnat provincial'


def test_the_display_form_no_longer_touches_the_title(settings, monkeypatch):
    """It is not in that form any more, so a post from it must leave it alone."""
    _post({'display_settings_submit': '1', 'locale': 'fr'}, monkeypatch)
    assert settings['meet_title'] == 'Old title'


def test_saving_the_title_tells_the_displays_to_re_read_config(settings, monkeypatch):
    """Without this the TV keeps the old title until something else reloads.

    `/config` carries `meet_title`, and a native client only re-reads it on
    `reload` — the browser gets a fresh render on navigation, so this gap was
    invisible before the Qt display existed.
    """
    emitted = _post({'splash_settings_submit': '1', 'meet_title': 'New title'},
                    monkeypatch)
    assert ('/scoreboard', 'reload') in emitted


def test_changing_the_carousel_also_triggers_a_reload(settings, monkeypatch):
    """Same reason: `/config` carries `carousel_images` and `carousel_interval`."""
    emitted = _post({'splash_settings_submit': '1', 'carousel_interval': '25'},
                    monkeypatch)
    assert settings['carousel_interval'] == 25
    assert ('/scoreboard', 'reload') in emitted


def test_a_string_interval_still_reaches_the_display_as_a_number(settings,
                                                                 monkeypatch):
    """Settings saved *before* the type fix are still strings on disk.

    Every reader coerces, so those installations keep working and repair
    themselves the next time each field is saved. This pins that safety net.
    """
    from scoreboard.theme import Config
    monkeypatch.setitem(state.settings, 'carousel_interval', '25')
    import web
    assert Config(web.display_config()).carousel_interval == 25


def test_an_unchanged_title_does_not_broadcast(settings, monkeypatch):
    """The card auto-submits on blur, so no-op posts are routine."""
    emitted = _post({'splash_settings_submit': '1', 'meet_title': 'Old title'},
                    monkeypatch)
    assert emitted == []


# ── The generic sweep ──────────────────────────────────────────────────────────
# A catch-all near the end of the handler saves any form field whose name matches
# a settings key, so a field with no dedicated handler still persists. It runs
# *after* the typed handlers, which is where two bugs came from.

@pytest.mark.parametrize('key, submit, posted, minimum', [
    ('carousel_interval', 'splash_settings_submit', '1',   3),
    ('finish_debounce',   'timing_tuning_submit',   '0.1', 0.5),
    ('split_min_duration', 'timing_tuning_submit',  '0.1', 0.5),
])
def test_the_sweep_does_not_undo_a_clamp(key, submit, posted, minimum, monkeypatch):
    """It used to overwrite the clamped value with the raw form text.

    A carousel interval of 1s got past a 3s minimum — a strobing splash — and a
    finish debounce of 0.1s past a 0.5s one, which is what stops results flapping
    on the board at the end of a race.
    """
    monkeypatch.setitem(state.settings, key, 9.0)
    _post({submit: '1', key: posted}, monkeypatch)
    assert float(state.settings[key]) >= minimum, 'the clamp was defeated'


@pytest.mark.parametrize('key, submit, posted, expected', [
    ('num_lanes',         'pool_setup_submit',      '10',  10),
    ('carousel_interval', 'splash_settings_submit', '25',  25),
])
def test_numbers_stay_numbers(key, submit, posted, expected, monkeypatch):
    """Comparing an int against form text is always unequal, so every save used
    to rewrite `10` as `'10'` and settings.json drifted to strings."""
    monkeypatch.setitem(state.settings, key, 1 if key == 'num_lanes' else 300)
    _post({submit: '1', key: posted}, monkeypatch)
    assert state.settings[key] == expected
    assert isinstance(state.settings[key], int), \
        f'{key} came back as {type(state.settings[key]).__name__}'


def test_booleans_stay_booleans(monkeypatch):
    """`relay.py` ships these to the cloud as JSON, where `'1'` is not `true`."""
    monkeypatch.setitem(state.settings, 'show_name', True)
    monkeypatch.setitem(state.settings, 'show_club', True)
    _post({'display_settings_submit': '1', 'show_name': '1'}, monkeypatch)
    assert state.settings['show_name'] is True
    assert state.settings['show_club'] is False, 'an unticked box must clear it'


def test_the_sweep_still_saves_a_field_with_no_typed_handler(monkeypatch):
    """Its whole purpose — do not break it while fixing it."""
    monkeypatch.setitem(state.settings, 'meet_location', 'Old pool')
    _post({'some_other_submit': '1', 'meet_location': 'Piscine olympique'},
          monkeypatch)
    assert state.settings['meet_location'] == 'Piscine olympique'


def test_unparseable_input_leaves_the_setting_alone(monkeypatch):
    """Better a stale number than a string where a number is expected."""
    monkeypatch.setitem(state.settings, 'num_lanes', 8)
    _post({'some_other_submit': '1', 'num_lanes': 'eight'}, monkeypatch)
    assert state.settings['num_lanes'] == 8
