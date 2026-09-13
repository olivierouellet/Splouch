"""`GET /i18n/{lang}` and `GET /locales` — strings for clients that render themselves.

The Qt display has always been *served* its status strings; the phone apps were
told to embed theirs, which put `shared/locales/` in three repos and made a fourth
language two store submissions (docs/app.md `T-05`). These endpoints
make every non-browser client fetch what the TV already fetches.

Two things carry the weight here. The English merge, per key, so a half-translated
locale falls back word by word instead of rendering a blank header; and the split
between the *served* file, `shared/locales/{lang}.toml`, which is what a spectator
reads and must be complete, and the optional `panel/{lang}.toml`, which is the
operator's console and may be missing (docs/admin.md "Localisation").
"""
import glob
import re
import tomllib
import io
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='splouch-i18n-test-'))
sys.path.insert(0, os.path.join(REPO, 'cloud'))

import state                     # noqa: E402
import web                       # noqa: E402
import cloud_server as cs        # noqa: E402


class _Req:
    """Only `.headers` is read, and only for `if-none-match`."""
    def __init__(self, etag=None):
        self.headers = {'if-none-match': etag} if etag else {}


LOCALES = os.path.join(REPO, 'shared', 'locales')


def _served(code):
    with open(os.path.join(LOCALES, f'{code}.toml'), 'rb') as f:
        return tomllib.load(f)


def _panel(code):
    with open(os.path.join(LOCALES, 'panel', f'{code}.toml'), 'rb') as f:
        return tomllib.load(f)


def _keys(table, prefix=''):
    """Every leaf key of a TOML table, dotted. `[aliases]` is a language's own URL
    shorthands (`/tableau` → `/scoreboard`), not a translation, so it is skipped."""
    out = set()
    for k, v in table.items():
        if k == 'aliases' and not prefix:
            continue
        if isinstance(v, dict):
            out |= _keys(v, f'{prefix}{k}.')
        else:
            out.add(prefix + k)
    return out


# ── The bundled table, as shipped ─────────────────────────────────────────────

def test_every_shipped_locale_is_listed_with_its_own_name():
    assert state.available_locales() == [('en', 'English'), ('es', 'Español'),
                                         ('fr', 'Français')]


@pytest.mark.parametrize('build', [state.i18n_bundle, cs._i18n_bundle])
def test_a_bundle_carries_chrome_and_both_label_styles(build):
    """One fetch, both axes: the client picks language *and* short/long from it."""
    b = build('fr')
    assert b['lang'] == 'fr'
    assert b['mobile']['scoreboard'] == 'Tableau'
    assert b['display']['connection_lost']
    assert b['labels']['short']['event'] == 'ÉP'
    assert b['labels']['long']['event']  == 'ÉPREUVE'


@pytest.mark.parametrize('build', [state.i18n_bundle, cs._i18n_bundle])
def test_an_unknown_language_falls_back_to_english(build):
    """`lang` comes off the URL, so it must never reach the filesystem unchecked."""
    assert build('de')['lang'] == 'en'
    assert build('../../etc/passwd')['lang'] == 'en'


@pytest.mark.parametrize('build', [state.i18n_bundle, cs._i18n_bundle])
def test_both_servers_agree_on_the_shipped_languages(build):
    assert build('es')['mobile']['scoreboard'] == 'Marcador'
    # A styled key, so `long` is the long word; `place` is narrow and short in both.
    assert build('es')['labels']['long']['event'] == 'PRUEBA'
    assert build('es')['labels']['long']['place'] == 'POS'


# ── A locale that is only half translated ─────────────────────────────────────

@pytest.fixture
def half_translated(monkeypatch, tmp_path):
    """A locale with one chrome string and one label, and nothing else."""
    (tmp_path / 'en.toml').write_text(io.open(
        os.path.join(REPO, 'shared', 'locales', 'en.toml'), encoding='utf-8').read(),
        encoding='utf-8')
    (tmp_path / 'zz.toml').write_text(
        '[meta]\nname = "Test"\n'
        '[mobile]\nscoreboard = "Tableau ZZ"\n'
        '[labels]\nlane = { short = "ZL", long = "ZLANE" }\n', encoding='utf-8')
    monkeypatch.setattr(state, 'LOCALES_DIR', str(tmp_path))
    return tmp_path


def test_untranslated_keys_fall_back_to_english_word_by_word(half_translated):
    """A missing key must render an English word, never an empty header."""
    b = state.i18n_bundle('zz')
    assert b['mobile']['scoreboard'] == 'Tableau ZZ'   # translated
    assert b['mobile']['results']    == 'Results'      # merged from English
    assert b['labels']['short']['lane']  == 'ZL'
    assert b['labels']['short']['event'] == 'EV'


def test_a_style_a_locale_omits_falls_back_to_the_other(monkeypatch, tmp_path):
    """A file may define one form; an empty header is the worse answer."""
    (tmp_path / 'en.toml').write_text(
        '[labels]\nlane = { long = "LANE" }\n', encoding='utf-8')
    monkeypatch.setattr(state, 'LOCALES_DIR', str(tmp_path))
    assert state.i18n_bundle('en')['labels']['short']['lane'] == 'LANE'


# ── One file is one language, and it has to be whole ──────────────────────────
#
# The served file is what a spectator reads on a phone, in an app or on the TV, so
# it is gated: every language ships every key English has. The panel file is the
# operator's console and is not gated — a new language may leave it out entirely.

SHIPPED = sorted(os.path.splitext(os.path.basename(p))[0]
                 for p in glob.glob(os.path.join(LOCALES, '*.toml')))


@pytest.mark.parametrize('code', [c for c in SHIPPED if c != 'en'])
def test_every_served_language_carries_every_english_key(code):
    """The gate. A key missing here renders as English on every client at once, and
    the apps only delete their own English floors because this holds."""
    missing = _keys(_served('en')) - _keys(_served(code))
    assert not missing, f'{code}.toml lacks {sorted(missing)}'


@pytest.mark.parametrize('code', [c for c in SHIPPED if c != 'en'])
def test_no_served_language_invents_a_key_english_lacks(code):
    """English is the fallback, so a key only another language has is unreachable
    from the merge and is almost always a typo."""
    extra = _keys(_served(code)) - _keys(_served('en'))
    assert not extra, f'{code}.toml has {sorted(extra)} that en.toml lacks'


def test_the_served_file_holds_only_what_a_spectator_reads():
    """The whole point of the split: a translator sees the spectator words and no
    operator string, and the served bundle cannot grow a panel string by accident."""
    assert set(_served('en')) == {'meta', 'labels', 'event_name', 'aliases', 'mobile', 'display'}
    # `chrome` is the fourth because both operator pages draw the same sidebar and
    # theme switcher; its words live once rather than once per page.
    assert set(_panel('en')) == {'preview', 'cloud', 'settings', 'chrome'}


@pytest.mark.parametrize('style', ['short', 'long'])
def test_the_style_reaches_event_and_heat_only(style):
    """`T-09`: lane and place are the narrow columns and stay short in both styles.

    This is the table `GET /config` hands the Qt board and `GET /i18n/{lang}` hands a
    phone, so getting it right here is what keeps a long word out of a 5%-wide column
    on every client at once.
    """
    labels = state.resolve_labels(state._locale_section('fr', 'labels'), style)
    assert labels['lane']  == 'CL'
    assert labels['place'] == 'POS'
    assert labels['event'] == ('ÉPREUVE' if style == 'long' else 'ÉP')
    assert labels['heat']  == ('SÉRIE'   if style == 'long' else 'SÉR')


def test_a_language_without_a_panel_file_reads_the_panel_in_english(monkeypatch, tmp_path):
    """Adding a language is the served file alone (docs/admin.md)."""
    (tmp_path / 'en.toml').write_text('[settings]\nsave = "Save"\n[preview]\nmeet = "Meet"\n',
                                      encoding='utf-8')
    monkeypatch.setattr(state, 'PANEL_LOCALES_DIR', str(tmp_path))
    assert state.settings_strings('zz') == {'save': 'Save'}
    assert state._panel_section('zz', 'preview') == {'meet': 'Meet'}


def test_a_partial_panel_file_degrades_word_by_word(monkeypatch, tmp_path):
    (tmp_path / 'en.toml').write_text('[settings]\nsave = "Save"\ncancel = "Cancel"\n',
                                      encoding='utf-8')
    (tmp_path / 'zz.toml').write_text('[settings]\nsave = "Sauver"\n', encoding='utf-8')
    monkeypatch.setattr(state, 'PANEL_LOCALES_DIR', str(tmp_path))
    assert state.settings_strings('zz') == {'save': 'Sauver', 'cancel': 'Cancel'}


def test_the_cloud_admin_reads_the_panel_file_the_same_way(monkeypatch, tmp_path):
    (tmp_path / 'panel').mkdir()
    (tmp_path / 'panel' / 'en.toml').write_text('[cloud]\nlogout = "Log out"\nsave = "Save"\n',
                                                encoding='utf-8')
    (tmp_path / 'panel' / 'fr.toml').write_text('[cloud]\nlogout = "Déconnexion"\n',
                                                encoding='utf-8')
    monkeypatch.setattr(cs, 'LOCALES_DIR', str(tmp_path))
    monkeypatch.setattr(cs, '_panel_cache', {})
    assert cs._panel_strings('fr', 'cloud') == {'logout': 'Déconnexion', 'save': 'Save'}
    assert cs._panel_strings('de', 'cloud') == {'logout': 'Log out', 'save': 'Save'}


# ── Words about the meet are the server's; words about the app are the app's ──
#
# `[mobile]` carries every word a spectator reads that the web pages also show —
# the tabs, the empty states, the filter sheet, the picker's chrome and compliance
# text. It does not carry words about the app or the device (server sheet,
# connection errors, OS requirements): those are native in each app repo.

PICKER_KEYS = ('page_title', 'no_meets', 'unnamed_meet', 'results_disclaimer',
               'privacy_note', 'offline', 'language', 'language_auto', 'prefs_title',
               'prefs_auto', 'prefs_labels', 'prefs_short', 'prefs_long')
FILTER_KEYS = ('filter', 'no_filters', 'no_search_results', 'no_matches', 'swimmer', 'club')


@pytest.mark.parametrize('key', PICKER_KEYS + FILTER_KEYS)
def test_the_picker_and_filter_words_are_served_to_every_client(key):
    """Both apps listed these as words the server lacked and carried an English
    floor for them. The preference words existed all along, in the admin section;
    the filter words were hard-coded in French in the web template."""
    for build in (state.i18n_bundle, cs._i18n_bundle):
        for code in SHIPPED:
            assert build(code)['mobile'].get(key), f'{code} lacks [mobile].{key}'


def test_the_native_picker_reads_its_strings_from_the_served_table():
    """`GET /picker/config` §5.7 — the compliance text must stay correctable
    without an app release, so it has to be in the file that is served."""
    for key in cs._PICKER_STRING_KEYS:
        assert key in _served('en')['mobile'], key


def test_the_schedule_template_hard_codes_no_language():
    """Two French strings sat here for a season while the apps lacked the keys."""
    src = io.open(os.path.join(REPO, 'shared', 'templates', 'schedule.html'),
                  encoding='utf-8').read()
    for word in ('Aucun', 'Nageur'):
        assert word not in src, f'schedule.html still hard-codes {word!r}'
    for key in ('no_search_results', 'no_matches', 'swimmer', 'club'):
        assert f't.{key}' in src, f'schedule.html does not read [mobile].{key}'


def test_the_words_about_the_app_are_not_the_servers():
    """A timing server has no business translating an Android version requirement.
    If one of these lands in `[mobile]`, the line has moved and app.md T-05 with it."""
    for key in ('needs_android_14', 'cleartext_not_local', 'not_splouch',
                'server_unreachable', 'add_server', 'nearby'):
        assert key not in _served('en')['mobile'], key


# ── Caching ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('etagged', [None, 'cloud'])
def test_the_same_bundle_returns_the_same_etag(etagged):
    from routes.i18n import etagged as pi_etagged
    tag = cs._etagged if etagged else pi_etagged
    first  = tag(_Req(), {'a': 1})
    second = tag(_Req(), {'a': 1})
    assert first.headers['etag'] == second.headers['etag']
    assert first.headers['cache-control'] == 'no-cache'


@pytest.mark.parametrize('etagged', [None, 'cloud'])
def test_a_client_that_already_has_it_gets_a_304(etagged):
    from routes.i18n import etagged as pi_etagged
    tag = cs._etagged if etagged else pi_etagged
    etag = tag(_Req(), {'a': 1}).headers['etag']
    again = tag(_Req(etag), {'a': 1})
    assert again.status_code == 304
    assert again.body == b''


def test_a_changed_string_changes_the_etag():
    from routes.i18n import etagged as pi_etagged
    assert (pi_etagged(_Req(), {'a': 1}).headers['etag'] !=
            pi_etagged(_Req(), {'a': 2}).headers['etag'])


# ── The visitor's choice, per device ──────────────────────────────────────────
#
# The choice is two cookies (`T-08`): the picker writes them, every page reads them,
# and the URL carries nothing. `?lang=` / `?style=` still win for one request so a
# shared link opens as sent, and the shell turns that into the cookie. What matters
# is that *no* choice is byte-for-byte what the operator configured — the override
# must not quietly restyle every board.


class _Q:
    """A request with query params and cookies, which is all these read."""
    def __init__(self, cookies=None, **params):
        self.query_params = params
        self.cookies = cookies or {}


class _Resp:
    def __init__(self): self.cookies = {}
    def set_cookie(self, key, value, **kw): self.cookies[key] = (value, kw)


_MEET = {'settings': {
    'locale': 'fr', 'label_style': 'short',
    'labels': {'event': 'ÉP', 'lane': 'CL'},
}}


def test_no_choice_renders_exactly_what_the_operator_configured():
    """Not "the same words" — the same object. Nothing re-resolved, nothing lost."""
    assert cs._client_lang(_Q(), _MEET) == 'fr'
    assert cs._client_style(_Q(), _MEET) == 'short'
    assert cs._client_labels(_MEET, 'fr', 'short') is _MEET['settings']['labels']


def test_choosing_a_style_or_language_reads_the_served_table():
    """The same body `GET /i18n/{lang}` serves, so a phone page and an app that
    made the same choice show the same header."""
    assert cs._client_labels(_MEET, 'fr', 'long')['event'] == 'ÉPREUVE'
    assert cs._client_labels(_MEET, 'es', 'long')['event'] == 'PRUEBA'
    # `T-09`: lane is narrow and stays short whatever the style says.
    assert cs._client_labels(_MEET, 'es', 'long')['lane'] == 'CA'


def test_a_stale_link_falls_back_instead_of_breaking_the_board():
    assert cs._client_lang(_Q(lang='de'), _MEET) == 'fr'
    assert cs._client_lang(_Q(lang='../en'), _MEET) == 'fr'
    assert cs._client_style(_Q(style='tiny'), _MEET) == 'short'


def test_the_choice_is_honoured_when_it_is_available():
    assert cs._client_lang(_Q(lang='es'), _MEET) == 'es'
    assert cs._client_style(_Q(style='long'), _MEET) == 'long'


def test_the_cookie_is_the_choice_and_the_url_is_a_one_shot_override():
    """A bookmark carries nothing and still opens right; a shared link carrying
    `?lang=` opens as its sender saw it, that once."""
    cookies = {'splouch_lang': 'es', 'splouch_style': 'long'}
    assert cs._client_lang(_Q(cookies), _MEET) == 'es'
    assert cs._client_style(_Q(cookies), _MEET) == 'long'
    assert cs._client_lang(_Q(cookies, lang='en'), _MEET) == 'en'
    assert cs._client_style(_Q(cookies, style='short'), _MEET) == 'short'
    # A cookie for a language this server no longer ships reads as no choice.
    assert cs._client_lang(_Q({'splouch_lang': 'de'}), _MEET) == 'fr'


def test_the_picker_follows_the_cookie_before_the_browser(monkeypatch):
    class _P(_Q):
        def __init__(self, cookies=None, accept='', **params):
            super().__init__(cookies, **params)
            self.headers = {'Accept-Language': accept}
    monkeypatch.setattr(cs, '_load_creds', lambda: {})
    assert cs._picker_lang(_P(accept='es-ES,es;q=0.9')) == 'es'
    assert cs._picker_lang(_P({'splouch_lang': 'fr'}, accept='es-ES')) == 'fr'
    assert cs._picker_lang(_P({'splouch_lang': 'fr'}, accept='es-ES', lang='en')) == 'en'


@pytest.mark.parametrize('remember', [cs._remember_prefs, web.remember_prefs])
def test_the_shell_turns_a_link_parameter_into_the_cookie(remember):
    """After the shell, the tabs and every later visit need no parameter at all."""
    resp = remember(_Q(lang='es', style='long'), _Resp())
    assert resp.cookies['splouch_lang'][0] == 'es'
    assert resp.cookies['splouch_style'][0] == 'long'
    assert resp.cookies['splouch_lang'][1]['max_age'] >= 30 * 24 * 3600
    # Nothing valid on the URL, nothing written — and nothing rewritten needlessly.
    assert remember(_Q(lang='de'), _Resp()).cookies == {}
    assert remember(_Q({'splouch_lang': 'es'}, lang='es'), _Resp()).cookies == {}


def test_both_servers_name_the_cookies_the_same():
    """One device, two servers, one preference: the names must match exactly."""
    assert cs.PREF_COOKIES == web.PREF_COOKIES


def test_the_pi_serves_its_own_settings_when_nothing_is_chosen(monkeypatch):
    """Same rule on the Pi, against its settings rather than a meet's."""
    monkeypatch.setitem(state.settings, 'locale', 'en')
    monkeypatch.setitem(state.settings, 'label_style', 'long')
    ctx = web.client_strings(_Q())
    assert (ctx['lang'], ctx['ui_style']) == ('en', 'long')
    assert ctx['labels'] == state.load_locale()
    assert ctx['t'] == state._mobile_strings()


def test_the_pi_honours_a_choice_and_hands_the_style_to_the_template(monkeypatch):
    """`ui_style` is what the shell stamps on its tab URLs, so it has to come back
    out of here even when it equals the default."""
    monkeypatch.setitem(state.settings, 'locale', 'en')
    monkeypatch.setitem(state.settings, 'label_style', 'long')
    for req in (_Q(lang='fr', style='short'),
                _Q({'splouch_lang': 'fr', 'splouch_style': 'short'})):
        ctx = web.client_strings(req)
        assert ctx['lang'] == 'fr' and ctx['ui_style'] == 'short'
        assert ctx['labels']['event'] == 'ÉP'
        assert ctx['t']['scoreboard'] == 'Tableau'


# ── Which server am I talking to ──────────────────────────────────────────────
#
# A native app can be pointed at a Pi or at a cloud (`P-11`), and the two are not
# interchangeable: a Pi has one meet and no picker. `GET /server` is how a client
# is told rather than inferring it from a 404, and it carries the contract versions
# now that both documents are numbered.

def test_each_server_says_what_kind_it_is():
    from routes.i18n import route_server as pi_server
    assert pi_server()['kind'] == 'pi'
    assert cs.route_server()['kind'] == 'cloud'


def test_each_server_names_itself_for_the_menu():
    """A list of servers is unusable if every row reads "Splouch"."""
    from routes.i18n import route_server as pi_server
    assert pi_server()['name']
    assert cs.route_server()['name']


@pytest.mark.parametrize('doc, key', [('api.md', 'api'), ('app.md', 'app')])
def test_the_handshake_matches_the_contract_it_claims(doc, key):
    """The versions a build advertises are the ones the documents carry.

    Two contracts, two version headers, and a handshake that repeats them: without
    this the code drifts from the docs silently and a client negotiates against a
    number nobody maintains.
    """
    import re
    from routes.i18n import route_server as pi_server
    text = io.open(os.path.join(REPO, 'docs', doc), encoding='utf-8').read()
    stated = re.search(r'\*\*Contract version: `(v\d+)`\*\*', text).group(1)
    assert pi_server()['contract'][key] == stated
    assert cs.route_server()['contract'][key] == stated


class _BaseUrlReq:
    def __init__(self, base): self.base_url = base


def test_the_directory_lists_this_server_with_no_configuration(monkeypatch, tmp_path):
    """Pointed at any cloud, a client gets at least that cloud back."""
    monkeypatch.setattr(cs, 'SERVERS_FILE', str(tmp_path / 'absent.json'))
    servers = cs.route_servers(_BaseUrlReq('https://splouch.example/'))['servers']
    assert [s['url'] for s in servers] == ['https://splouch.example']
    assert servers[0]['kind'] == 'cloud'


def test_the_directory_adds_what_the_operator_published(monkeypatch, tmp_path):
    """A club standing up its own instance must not need a store release to be
    reachable — which is the whole reason this is fetched (`P-11`)."""
    f = tmp_path / 'servers.json'
    f.write_text('[{"name": "Club X", "url": "https://x.example/"},'
                 ' {"url": "https://splouch.example"},'
                 ' {"name": "no url"}]', encoding='utf-8')
    monkeypatch.setattr(cs, 'SERVERS_FILE', str(f))
    servers = cs.route_servers(_BaseUrlReq('https://splouch.example/'))['servers']
    # This server first, the published one after it; the duplicate and the
    # entry with no URL are dropped rather than rendering a dead row.
    assert [(s['name'], s['url']) for s in servers] == [
        ('Splouch', 'https://splouch.example'), ('Club X', 'https://x.example')]


def test_a_broken_directory_file_does_not_take_the_endpoint_down(monkeypatch, tmp_path):
    """It is hand-edited on a server, so assume it will be malformed one day."""
    f = tmp_path / 'servers.json'
    f.write_text('{not json', encoding='utf-8')
    monkeypatch.setattr(cs, 'SERVERS_FILE', str(f))
    assert len(cs.route_servers(_BaseUrlReq('https://splouch.example/'))['servers']) == 1


# ── Event names follow the reader, not the meet ───────────────────────────────

def test_an_event_name_parses_into_keys_not_words():
    """`T-04`: the parts name entries in a locale table, so one parse renders in
    every language the server ships. Words here would pin the meet's language."""
    p = state.parse_event_name('200 Backstroke Girls 12 & Under')
    assert p['stroke'] == 'backstroke'
    assert p['gender'] == 'girls'
    assert p['dist']   == '200'
    assert p['age']    == '< 12'      # a number needs no translation
    assert p['age_key'] == ''


@pytest.mark.parametrize('lang, expected', [
    ('en', '200 m Backstroke  —  Girls < 12'),
    ('fr', '200 m dos  —  Filles < 12'),
    ('es', '200 m espalda  —  Niñas < 12'),
])
def test_one_parse_composes_in_every_shipped_language(lang, expected):
    """The whole point: a spectator reading Spanish at a French meet gets a Spanish
    event name, without the client owning a parser or the frame being per-viewer."""
    parts = state.parse_event_name('200 Backstroke Girls 12 & Under')
    assert state.compose_event_name(parts, state.i18n_bundle(lang)['event_name']) == expected


@pytest.mark.parametrize('build', [state.i18n_bundle, cs._i18n_bundle])
def test_both_servers_serve_the_event_name_vocabulary(build):
    """A client cannot compose what it was not given, and the cloud must agree with
    the Pi or the same meet reads differently through the relay."""
    ev = build('fr')['event_name']
    assert ev['freestyle'] == 'libre'
    assert ev['girls'] == 'Filles'
    assert ev['unit'] and ev['separator']


def test_composing_survives_a_name_that_parses_into_nothing():
    """Hand-entered names exist. Falling back to the raw string beats a blank header."""
    parts = state.parse_event_name('Club Handicap Final')
    assert state.compose_event_name(parts, state.i18n_bundle('fr')['event_name']) \
        == 'Club Handicap Final'


def test_translate_event_name_still_composes_through_the_split():
    """The meet's own locale still resolves server-side, unchanged — the parts are
    additive, and `event_name` stays correct for a client that ignores them."""
    ev = state._locale_section('fr', 'event_name')
    assert state.translate_event_name('100 Free Women', ev) == '100 m libre  —  Femmes'
    assert state.translate_event_name('', ev) == ''
    assert state.translate_event_name('100 Free', None) == '100 Free'


# ── The operator panel's own words ─────────────────────────────────────────────
#
# `_panel_strings` merges English per key, so a missing translation degrades to an
# English word rather than a blank. That is the right failure, and it is also a silent
# one: the Appearance form and the Debug tab read as half-translated for a long time
# because their markup carried the words as literals and never looked anything up.

@pytest.mark.parametrize('code', ['fr', 'es'])
def test_every_panel_language_carries_every_english_key(code):
    """English-merge hides an omission behind an English word. This names it."""
    en, other = _panel('en'), _panel(code)
    for section in en:
        missing = sorted(set(en[section]) - set(other.get(section, {})))
        assert not missing, f'{code} [{section}] is missing: {missing}'


@pytest.mark.parametrize('code', ['fr', 'es'])
def test_no_panel_language_invents_a_key_english_lacks(code):
    """A key only a translation has is dead weight: nothing renders it."""
    en, other = _panel('en'), _panel(code)
    for section in other:
        extra = sorted(set(other[section]) - set(en.get(section, {})))
        assert not extra, f'{code} [{section}] has no English source for: {extra}'


# The words the cloud panel used to hard-code. `t.<key>`, not `t.get(key, 'English')`:
# the fallback form would have hidden a missing key just as effectively.
@pytest.mark.parametrize('key', [
    'picker_page', 'window_title', 'window_title_hint', 'picker_title',
    'picker_title_hint', 'logo', 'upload_image', 'remove', 'logo_above',
    'home_icon', 'home_icon_hint',
    'log_source_app', 'log_source_webhook', 'log_refresh', 'log_follow',
])
def test_the_cloud_appearance_and_debug_tabs_look_their_words_up(key):
    src = open(os.path.join(REPO, 'cloud', 'templates', 'admin.html'),
               encoding='utf-8').read()
    assert '{{ t.%s }}' % key in src, f'{key} is not rendered from the panel table'
    assert key in _panel('en')['cloud']


@pytest.mark.parametrize('literal', [
    'Window Title', 'Home Screen Icon', 'Image above title', 'Upload Image',
    'App container', 'Deploy webhook',
])
def test_the_cloud_panel_no_longer_hard_codes_those_words(literal):
    """The English still exists — in `panel/en.toml`, where a translator can reach it."""
    src = open(os.path.join(REPO, 'cloud', 'templates', 'admin.html'),
               encoding='utf-8').read()
    assert literal not in src, f'{literal!r} is back in the markup'


# ── Shared panel chrome ────────────────────────────────────────────────────────
#
# The Pi's Settings and the cloud's /admin render the same sidebar and theme switcher.
# The words used to exist twice — translated under `[settings]`, hard-coded in the
# cloud's markup — which is how one panel ended up in French and the other in English.

CHROME_KEYS = ['menu', 'toggle_menu', 'close', 'auto', 'refresh', 'loading',
               'theme', 'theme_light', 'theme_dark', 'theme_auto']


@pytest.mark.parametrize('key', CHROME_KEYS)
def test_the_shared_chrome_words_live_in_one_section(key):
    assert key in _panel('en')['chrome']
    for section in ('cloud', 'settings'):
        assert key not in _panel('en')[section], \
            f'{key} is back in [{section}] — two copies is what lets them drift'


@pytest.mark.parametrize('lang', ['en', 'fr', 'es'])
def test_both_panels_read_the_chrome_section(lang):
    """Each page's own section wins, so a page-specific override stays possible."""
    sys.path.insert(0, os.path.join(REPO, 'cloud'))
    import state
    pi = state.settings_strings(lang)
    for key in CHROME_KEYS:
        assert pi.get(key), f'Pi Settings has no {key} in {lang}'
    # The cloud's loader is the twin; check the same file resolves for it.
    merged = {**_panel(lang).get('chrome', {}), **_panel(lang).get('cloud', {})}
    for key in CHROME_KEYS:
        assert merged.get(key), f'cloud /admin has no {key} in {lang}'


@pytest.mark.parametrize('literal,template', [
    ('>Menu<',               'cloud/templates/admin.html'),
    ('Toggle menu',          'cloud/templates/admin.html'),
    ('Auto (follow system)', 'cloud/templates/admin.html'),
    ('aria-label="Close"',   'cloud/templates/admin.html'),
    ('>Current<',            'cloud/templates/admin.html'),
    ('>Loading…<',           'cloud/templates/admin.html'),
    ('>Menu<',               'server/templates/settings.html'),
    ('Toggle menu',          'server/templates/settings.html'),
    ('aria-label="Close"',   'server/templates/settings.html'),
])
def test_neither_panel_hard_codes_its_chrome(literal, template):
    src = open(os.path.join(REPO, template), encoding='utf-8').read()
    body = re.sub(r'<script.*?</script>', '', src, flags=re.S)
    assert literal not in body, f'{literal!r} is back in {template}'


# ── `t.get(key, 'English')` fallbacks ──────────────────────────────────────────
#
# The fallback is the right behaviour at runtime — a locale file that fails to load
# leaves English rather than a blank button. It is also a perfect hiding place: a key
# that was never added renders its default in every language and looks translated to
# anyone who reads English. Two of them sat there for a while, and one pair had drifted
# far enough apart to contradict each other about whether a restore deletes anything.

_FALLBACK_RE = re.compile(
    r"""t\.get\(\s*'([a-z0-9_]+)'\s*,\s*(['"])((?:[^'"\\]|\\.)*)\2""", re.S)


def _fallbacks(template):
    src = open(os.path.join(REPO, template), encoding='utf-8').read()
    return [(m.group(1), m.group(3).replace("\\'", "'")) for m in _FALLBACK_RE.finditer(src)]


CLOUD_ADMIN = 'cloud/templates/admin.html'


def test_the_cloud_admin_actually_uses_fallbacks():
    """If this stops being true the two tests below are silently vacuous."""
    assert len(_fallbacks(CLOUD_ADMIN)) > 20


@pytest.mark.parametrize('key,default', _fallbacks(CLOUD_ADMIN),
                         ids=[k for k, _ in _fallbacks(CLOUD_ADMIN)])
def test_every_fallback_has_a_real_key(key, default):
    """Otherwise the English default is what every language renders."""
    panel = _panel('en')
    assert key in {**panel['chrome'], **panel['cloud']}, \
        f'{key} has no entry, so admin.html shows {default!r} in every language'


@pytest.mark.parametrize('key,default', _fallbacks(CLOUD_ADMIN),
                         ids=[k for k, _ in _fallbacks(CLOUD_ADMIN)])
def test_every_fallback_matches_its_english(key, default):
    """The two are the same sentence or they are a bug.

    Whichever is stale, the page shows the table and the template reads like the
    other — which is how `restore_meets_note` came to promise that restoring meets
    replaces the lot, while the code has always merged and never removed anything.
    """
    panel = _panel('en')
    english = {**panel['chrome'], **panel['cloud']}[key]
    assert english == default, (
        f'{key}: en.toml says {english!r}, admin.html falls back to {default!r}')
