"""`GET /i18n/{lang}` and `GET /locales` — strings for clients that render themselves.

The Qt display has always been *served* its status strings; the phone apps were
told to embed theirs, which put `shared/locales/` in three repos and made a fourth
language two store submissions (docs/mobile-features.md `T-05`). These endpoints
make every non-browser client fetch what the TV already fetches.

Two things carry the weight here. The English merge, per key, so a half-translated
locale falls back word by word instead of rendering a blank header; and the split
between the *bundled* table, which is identical for every meet and so belongs in a
cached response, and a Pi's *custom* wording, which exists on one box and so has to
ride in that meet's relay payload (`label_overrides`, docs/api.md §5.4).
"""
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
    assert build('es')['labels']['long']['place'] == 'POSICIÓN'


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
    monkeypatch.setattr(state, 'CUSTOM_LOCALE_FOLDER', str(tmp_path / 'none'))
    return tmp_path


def test_untranslated_keys_fall_back_to_english_word_by_word(half_translated):
    """A missing key must render an English word, never an empty header."""
    b = state.i18n_bundle('zz')
    assert b['mobile']['scoreboard'] == 'Tableau ZZ'   # translated
    assert b['mobile']['results']    == 'Results'      # merged from English
    assert b['labels']['short']['lane']  == 'ZL'
    assert b['labels']['short']['event'] == 'EV'


def test_a_style_a_locale_omits_falls_back_to_the_other(monkeypatch, tmp_path):
    """A custom file may define one form; an empty header is the worse answer."""
    (tmp_path / 'en.toml').write_text(
        '[labels]\nlane = { long = "LANE" }\n', encoding='utf-8')
    monkeypatch.setattr(state, 'LOCALES_DIR', str(tmp_path))
    monkeypatch.setattr(state, 'CUSTOM_LOCALE_FOLDER', str(tmp_path / 'none'))
    assert state.i18n_bundle('en')['labels']['short']['lane'] == 'LANE'


# ── A pool's own wording ──────────────────────────────────────────────────────

@pytest.fixture
def custom_pool(monkeypatch, tmp_path):
    """A Pi whose `scoreboard/locale/fr.toml` renames one label."""
    shipped, custom = tmp_path / 'shipped', tmp_path / 'custom'
    shipped.mkdir(); custom.mkdir()
    for code in ('en', 'fr'):
        (shipped / f'{code}.toml').write_text(io.open(
            os.path.join(REPO, 'shared', 'locales', f'{code}.toml'),
            encoding='utf-8').read(), encoding='utf-8')
    (custom / 'fr.toml').write_text(
        '[meta]\nname = "Français (club)"\n'
        '[labels]\nlane = { short = "CO", long = "CORRIDOR" }\n', encoding='utf-8')
    monkeypatch.setattr(state, 'LOCALES_DIR', str(shipped))
    monkeypatch.setattr(state, 'CUSTOM_LOCALE_FOLDER', str(custom))


def test_the_pi_serves_custom_wording_rather_than_diffing_it(custom_pool):
    """On the LAN there is nothing to reconcile — the Pi has the file."""
    b = state.i18n_bundle('fr')
    assert b['labels']['long']['lane']  == 'CORRIDOR'
    assert b['labels']['short']['lane'] == 'CO'
    assert b['labels']['long']['event'] == 'ÉPREUVE'   # untouched keys still shipped


def test_overrides_carry_only_what_the_pool_changed(custom_pool):
    """This is what rides in the relay payload, so it must not be the whole table."""
    assert state.label_overrides() == {
        'fr': {'short': {'lane': 'CO'}, 'long': {'lane': 'CORRIDOR'}}}


def test_a_custom_file_that_changes_nothing_overrides_nothing(monkeypatch, tmp_path):
    """Re-stating the shipped value is not an override, and must not be sent."""
    shipped, custom = tmp_path / 'shipped', tmp_path / 'custom'
    shipped.mkdir(); custom.mkdir()
    (shipped / 'fr.toml').write_text(
        '[labels]\nlane = { short = "CL", long = "COULOIR" }\n', encoding='utf-8')
    (custom / 'fr.toml').write_text(
        '[labels]\nlane = { short = "CL", long = "COULOIR" }\n', encoding='utf-8')
    monkeypatch.setattr(state, 'LOCALES_DIR', str(shipped))
    monkeypatch.setattr(state, 'CUSTOM_LOCALE_FOLDER', str(custom))
    assert state.label_overrides() == {}


def test_a_custom_language_counts_as_a_language(custom_pool):
    """A club adding a file adds a language, and the picker must offer it."""
    assert dict(state.available_locales())['fr'] == 'Français (club)'


def test_the_cloud_cannot_see_a_pools_files(custom_pool):
    """Which is the whole reason `label_overrides` exists (api.md §5.4)."""
    assert cs._i18n_bundle('fr')['labels']['long']['lane'] == 'COULOIR'


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


# ── The visitor's choice, per request ─────────────────────────────────────────
#
# `?lang=` and `?style=` are the whole mechanism: the picker stores a preference,
# the shell puts it on every page it opens, and each page resolves it against the
# meet's defaults here. What matters is that *no* choice is byte-for-byte what the
# operator configured — the override must not quietly restyle every board.


class _Q:
    """A request with only query params, which is all these read."""
    def __init__(self, **params):
        self.query_params = params


_MEET = {'settings': {
    'locale': 'fr', 'label_style': 'short',
    'labels': {'event': 'ÉP', 'lane': 'CL'},
    'label_overrides': {'fr': {'long': {'lane': 'CORRIDOR'}}},
}}


def test_no_choice_renders_exactly_what_the_operator_configured():
    """Not "the same words" — the same object. Nothing re-resolved, nothing lost."""
    assert cs._client_lang(_Q(), _MEET) == 'fr'
    assert cs._client_style(_Q(), _MEET) == 'short'
    assert cs._client_labels(_MEET, 'fr', 'short') is _MEET['settings']['labels']


def test_choosing_the_other_style_keeps_the_pools_own_wording():
    """The cloud cannot resolve a Pi's locale file, so it layers the diff it was
    sent (api.md §5.4). Without this a club's `CORRIDOR` reverts the moment a
    visitor touches the control."""
    assert cs._client_labels(_MEET, 'fr', 'long')['lane'] == 'CORRIDOR'
    assert cs._client_labels(_MEET, 'fr', 'long')['event'] == 'ÉPREUVE'


def test_choosing_another_language_gets_the_bundled_word():
    """Custom wording exists only in the language the club wrote it in. Falling back
    is right; inventing a translation of `CORRIDOR` would not be."""
    assert cs._client_labels(_MEET, 'es', 'long')['lane'] == 'CALLE'


def test_a_stale_link_falls_back_instead_of_breaking_the_board():
    assert cs._client_lang(_Q(lang='de'), _MEET) == 'fr'
    assert cs._client_lang(_Q(lang='../en'), _MEET) == 'fr'
    assert cs._client_style(_Q(style='tiny'), _MEET) == 'short'


def test_the_choice_is_honoured_when_it_is_available():
    assert cs._client_lang(_Q(lang='es'), _MEET) == 'es'
    assert cs._client_style(_Q(style='long'), _MEET) == 'long'


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
    ctx = web.client_strings(_Q(lang='fr', style='short'))
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


@pytest.mark.parametrize('doc, key', [('api.md', 'api'), ('mobile-features.md', 'mobile')])
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
