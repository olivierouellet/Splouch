"""Where an organizer is based, and the meet dates the picker keeps (`app.md` `P-01`).

What this file guards: a state/province is stored as its code, picked from the
country's own list — a spelling of one, as an older Pi sends it, is read as its
code, and anything else is dropped; the drop-downs offer each country's in the
reader's language; a meet whose dates are past is left off the picker, and the Pi's
Meet and Cloud tabs say so.
"""

import datetime
import os

import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

import cloud_registry
import relay
import state
from conftest import stub_url_for
from routes import settings as settings_route
from splouch_regions import clean_location, province_choices, province_code

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── State/province codes ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("country", "province", "stored"),
    [
        ("CA", "QC", ("CA", "QC")),
        ("ca", " quebec ", ("CA", "QC")),
        ("CA", "Colombie-Britannique", ("CA", "BC")),
        ("MX", "CDMX", ("MX", "CMX")),
        ("CA", "Bavaria", ("CA", "")),
        ("CA", "NY", ("CA", "")),
        ("FR", "Normandie", ("FR", "")),
        ("XX", "QC", ("", "")),
    ],
)
def test_a_province_is_stored_as_its_code_or_not_at_all(country, province, stored):
    assert clean_location(country, province) == stored


def test_an_unknown_province_is_shown_as_sent():
    """A value recorded before the drop-down, or for a country with no list."""
    assert province_code("CA", "Québec") == "QC"
    assert province_code("DE", "Bayern") == "Bayern"


def test_each_country_s_choices_are_named_in_the_reader_s_language():
    fr = dict(province_choices("fr")["CA"])
    en = dict(province_choices("en")["CA"])
    assert fr["BC"] == "Colombie-Britannique" and en["BC"] == "British Columbia"
    assert fr["QC"] == en["QC"] == "Québec"
    assert set(province_choices()) == {"CA", "US", "MX"}


# ── Meets already over ──────────────────────────────────────────────────────

NOW = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=datetime.UTC)


@pytest.mark.parametrize(
    ("meet_date", "offset", "ended"),
    [
        ("2026-10-05", 0, True),
        ("2026-10-06", 0, False),
        ("2026-10-07", 0, False),
        ("", 0, False),
        # Already the 7th in Auckland, still the 6th in Honolulu.
        ("2026-10-06", 13 * 60, True),
        ("2026-10-05", -10 * 60, True),
        ("2026-10-06", -10 * 60, False),
    ],
)
def test_a_meet_ends_after_its_last_day_at_the_pool(meet_date, offset, ended):
    meet = {"meet_date": meet_date, "utc_offset": offset}
    assert cloud_registry.ended(meet, NOW) is ended


@pytest.mark.parametrize(("days", "ended"), [(-1, True), (0, False), (3, False)])
def test_the_pi_knows_its_meet_is_over(monkeypatch, days, ended):
    date = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    monkeypatch.setattr(relay, "last_session_date", lambda: date)
    assert settings_route._meet_ended() is ended


def test_a_meet_without_dates_is_not_over(monkeypatch):
    monkeypatch.setattr(relay, "last_session_date", lambda: "")
    assert settings_route._meet_ended() is False


def _settings_page(**ctx):
    env = Environment(
        undefined=ChainableUndefined,
        loader=FileSystemLoader(
            [
                os.path.join(REPO, "server", "templates"),
                os.path.join(REPO, "shared", "templates"),
            ]
        ),
    )
    stub_url_for(env)
    return env.get_template("settings.html").render(
        t=state.settings_strings("en"),
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_color_defaults=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS,
        **ctx,
    )


def test_the_meet_and_cloud_tabs_say_a_past_meet_will_not_be_listed():
    warning = state.settings_strings("en")["meet_date_past"]
    assert _settings_page(meet_ended=True).count(warning) == 2
    assert warning not in _settings_page(meet_ended=False)


def test_the_cloud_tab_offers_the_provinces_with_the_stored_one_chosen():
    html = _settings_page(
        countries={"CA": "ca", "US": "us"},
        cloud_country="CA",
        cloud_province="QC",
        provinces=province_choices("en"),
    )
    assert '<select name="cloud_province" id="cloud_province"' in html
    assert '<option value="QC" data-country="CA" selected>Québec</option>' in html
    assert '<option value="NY" data-country="US" >New York</option>' in html
