"""What Meet Setup says about a meet file once it is read: a summary line, what
the parser left out, and why a file it refused was refused."""

import io
import os
import zipfile

import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

import state
from conftest import stub_url_for
from meet_data import meet_summary, meet_warning_groups
from meet_parsers.lenex_parser import MeetFileError, load_lenex

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _lxf(events, athletes="", relays=""):
    """A Splash-shaped file: entries under the ATHLETE and the RELAY."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<LENEX version="3.0">
  <MEETS><MEET name="Warn Meet" course="SCM">
    <SESSIONS><SESSION number="1" date="2026-11-07"><EVENTS>{events}</EVENTS>
    </SESSION></SESSIONS>
    <CLUBS><CLUB name="Aqua Club" code="AQUA">
      <ATHLETES>{athletes}</ATHLETES><RELAYS>{relays}</RELAYS>
    </CLUB></CLUBS>
  </MEET></MEETS>
</LENEX>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("meet.lef", xml.encode("utf-8"))
    buf.seek(0)
    return buf


def _event(eid, number, heats, extra=""):
    heat_xml = "".join(
        f'<HEAT heatid="{hid}" number="{num}" status="SEEDED"/>' for hid, num in heats
    )
    return (
        f'<EVENT eventid="{eid}" number="{number}" {extra}>'
        '<SWIMSTYLE distance="50" relaycount="1" stroke="FREE"/>'
        f"<HEATS>{heat_xml}</HEATS></EVENT>"
    )


def _athlete(aid, first, entries):
    return (
        f'<ATHLETE athleteid="{aid}" lastname="Swim" firstname="{first}">'
        f"<ENTRIES>{entries}</ENTRIES></ATHLETE>"
    )


def _keys(data):
    return [w["key"] for w in data.warnings]


def test_a_clean_file_has_no_warnings():
    data = load_lenex(
        _lxf(
            _event(1, 1, [(10, 1)]),
            _athlete(1, "Ann", '<ENTRY eventid="1" heatid="10" lane="3"/>')
            # Entered, not seeded yet: no heat, which is no error.
            + _athlete(2, "Bea", '<ENTRY eventid="1"/>'),
        )
    )
    assert data.warnings == ()


def test_an_event_and_a_heat_without_a_number():
    data = load_lenex(
        _lxf(
            _event(1, "", [(10, 1)])
            + _event(2, 2, [(20, 1), (21, "x")], 'round="FIN"')
            + _event(3, 2, [(30, 1)], 'round="PRE"')
        )
    )
    assert data.warnings == (
        {"key": "event_no_number", "eventid": "1"},
        {"key": "heat_no_number", "event": 2, "round": "final"},
    )


def test_entries_left_out_say_where_and_who():
    data = load_lenex(
        _lxf(
            _event(1, 1, [(10, 1)]),
            _athlete(1, "Ann", '<ENTRY eventid="1" heatid="99" lane="3"/>')
            + _athlete(2, "Bea", '<ENTRY eventid="1" heatid="10"/>')
            + _athlete(3, "Cat", '<ENTRY eventid="1" heatid="10" lane="4"/>')
            + _athlete(4, "Dee", '<ENTRY eventid="1" heatid="10" lane="4"/>')
            # Withdrawn: no lane is expected, so nothing to say.
            + _athlete(5, "Eve", '<ENTRY eventid="1" heatid="10" status="WDR"/>')
            # An event the file does not have: no number, said once, above.
            + _athlete(6, "Fay", '<ENTRY eventid="77" heatid="1" lane="5"/>'),
        )
    )
    assert data.warnings == (
        {"key": "entry_no_heat", "event": 1, "round": "", "name": "Ann Swim"},
        {
            "key": "entry_no_lane",
            "event": 1,
            "round": "",
            "heat": 1,
            "name": "Bea Swim",
        },
        {
            "key": "lane_twice",
            "event": 1,
            "round": "",
            "heat": 1,
            "lane": 4,
            "name": "Dee Swim",
            "other": "Cat Swim",
        },
    )
    assert data.start_list[1][1][4]["name"] == "Dee Swim"


def test_the_same_entry_written_twice_is_no_error():
    """Splash wrote one swim-off entry twice (Champ2024, event 9)."""
    entry = '<ENTRY eventid="1" heatid="10" lane="5"/>'
    data = load_lenex(_lxf(_event(1, 1, [(10, 1)]), _athlete(1, "Ann", entry * 2)))
    assert data.warnings == ()


def test_a_relay_entry_left_out_is_named_by_its_team():
    relay = (
        '<RELAY number="1" name="Aqua A"><ENTRIES>'
        '<ENTRY eventid="1" heatid="99" lane="2"/></ENTRIES></RELAY>'
    )
    data = load_lenex(_lxf(_event(1, 1, [(10, 1)]), relays=relay))
    assert data.warnings == (
        {"key": "entry_no_heat", "event": 1, "round": "", "name": "Aqua A"},
    )


def test_extra_heats_without_their_timed_final():
    data = load_lenex(
        _lxf(_event(5, 1, [(10, 1)], 'round="EXTRAHEATS" preveventid="404"'))
    )
    assert _keys(data) == ["extra_heats_alone"]


def test_unknown_codes_are_not_warned_about():
    """What exporters write as a matter of course is not news to the operator."""
    data = load_lenex(_lxf(_event(1, 1, [(10, 1)], 'round="XYZ" status="WHATEVER"')))
    assert data.warnings == ()


# ── Refused files ──────────────────────────────────────────────────────────────


def test_a_file_that_is_not_xml_says_where_it_breaks(tmp_path):
    path = tmp_path / "broken.lxf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("meet.lef", b"<LENEX>\n  <MEETS>\n</LENEX>")
    with pytest.raises(MeetFileError, match="line 3, column"):
        load_lenex(str(path))


@pytest.fixture
def pi(monkeypatch, tmp_path):
    import routes.settings as settings_routes

    monkeypatch.setattr(state, "CONSOLE_TIMES_DIR", str(tmp_path))
    monkeypatch.setattr(state, "save_settings", lambda: None)
    for name in ("send_event_info", "meet_changed", "announce_schedule"):
        monkeypatch.setattr(settings_routes, name, lambda: None)
    monkeypatch.setattr(settings_routes.relay, "update_metadata", lambda: None)
    monkeypatch.setattr(settings_routes.relay, "send_schedule", lambda: None)
    monkeypatch.setattr(settings_routes.bus, "emit", lambda *a, **k: None)
    monkeypatch.setattr(settings_routes, "_render_home_icon", lambda *a: None)
    monkeypatch.setattr(state, "apply_meet_profile", lambda uid: None)
    yield settings_routes
    state.clear_meet()


def test_a_refused_file_says_why_on_the_page(pi, tmp_path):
    path = tmp_path / "Broken.lxf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("notes.txt", b"hello")
        z.writestr("other.txt", b"hello")
    assert pi._load_meet_file(str(path)) == (
        "Could not read Broken.lxf: No Lenex XML (.lef) found inside the .lxf; "
        "it contains: notes.txt, other.txt"
    )


def test_a_surprise_goes_to_the_log(pi, tmp_path, monkeypatch, capsys):
    def boom(path):
        raise KeyError("a bug")

    monkeypatch.setattr(pi, "load_structured", boom)
    path = tmp_path / "Meet.lxf"
    path.write_bytes(b"")
    assert pi._load_meet_file(str(path)) == "Could not read Meet.lxf — see the log"
    assert "a bug" in capsys.readouterr().err


# ── Meet Setup ─────────────────────────────────────────────────────────────────


def _warned_meet():
    lanes = "".join(
        _athlete(i, f"S{i}", f'<ENTRY eventid="1" heatid="99" lane="{i}"/>')
        for i in range(1, 6)
    )
    return load_lenex(
        _lxf(
            _event(1, 1, [(10, 1), (11, 2)], 'round="FIN"') + _event(2, 2, [(20, 1)]),
            _athlete(9, "Ann", '<ENTRY eventid="2" heatid="20" lane="3"/>') + lanes,
        )
    )


def test_the_summary_counts_events_heats_and_entries(pi):
    assert meet_summary() is None
    state.set_lenex(_warned_meet())
    assert meet_summary() == {"events": 2, "heats": 3, "entries": 1}


def test_warnings_are_grouped_with_a_few_examples(pi):
    state.set_lenex(_warned_meet())
    t = state.settings_strings("en")
    assert meet_warning_groups(t) == [
        {
            "text": "Entries whose heat is not in the file (5)",
            "examples": [
                "event 1 Final, S1 Swim",
                "event 1 Final, S2 Swim",
                "event 1 Final, S3 Swim",
            ],
            "more": 2,
        }
    ]


def test_every_panel_language_words_every_warning(pi):
    """A key the parser emits with no headline would vanish from the page."""
    keys = {
        "event_no_number",
        "heat_no_number",
        "entry_no_heat",
        "entry_no_lane",
        "lane_twice",
        "extra_heats_alone",
    }
    values = {"eventid": "1", "event": 1, "round": "", "heat": 1, "lane": 1,
              "name": "A", "other": "B", "n": 2}  # fmt: skip
    for lang, _ in state.list_locales():
        t = state.settings_strings(lang)
        for key in keys:
            assert "{n}" in t[f"meet_warn_{key}"], (lang, key)
            t[f"meet_warn_{key}_eg"].format(**values)


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


def test_meet_setup_shows_the_summary_and_the_warnings(pi):
    state.set_lenex(_warned_meet())
    t = state.settings_strings("en")
    page = _settings_page(
        active_meet_file="Warn.lxf",
        meet_summary=meet_summary(),
        meet_warnings=meet_warning_groups(t),
    )
    assert "Read: 2 events · 3 heats · 1 entries" in page
    assert 'id="meet-warnings"' in page
    assert "event 1 Final, S3 Swim" in page and " (and 2 more)" in page

    clean = _settings_page(active_meet_file="Warn.lxf", meet_warnings=[])
    assert 'id="meet-warnings"' not in clean
