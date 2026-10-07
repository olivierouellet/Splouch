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
def test_a_retained_meet_ends_after_its_last_day_at_the_pool(meet_date, offset, ended):
    meet = {"meet_date": meet_date, "utc_offset": offset, "live": False}
    assert cloud_registry.ended(meet, NOW) is ended


def _live(end_hours_ago, **kw):
    """A live meet whose last day was yesterday at the pool (UTC), ended so long ago."""
    return {
        "meet_date": "2026-10-05",
        "utc_offset": 0,
        "live": True,
        "meet_end": NOW - datetime.timedelta(hours=end_hours_ago),
        **kw,
    }


def test_a_live_meet_stays_listed_24_hours_past_its_end_without_asking():
    assert cloud_registry.ended(_live(23), NOW) is False
    assert cloud_registry.ended(_live(24), NOW) is True


def test_a_live_meet_without_an_end_time_stays_24_hours_past_its_last_day():
    """From a Pi older than `meet_end`: the end of its day, 00:00 on the 6th."""
    meet = _live(0, meet_end=None)
    assert cloud_registry.ended(meet, NOW) is False
    assert cloud_registry.ended(meet, NOW + datetime.timedelta(hours=12)) is True


def test_a_kept_live_meet_stays_until_its_keep_runs_out():
    keep = NOW + datetime.timedelta(hours=1)
    assert cloud_registry.ended(_live(30, keep_listed_until=keep), NOW) is False
    assert cloud_registry.ended(_live(30, keep_listed_until=keep), keep) is True


def test_a_keep_is_nothing_once_its_pi_has_left():
    keep = NOW + datetime.timedelta(hours=1)
    meet = {**_live(1, keep_listed_until=keep), "live": False}
    assert cloud_registry.ended(meet, NOW) is True


def test_the_cloud_holds_what_the_pi_sends_to_its_last_day():
    hold = cloud_registry._within_last_day
    asked = "2026-10-20T00:00:00+00:00"
    # Midnight after the 5th in Montréal (UTC-4) is 04:00 UTC on the 6th.
    assert hold(asked, "2026-10-05", -240, 72) == datetime.datetime(
        2026, 10, 9, 4, 0, tzinfo=datetime.UTC
    )
    assert hold(asked, "2026-10-05", -240, 0) == datetime.datetime(
        2026, 10, 6, 4, 0, tzinfo=datetime.UTC
    )
    soon = "2026-10-05T22:00:00+00:00"
    assert hold(soon, "2026-10-05", -240, 0).isoformat() == soon
    assert hold(None, "2026-10-05", 0, 72) is None
    assert hold("2026-10-07T12:00:00", "2026-10-05", 0, 72) is None
    assert hold(soon, "", 0, 72) is None


def _sessions(monkeypatch, *sessions):
    """Load a meet with these `(date, endtime)` sessions, and none of its own uid."""
    monkeypatch.setattr(
        state.meet,
        "meet_info",
        {"name": "Coupe", "sessions": [{"date": d, "endtime": e} for d, e in sessions]},
    )
    monkeypatch.setitem(state.settings, "cloud_keep_listed", "")


def _day(n):
    return (datetime.date.today() + datetime.timedelta(days=n)).isoformat()


def _at(days, hour):
    """Local time, `days` from today at `hour`."""
    return datetime.datetime.combine(
        datetime.date.today() + datetime.timedelta(days=days), datetime.time(hour)
    )


@pytest.mark.parametrize(
    ("days", "now", "over"),
    [
        (-1, _at(0, 17), False),  # 23 hours past its end
        (-1, _at(0, 18), True),  # 24
        (0, _at(0, 23), False),  # its last day, however late
        (-2, _at(0, 23), True),
        (3, _at(0, 12), False),
    ],
)
def test_the_pi_knows_its_meet_is_off_the_picker(monkeypatch, days, now, over):
    """Listed while connected until 24 hours past its 18:00 end."""
    _sessions(monkeypatch, (_day(days), "18:00"))
    assert relay.meet_over(now) is over


def test_a_meet_without_dates_is_not_over(monkeypatch):
    _sessions(monkeypatch)
    assert relay.meet_over() is False
    assert relay.meet_end() is None and relay.can_keep_listed() is False


def test_the_meet_ends_with_its_last_session_or_its_last_day(monkeypatch):
    _sessions(
        monkeypatch,
        ("2026-10-04", "21:00"),
        ("2026-10-05", "12:30"),
        ("2026-10-05", "18:00"),
    )
    assert relay.meet_end() == datetime.datetime(2026, 10, 5, 18, 0)
    _sessions(monkeypatch, ("2026-10-05", ""))
    assert relay.meet_end() == datetime.datetime(2026, 10, 6, 0, 0)


def test_a_past_meet_can_be_kept_listed_for_72_hours_after_its_end(monkeypatch):
    _sessions(monkeypatch, (_day(-2), "18:00"))
    end = _at(-2, 18)
    assert relay.keep_listed_deadline() == end + datetime.timedelta(hours=72)
    assert not relay.can_keep_listed(now=end + datetime.timedelta(hours=23)), (
        "still listed on its own"
    )
    assert relay.can_keep_listed(now=end + datetime.timedelta(hours=71))
    assert not relay.can_keep_listed(now=end + datetime.timedelta(hours=72))


def test_the_end_and_the_keep_are_sent_to_the_cloud(monkeypatch):
    _sessions(monkeypatch, (_day(-2), "18:00"))
    sent = datetime.datetime.fromisoformat(relay._aware_iso(relay.meet_end()))
    assert sent == _at(-2, 18).astimezone(datetime.UTC)
    assert relay._aware_iso(relay.keep_listed_until()) is None
    monkeypatch.setitem(state.settings, "cloud_keep_listed", state.meet_uid())
    deadline = relay.keep_listed_deadline()
    assert relay.keep_listed_until() == deadline
    assert relay.can_keep_listed() is False, "already kept: no second offer"
    assert relay.keep_listed_until(now=deadline) is None


def test_keeping_one_meet_listed_does_not_keep_the_next(monkeypatch):
    _sessions(monkeypatch, (_day(-2), "18:00"))
    monkeypatch.setitem(state.settings, "cloud_keep_listed", state.meet_uid())
    monkeypatch.setattr(
        state.meet, "meet_info", {"name": "Other", "sessions": [{"date": _day(-2)}]}
    )
    assert relay.keep_listed_until() is None and relay.can_keep_listed()


def test_the_route_refuses_a_meet_not_over(monkeypatch):
    from routes.meet import route_meet_keep_listed

    _sessions(monkeypatch, (_day(0), "18:00"))
    monkeypatch.setattr(state, "save_settings", lambda: None)
    monkeypatch.setattr(relay, "update_metadata", lambda: None)
    assert route_meet_keep_listed().status_code == 409
    _sessions(monkeypatch, (_day(-2), "18:00"))
    assert route_meet_keep_listed() == {"ok": True}
    assert state.settings["cloud_keep_listed"] == state.meet_uid()


# ── The cloud's side ────────────────────────────────────────────────────────


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
    t = state.settings_strings("en")
    page = _settings_page(meet_ended=True)
    assert page.count(t["meet_date_past"]) == 2
    assert 'data-hold-fn="keepListedHeld"' not in page, "past the 72 hours: no offer"
    assert t["meet_date_past"] not in _settings_page(meet_ended=False)


def test_within_72_hours_the_tabs_offer_a_held_keep_listing():
    page = _settings_page(
        meet_ended=True, can_keep_listed=True, keep_listed_deadline="2026-10-08 18:00"
    )
    assert page.count('data-hold-fn="keepListedHeld"') == 2
    assert "until 2026-10-08 18:00" in page


def test_a_kept_meet_says_until_when():
    page = _settings_page(meet_ended=True, keep_listed_until="2026-10-08 18:00")
    assert page.count("keeps listing it until 2026-10-08 18:00") == 2
    assert "keepListedHeld" not in page.split("<script")[0]


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
