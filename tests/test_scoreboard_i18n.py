"""The TV display's status strings follow the server's Scoreboard language.

Settings → Display → Scoreboard language sets `locale`; `state.display_strings()`
reads the `[display]` section of that locale file and `/config` ships it. The
chicken-and-egg problem — those strings are needed *before* the first `/config`
lands — is solved by caching the last config on disk, covered here too.

Qt-free: the locale lookup and the cache are both plain Python.
"""

import json
import os

import pytest

from scoreboard.theme import DEFAULT_STRINGS, Config

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOCALES = os.path.join(REPO, "shared", "locales")
KEYS = sorted(DEFAULT_STRINGS)


def _display_section(code):
    import tomllib

    with open(os.path.join(LOCALES, f"{code}.toml"), "rb") as f:
        return tomllib.load(f).get("display", {})


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_every_locale_defines_every_display_string(code):
    """A missing key would render the raw key name on a TV in front of a crowd."""
    assert sorted(_display_section(code)) == KEYS


@pytest.mark.parametrize("code", ["fr", "es"])
def test_translations_are_not_left_as_english(code):
    """Guards against a locale file copied but never translated."""
    english = _display_section("en")
    assert _display_section(code)["waiting_server"] != english["waiting_server"]


def test_config_defaults_to_english_when_the_server_says_nothing():
    """First-ever boot: no cache, no server — must still read as a sentence."""
    cfg = Config()
    assert cfg.locale == "en"
    assert cfg.strings == DEFAULT_STRINGS


def test_server_strings_override_the_built_in_defaults():
    cfg = Config(
        {"locale": "fr", "display_strings": {"waiting_server": "En attente du serveur"}}
    )
    assert cfg.locale == "fr"
    assert cfg.strings["waiting_server"] == "En attente du serveur"
    # Keys the server did not send still resolve, rather than vanishing.
    assert cfg.strings["retrying"] == DEFAULT_STRINGS["retrying"]


def test_display_strings_are_english_merged(monkeypatch):
    """A partially translated locale falls back per key, not wholesale."""
    import state

    monkeypatch.setitem(state.settings, "locale", "fr")
    strings = state.display_strings()
    assert sorted(strings) == KEYS
    assert strings["waiting_server"].startswith("En attente")


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_config_endpoint_carries_locale_and_strings(monkeypatch, code):
    import state
    import web

    monkeypatch.setitem(state.settings, "locale", code)
    cfg = web.display_config()
    assert cfg["locale"] == code
    assert sorted(cfg["display_strings"]) == KEYS


# ── The test-session badge's text ──────────────────────────────────────────────
# The badge itself is drawn in test_scoreboard_test_mode.py, which needs Qt.


def test_the_badge_text_is_a_display_string():
    assert "test_session" in DEFAULT_STRINGS


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_every_locale_translates_the_badge(code):
    import state

    strings = state.display_strings(code)
    assert strings.get("test_session"), f"{code} has no test_session"


def test_the_server_ships_the_badge_text_in_config(monkeypatch):
    import state
    import web

    monkeypatch.setitem(state.settings, "locale", "fr")
    cfg = Config(web.display_config())
    assert cfg.strings["test_session"].startswith("⚠")
    assert "TEST" in cfg.strings["test_session"].upper()


# ── Config cache (what makes the first screen translated) ──────────────────────


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    import importlib

    import scoreboard.cache as cache_mod

    importlib.reload(cache_mod)  # re-read XDG_CACHE_HOME at import time
    yield cache_mod
    importlib.reload(cache_mod)


def test_cache_round_trips_the_config(cache):
    raw = {
        "locale": "fr",
        "num_lanes": 6,
        "display_strings": {"waiting_server": "En attente du serveur"},
    }
    assert cache.save_cached_config(raw) is True
    assert cache.load_cached_config() == raw
    assert Config(cache.load_cached_config()).locale == "fr"


def test_identical_save_is_skipped(cache):
    """Every reconnect re-fetches /config; rewriting it each time is SD wear."""
    raw = {"locale": "es", "num_lanes": 8}
    assert cache.save_cached_config(raw) is True
    assert cache.save_cached_config(raw) is False
    assert cache.save_cached_config({**raw, "num_lanes": 6}) is True


def test_missing_cache_reads_as_absent(cache):
    assert cache.load_cached_config() is None


def test_corrupt_cache_reads_as_absent(cache):
    """A power cut mid-write must not poison every later boot."""
    cache.save_cached_config({"locale": "fr"})
    path = os.path.join(
        os.environ["XDG_CACHE_HOME"], "splouch", "scoreboard-config.json"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"locale": "fr"')  # truncated
    assert cache.load_cached_config() is None
    assert Config(cache.load_cached_config() or {}).strings == DEFAULT_STRINGS


def test_non_dict_cache_reads_as_absent(cache):
    path = os.path.join(
        os.environ["XDG_CACHE_HOME"], "splouch", "scoreboard-config.json"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(["not", "a", "config"], f)
    assert cache.load_cached_config() is None
