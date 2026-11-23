import io
import zipfile

import pytest

from meet_parsers.lenex_parser import LenexData, load_lenex

# ── Helpers ────────────────────────────────────────────────────────────────────


def make_lxf(xml: str) -> io.BytesIO:
    """Wrap an XML string in an in-memory .lxf zip file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("meet.lef", xml.encode("utf-8"))
    buf.seek(0)
    return buf


CLUBS = """
  <CLUBS>
    <CLUB name="Aqua Club" shortname="AQUA">
      <ATHLETES>
        <ATHLETE athleteid="1" lastname="Smith"  firstname="Jane"/>
        <ATHLETE athleteid="2" lastname="Tremblay" firstname="Marc"/>
      </ATHLETES>
    </CLUB>
    <CLUB name="Wave Club" shortname="WAVE">
      <ATHLETES>
        <ATHLETE athleteid="3" lastname="Doe" firstname="Mary"/>
      </ATHLETES>
    </CLUB>
  </CLUBS>
"""


def lenex2_xml(events_xml: str) -> str:
    """Lenex 2.0 — no namespace."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<LENEX version="2.0">
  <MEETS><MEET name="Test Meet">
    <SESSIONS><SESSION>
      <EVENTS>{events_xml}</EVENTS>
    </SESSION></SESSIONS>
    {CLUBS}
  </MEET></MEETS>
</LENEX>"""


def lenex3_xml(events_xml: str) -> str:
    """Lenex 3.0 — with namespace."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<LENEX xmlns="lenex/3.0" version="3.0">
  <MEETS><MEET name="Test Meet">
    <SESSIONS><SESSION>
      <EVENTS>{events_xml}</EVENTS>
    </SESSION></SESSIONS>
    {CLUBS}
  </MEET></MEETS>
</LENEX>"""


EVENTS_XML = """
  <EVENT number="1" prename="Girls 10U" name="100 Freestyle" gender="F">
    <HEATS>
      <HEAT number="1">
        <ENTRIES>
          <ENTRY athleteid="1" lane="3"/>
          <ENTRY athleteid="3" lane="5"/>
        </ENTRIES>
      </HEAT>
      <HEAT number="2">
        <ENTRIES>
          <ENTRY athleteid="2" lane="4"/>
        </ENTRIES>
      </HEAT>
    </HEATS>
  </EVENT>
  <EVENT number="2" gender="M">
    <SWIMSTYLE distance="200" stroke="BACKSTROKE"/>
    <HEATS>
      <HEAT number="1">
        <ENTRIES>
          <ENTRY athleteid="2" lane="6"/>
        </ENTRIES>
      </HEAT>
    </HEATS>
  </EVENT>
"""

# ── Tests ──────────────────────────────────────────────────────────────────────


class TestLenexParser:
    def test_returns_lenex_data_namedtuple(self):
        data = load_lenex(make_lxf(lenex2_xml(EVENTS_XML)))
        assert isinstance(data, LenexData)
        assert hasattr(data, "event_names")
        assert hasattr(data, "start_list")

    def test_event_name_from_prename_and_name(self):
        data = load_lenex(make_lxf(lenex2_xml(EVENTS_XML)))
        assert data.event_names[1] == "Girls 10U 100 Freestyle"

    def test_event_name_swimstyle_fallback(self):
        data = load_lenex(make_lxf(lenex2_xml(EVENTS_XML)))
        assert data.event_names[2] == "Men's 200 Backstroke"

    def test_swimmer_name(self):
        data = load_lenex(make_lxf(lenex2_xml(EVENTS_XML)))
        assert data.start_list[1][1][3]["name"] == "Jane Smith"

    def test_swimmer_club(self):
        data = load_lenex(make_lxf(lenex2_xml(EVENTS_XML)))
        assert data.start_list[1][1][3]["club"] == "AQUA"
        assert data.start_list[1][1][5]["club"] == "WAVE"

    def test_multiple_heats(self):
        data = load_lenex(make_lxf(lenex2_xml(EVENTS_XML)))
        assert data.start_list[1][2][4]["name"] == "Marc Tremblay"

    def test_lenex3_namespace_same_results(self):
        data2 = load_lenex(make_lxf(lenex2_xml(EVENTS_XML)))
        data3 = load_lenex(make_lxf(lenex3_xml(EVENTS_XML)))
        assert data2.event_names == data3.event_names
        assert data2.start_list == data3.start_list

    def test_missing_lane_not_in_start_list(self):
        data = load_lenex(make_lxf(lenex2_xml(EVENTS_XML)))
        assert 9 not in data.start_list[1][1]

    def test_every_round_code_maps_to_a_key(self):
        """`S-01`: the swim-offs share one word; an event with no `round` or an
        unknown code stays out of the map."""
        codes = ["FIN", "sem", "QUA", "PRE", "FHT", "TIM", "SOP", "SOQ", "SOS", "XYZ"]
        events = (
            "".join(
                f'<EVENT number="{n}" name="50 Free" round="{code}"><HEATS/></EVENT>'
                for n, code in enumerate(codes, 1)
            )
            + '<EVENT number="11" name="50 Free"><HEATS/></EVENT>'
        )
        for wrap in (lenex2_xml, lenex3_xml):
            data = load_lenex(make_lxf(wrap(events)))
            assert data.event_rounds == {
                1: "final",
                2: "semifinal",
                3: "quarterfinal",
                4: "prelims",
                5: "fastest_heat",
                6: "timed_final",
                7: "swimoff",
                8: "swimoff",
                9: "swimoff",
            }

    def test_missing_event_not_in_event_names(self):
        data = load_lenex(make_lxf(lenex2_xml(EVENTS_XML)))
        assert 99 not in data.event_names

    def test_structure_b_event_level_entries_with_heatid(self):
        """Real Lenex 3.0 files from timing software put ENTRIES under EVENT, not HEAT."""
        events_xml = """
          <EVENT number="1" name="100 Freestyle">
            <HEATS>
              <HEAT number="1" heatid="H1"/>
              <HEAT number="2" heatid="H2"/>
            </HEATS>
            <ENTRIES>
              <ENTRY athleteid="1" heatid="H1" lane="3" entrytime="58.20"/>
              <ENTRY athleteid="2" heatid="H2" lane="5" entrytime="57.44"/>
            </ENTRIES>
          </EVENT>
        """
        data = load_lenex(make_lxf(lenex2_xml(events_xml)))
        assert data.start_list[1][1][3]["name"] == "Jane Smith"
        assert data.start_list[1][1][3]["club"] == "AQUA"
        assert data.start_list[1][1][3]["seed_time"] == "58.20"
        assert data.start_list[1][2][5]["name"] == "Marc Tremblay"


class TestArchiveVariants:
    """The inner Lenex XML is found regardless of how the archive names it."""

    @staticmethod
    def _lxf(inner_name):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(inner_name, lenex2_xml(EVENTS_XML).encode("utf-8"))
        buf.seek(0)
        return buf

    def test_uppercase_lef_extension(self):
        data = load_lenex(self._lxf("MEET.LEF"))
        assert data.event_names[1] == "Girls 10U 100 Freestyle"

    def test_xml_extension_fallback(self):
        data = load_lenex(self._lxf("meet.xml"))
        assert data.event_names[1] == "Girls 10U 100 Freestyle"

    def test_single_unrecognized_member(self):
        data = load_lenex(self._lxf("lenex"))
        assert data.event_names[1] == "Girls 10U 100 Freestyle"

    def test_no_lenex_member_raises_clear_error(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", b"not lenex")
            z.writestr("notes.md", b"still not lenex")
        buf.seek(0)
        with pytest.raises(ValueError, match="No Lenex XML"):
            load_lenex(buf)


# ── Relay reserves ─────────────────────────────────────────────────────────────

RELAY_CLUBS = """
  <CLUBS>
    <CLUB name="Aqua Club" shortname="AQUA">
      <ATHLETES>
        <ATHLETE athleteid="1" lastname="A" firstname="Ann"/>
        <ATHLETE athleteid="2" lastname="B" firstname="Bea"/>
        <ATHLETE athleteid="3" lastname="C" firstname="Cat"/>
        <ATHLETE athleteid="4" lastname="D" firstname="Dee"/>
        <ATHLETE athleteid="5" lastname="E" firstname="Eve"/>
        <ATHLETE athleteid="6" lastname="F" firstname="Fay"/>
      </ATHLETES>
      <RELAYS><RELAY relayid="r1" name="Aqua A"/></RELAYS>
    </CLUB>
  </CLUBS>
"""


def relay_xml(relaycount, positions):
    style = f' relaycount="{relaycount}"' if relaycount else ""
    rps = "".join(
        f'<RELAYPOSITION athleteid="{aid}" number="{num}"/>' for aid, num in positions
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<LENEX xmlns="lenex/3.0" version="3.0">
  <MEETS><MEET name="Test Meet">
    <SESSIONS><SESSION><EVENTS>
      <EVENT number="7" gender="F">
        <SWIMSTYLE distance="50" stroke="FREE"{style}/>
        <HEATS><HEAT number="1"><ENTRIES>
          <ENTRY lane="4"><RELAY relayid="r1" name="Aqua A"/>
            <RELAYPOSITIONS>{rps}</RELAYPOSITIONS>
          </ENTRY>
        </ENTRIES></HEAT></HEATS>
      </EVENT>
    </EVENTS></SESSION></SESSIONS>
    {RELAY_CLUBS}
  </MEET></MEETS>
</LENEX>"""


def relay_firsts(relaycount, positions):
    data = load_lenex(make_lxf(relay_xml(relaycount, positions)))
    return [s["first"] for s in data.start_list[7][1][4]["swimmers"]]


def test_relay_legs_are_kept_in_order():
    legs = [("4", 4), ("2", 2), ("1", 1), ("3", 3)]
    assert relay_firsts(4, legs) == ["Ann", "Bea", "Cat", "Dee"]


def test_a_relay_reserve_past_the_last_leg_is_left_out():
    positions = [("1", 1), ("2", 2), ("3", 3), ("4", 4), ("5", 5), ("6", 6)]
    assert relay_firsts(4, positions) == ["Ann", "Bea", "Cat", "Dee"]


def test_a_relay_reserve_numbered_below_1_is_left_out():
    positions = [("5", -1), ("6", 0), ("1", 1), ("2", 2), ("3", 3), ("4", 4)]
    assert relay_firsts(4, positions) == ["Ann", "Bea", "Cat", "Dee"]


def test_a_relay_position_that_is_not_a_number_is_left_out():
    positions = [("5", "R"), ("1", 1), ("2", 2), ("3", 3), ("4", 4)]
    assert relay_firsts(4, positions) == ["Ann", "Bea", "Cat", "Dee"]


def test_without_a_relaycount_every_leg_from_1_up_is_kept():
    positions = [("6", -1), ("1", 1), ("2", 2), ("3", 3), ("4", 4)]
    assert relay_firsts(None, positions) == ["Ann", "Bea", "Cat", "Dee"]


# ── Against the Lenex 3.0 spec ─────────────────────────────────────────────────


def _splash(events, clubs="", meet_attrs='course="SCM"', meet_children=""):
    """A Splash-shaped file: entries and results under the ATHLETE / RELAY."""
    return make_lxf(f"""<?xml version="1.0" encoding="UTF-8"?>
<LENEX version="3.0">
  <MEETS><MEET name="Spec Meet" {meet_attrs}>
    {meet_children}
    <SESSIONS><SESSION number="1" date="2026-11-07">
      <EVENTS>{events}</EVENTS>
    </SESSION></SESSIONS>
    <CLUBS>{clubs}</CLUBS>
  </MEET></MEETS>
</LENEX>""")


def _event(style, number=1, extra=""):
    return (
        f'<EVENT eventid="{number}" number="{number}" gender="F" {extra}>'
        f'<SWIMSTYLE {style}/><HEATS><HEAT heatid="{number}" number="1"/></HEATS>'
        "</EVENT>"
    )


@pytest.mark.parametrize(
    ("style", "name"),
    [
        ('distance="50" relaycount="4" stroke="FREE"', "Women's 4x50 Freestyle Relay"),
        ('distance="100" relaycount="4" stroke="MEDLEY"', "Women's 4x100 Medley Relay"),
        (
            'distance="75" relaycount="1" stroke="UNKNOWN" name="5 x 75m Breast one arm"',
            "Women's 5 x 75m Breast one arm",
        ),
        (
            'distance="25" relaycount="1" stroke="FREE" technique="KICK"',
            "Women's 25 Freestyle Kick",
        ),
        ('distance="200" relaycount="1" stroke="BACK"', "Women's 200 Backstroke"),
    ],
)
def test_an_event_name_from_its_swimstyle(style, name):
    assert load_lenex(_splash(_event(style))).event_names == {1: name}


@pytest.mark.parametrize(
    ("groups", "age"),
    [
        # A timed final scored by age groups: the whole span.
        ([("-1", "7"), ("8", "8"), ("9", "9"), ("10", "10")], "≤10"),
        ([("11", "12"), ("13", "14"), ("15", "16"), ("17", "-1")], "11+"),
        ([("11", "-1")], "11+"),
        ([("13", "14")], "13-14"),
        ([("8", "8")], "8"),
        # Open both ways, or no group: no age.
        ([("-1", "-1")], ""),
        ([], ""),
    ],
)
def test_an_event_name_from_its_swimstyle_carries_its_ages(groups, age):
    """Splash writes no name, only the SWIMSTYLE and the AGEGROUPs: the ages
    reach the name, so the board, /meet and the apps show them."""
    import state

    agegroups = "".join(
        f'<AGEGROUP agegroupid="{i}" agemin="{lo}" agemax="{hi}"/>'
        for i, (lo, hi) in enumerate(groups, start=1)
    )
    event = (
        '<EVENT eventid="1" number="1" gender="M">'
        '<SWIMSTYLE distance="100" relaycount="1" stroke="MEDLEY"/>'
        f"<AGEGROUPS>{agegroups}</AGEGROUPS>"
        '<HEATS><HEAT heatid="1" number="1"/></HEATS></EVENT>'
    )
    parts = state.parse_event_name(load_lenex(_splash(event)).event_names[1])
    assert (parts["dist"], parts["stroke"], parts["gender"], parts["age"]) == (
        "100",
        "medley",
        "men",
        age,
    )


def test_a_final_seeded_by_age_group_leaves_the_span_to_its_heats():
    """Each heat of a Splash final has its own age group, shown beside the name
    (`S-25`): the span in the name too would read `11+ · 13-14`."""
    event = (
        '<EVENT eventid="1" number="1" gender="M" round="FIN">'
        '<SWIMSTYLE distance="50" relaycount="1" stroke="FREE"/>'
        '<AGEGROUPS><AGEGROUP agegroupid="1" agemin="11" agemax="12"/>'
        '<AGEGROUP agegroupid="2" agemin="13" agemax="-1"/></AGEGROUPS>'
        '<HEATS><HEAT heatid="1" number="1" agegroupid="1"/>'
        '<HEAT heatid="2" number="2" agegroupid="2"/></HEATS></EVENT>'
    )
    data = load_lenex(_splash(event))
    assert data.event_names[1] == "Men's 50 Freestyle"
    assert data.rounds[0]["heat_ages"] == {1: "11-12", 2: "13+"}


def test_a_relay_name_reads_back_as_a_relay():
    import state

    raw = load_lenex(_splash(_event('distance="50" relaycount="4" stroke="FREE"')))
    parts = state.parse_event_name(raw.event_names[1])
    assert (parts["dist"], parts["relay"], parts["stroke"]) == (
        "4x50",
        True,
        "freestyle",
    )


def test_a_time_trial_is_a_round():
    data = load_lenex(
        _splash(_event('distance="50" stroke="FREE"', extra='round="TIMETRIAL"'))
    )
    assert data.event_rounds == {1: "time_trial"}


def _with_result(heat_status="", event_status="", other_status=""):
    events = (
        f'<EVENT eventid="1" number="1" {event_status}><SWIMSTYLE distance="50" stroke="FREE"/>'
        f'<HEATS><HEAT heatid="1" number="1" {heat_status}/></HEATS></EVENT>'
        f'<EVENT eventid="2" number="2" {other_status}><SWIMSTYLE distance="50" stroke="FREE"/>'
        '<HEATS><HEAT heatid="2" number="1"/></HEATS></EVENT>'
    )
    clubs = """<CLUB name="Aqua"><ATHLETES><ATHLETE athleteid="1" lastname="Smith" firstname="Jane">
      <ENTRIES><ENTRY eventid="1" heatid="1" lane="3"/></ENTRIES>
      <RESULTS><RESULT resultid="1" eventid="1" heatid="1" lane="3" swimtime="00:00:30.10"/></RESULTS>
    </ATHLETE></ATHLETES></CLUB>"""
    return load_lenex(_splash(events, clubs)).results


@pytest.mark.parametrize(
    ("statuses", "kept"),
    [
        ({}, True),  # an exporter that writes no status at all
        ({"heat_status": 'status="OFFICIAL"'}, True),
        ({"heat_status": 'status="UNOFFICIAL"'}, False),
        ({"event_status": 'status="OFFICIAL"'}, True),  # the heat goes by its event
        ({"event_status": 'status="UNOFFICIAL"'}, False),
        ({"heat_status": 'status="INOFFICIAL"'}, False),  # Splash's spelling
        ({"other_status": 'status="SEEDED"'}, False),  # the file tracks status
    ],
)
def test_which_results_count_as_official(statuses, kept):
    results = _with_result(**statuses)
    assert (results == {1: {1: {3: {"time": "00:00:30.10", "status": ""}}}}) is kept


def test_the_pool_is_the_meets_not_a_qualifying_meets():
    clubs = """<CLUB name="Aqua"><ATHLETES><ATHLETE athleteid="1" lastname="Smith" firstname="Jane">
      <ENTRIES><ENTRY eventid="1" heatid="1" lane="3">
        <MEETINFO city="Elsewhere" date="2026-01-01" nation="CAN"><POOL lanemin="1" lanemax="10"/></MEETINFO>
      </ENTRY></ENTRIES></ATHLETE></ATHLETES></CLUB>"""
    info = load_lenex(
        _splash(
            _event('distance="50" stroke="FREE"'),
            clubs,
            meet_children='<FACILITY city="Québec" nation="CAN" name="Centre aquatique"/>',
        )
    ).meet_info
    assert info["pool"] == "Centre aquatique"
    assert "lanes_lenex" not in info


def test_a_sessions_pool_when_the_meet_has_none():
    xml = _splash(_event('distance="50" stroke="FREE"')).getvalue()
    with zipfile.ZipFile(io.BytesIO(xml)) as z:
        text = z.read("meet.lef").decode()
    text = text.replace(
        'date="2026-11-07">', 'date="2026-11-07"><POOL lanemin="1" lanemax="8"/>'
    )
    assert load_lenex(make_lxf(text)).meet_info["lanes_lenex"] == [1, 8]


@pytest.mark.parametrize(
    ("course", "length"),
    [("LCM", 50), ("SCM", 25), ("SCY", 25), ("SCM33", 33), ("SCY20", 20), ("OPEN", 0)],
)
def test_a_courses_pool_length(course, length):
    data = load_lenex(
        _splash(_event('distance="50" stroke="FREE"'), meet_attrs=f'course="{course}"')
    )
    assert data.meet_info["pool_length_lenex"] == length


def test_a_course_set_per_session():
    """No meet `course`: the sessions carry it, and a seed is measured against
    the course its own round is swum in."""
    clubs = """<CLUB name="Aqua"><ATHLETES><ATHLETE athleteid="1" lastname="Smith" firstname="Jane">
      <ENTRIES><ENTRY eventid="1" heatid="1" lane="3" entrytime="00:00:30.00" entrycourse="LCM"/></ENTRIES>
    </ATHLETE></ATHLETES></CLUB>"""
    xml = _splash(
        _event('distance="50" stroke="FREE"'), clubs, meet_attrs=""
    ).getvalue()
    with zipfile.ZipFile(io.BytesIO(xml)) as z:
        text = z.read("meet.lef").decode()
    data = load_lenex(
        make_lxf(text.replace('number="1" date=', 'number="1" course="LCM" date='))
    )
    assert data.meet_info["course"] == "LCM"
    assert data.meet_info["pool_length_lenex"] == 50
    assert data.start_list[1][1][3]["seed_course"] == ""


def test_names_clubs_and_relays_as_the_spec_writes_them():
    events = _event('distance="50" stroke="FREE"') + _event(
        'distance="50" relaycount="4" stroke="FREE"', number=2
    )
    clubs = """
      <CLUB name="Aqua Club" shortname="" code="AQUA">
        <ATHLETES>
          <ATHLETE athleteid="1" nameprefix="van den" lastname="Hoogenband" firstname="Pieter">
            <ENTRIES><ENTRY eventid="1" heatid="1" lane="3"/></ENTRIES>
          </ATHLETE>
          <ATHLETE athleteid="2" lastname="Smith" firstname="Jane"/>
        </ATHLETES>
        <RELAYS><RELAY number="1" name="Aqua Rockets" gender="F">
          <ENTRIES><ENTRY eventid="2" heatid="2" lane="4">
            <RELAYPOSITIONS>
              <RELAYPOSITION number="1" athleteid="1"/>
              <RELAYPOSITION number="2" athleteid="2"/>
            </RELAYPOSITIONS>
          </ENTRY></ENTRIES>
          <RESULTS><RESULT resultid="9" eventid="2" heatid="2" lane="4" swimtime="NT">
            <RELAYPOSITIONS>
              <RELAYPOSITION number="1" athleteid="2"/>
              <RELAYPOSITION number="2" athleteid="1"/>
            </RELAYPOSITIONS>
          </RESULT></RESULTS>
        </RELAY></RELAYS>
      </CLUB>"""
    start = load_lenex(_splash(events, clubs)).start_list
    assert start[1][1][3]["name"] == "Pieter van den Hoogenband"
    assert start[1][1][3]["club"] == "AQUA"
    relay = start[2][1][4]
    assert (relay["name"], relay["club"]) == ("Aqua Rockets", "AQUA")
    # The order the result swam, not the one entered.
    assert [s["name"] for s in relay["swimmers"]] == [
        "Jane Smith",
        "Pieter van den Hoogenband",
    ]


def test_a_record_holder_is_not_a_swimmer():
    xml = _splash(_event('distance="50" stroke="FREE"')).getvalue()
    with zipfile.ZipFile(io.BytesIO(xml)) as z:
        text = z.read("meet.lef").decode()
    records = """<RECORDLISTS><RECORDLIST course="SCM" gender="F" name="Club"><RECORDS>
      <RECORD swimtime="00:00:25.00"><SWIMSTYLE distance="50" relaycount="1" stroke="FREE"/>
        <ATHLETE lastname="Old" firstname="Record" gender="F" birthdate="1990-01-01"/>
      </RECORD></RECORDS></RECORDLIST></RECORDLISTS>"""
    data = load_lenex(make_lxf(text.replace("</MEETS>", "</MEETS>" + records)))
    assert data.start_list == {1: {1: {}}}


def _fastest_heats(tim_status):
    events = (
        '<EVENT eventid="1" number="1" round="TIM"><SWIMSTYLE distance="400" stroke="FREE"/>'
        f'<HEATS><HEAT heatid="1" number="1" status="{tim_status}"/></HEATS></EVENT>'
        '<EVENT eventid="2" number="1" round="FHT" preveventid="1">'
        '<SWIMSTYLE distance="400" stroke="FREE"/>'
        '<HEATS><HEAT heatid="2" number="1" status="SEEDED"/></HEATS></EVENT>'
    )
    clubs = """<CLUB name="Aqua"><ATHLETES>
      <ATHLETE athleteid="1" lastname="Slow" firstname="Ann">
        <ENTRIES><ENTRY eventid="1" heatid="1" lane="3"/></ENTRIES>
        <RESULTS><RESULT resultid="1" eventid="1" heatid="1" lane="3" swimtime="00:05:00.00"/></RESULTS>
      </ATHLETE>
      <ATHLETE athleteid="2" lastname="Fast" firstname="Bea">
        <ENTRIES><ENTRY eventid="2" heatid="2" lane="4"/></ENTRIES>
      </ATHLETE></ATHLETES></CLUB>"""
    return load_lenex(_splash(events, clubs))


def test_fastest_heats_wait_for_their_timed_final():
    assert _fastest_heats("INOFFICIAL").live_rounds == {1: "timed_final"}
    assert _fastest_heats("OFFICIAL").live_rounds == {1: "fastest_heat"}


def _extra_heats_xml():
    """Splash's 1500 swum alternating with other events: each heat an EVENT of
    its own (`round="EXTRAHEATS"`), naming the timed final that holds the age
    groups, which comes later in the file without heats."""
    extra = (
        '<EVENT eventid="{eid}" number="1" gender="F" round="EXTRAHEATS" '
        'preveventid="1"><SWIMSTYLE distance="1500" relaycount="1" stroke="FREE"/>'
        '<HEATS><HEAT heatid="{eid}" number="{heat}" daytime="{time}"{age}/></HEATS>'
        "</EVENT>"
    )
    events = (
        extra.format(eid=11, heat=1, time="17:15", age="")
        + '<EVENT eventid="5" number="5" gender="M"><SWIMSTYLE distance="50" '
        'relaycount="1" stroke="FREE"/><HEATS><HEAT heatid="5" number="1"/></HEATS>'
        "</EVENT>"
        + extra.format(eid=12, heat=2, time="17:58", age=' agegroupid="101"')
        + '<EVENT eventid="1" number="1" gender="F" round="TIM">'
        '<SWIMSTYLE distance="1500" relaycount="1" stroke="FREE"/>'
        '<AGEGROUPS><AGEGROUP agegroupid="100" agemin="-1" agemax="12"/>'
        '<AGEGROUP agegroupid="101" agemin="13" agemax="14"/></AGEGROUPS></EVENT>'
    )
    clubs = """<CLUB name="Aqua"><ATHLETES>
      <ATHLETE athleteid="1" lastname="Un" firstname="Ann">
        <ENTRIES><ENTRY eventid="1" heatid="11" lane="3"/></ENTRIES></ATHLETE>
      <ATHLETE athleteid="2" lastname="Deux" firstname="Bea">
        <ENTRIES><ENTRY eventid="1" heatid="12" lane="4"/></ENTRIES></ATHLETE>
      </ATHLETES></CLUB>"""
    return load_lenex(_splash(events, clubs))


def test_splash_extra_heats_are_the_heats_of_their_timed_final():
    data = _extra_heats_xml()
    assert {h: list(lanes) for h, lanes in data.start_list[1].items()} == {
        1: [3],
        2: [4],
    }
    assert data.start_list[1][1][3]["name"] == "Ann Un"
    assert data.start_list[1][2][4]["name"] == "Bea Deux"
    assert data.heat_times[1] == {1: "17:15", 2: "17:58"}
    assert data.event_rounds == {1: "timed_final"}
    assert data.live_rounds == {}
    assert data.event_distances[1] == 1500


def test_splash_extra_heats_run_where_their_first_heat_is():
    data = _extra_heats_xml()
    assert [r["event"] for r in data.rounds] == [1, 5]
    assert data.rounds[0]["heat_ages"] == {2: "13-14"}
    assert data.meet_info["sessions"][0]["events"] == [1, 5]


def test_extra_heats_naming_another_number_stay_a_round_of_their_own():
    events = (
        '<EVENT eventid="1" number="1" round="TIM"><SWIMSTYLE distance="800" '
        'stroke="FREE"/></EVENT>'
        '<EVENT eventid="2" number="2" round="EXTRAHEATS" preveventid="1">'
        '<SWIMSTYLE distance="800" stroke="FREE"/>'
        '<HEATS><HEAT heatid="2" number="1"/></HEATS></EVENT>'
    )
    data = load_lenex(_splash(events))
    assert [(r["event"], r["kind"]) for r in data.rounds] == [
        (1, "timed_final"),
        (2, ""),
    ]


def test_fastest_heats_take_their_age_groups_from_their_timed_final():
    """Lenex 3.0: a FHT EVENT may have no AGEGROUPS; its heats name the timed
    final's."""
    events = (
        '<EVENT eventid="1" number="1" round="TIM"><SWIMSTYLE distance="400" '
        'stroke="FREE"/><AGEGROUPS><AGEGROUP agegroupid="7" agemin="11" agemax="13"/>'
        '<AGEGROUP agegroupid="8" agemin="14" agemax="-1"/></AGEGROUPS>'
        '<HEATS><HEAT heatid="1" number="1"/></HEATS></EVENT>'
        '<EVENT eventid="2" number="1" round="FHT" preveventid="1">'
        '<SWIMSTYLE distance="400" stroke="FREE"/><HEATS>'
        '<HEAT heatid="2" number="2" agegroupid="7"/>'
        '<HEAT heatid="3" number="3" agegroupid="8"/></HEATS></EVENT>'
    )
    data = load_lenex(_splash(events))
    assert data.rounds[1]["heat_ages"] == {2: "11-13", 3: "14+"}


def test_an_event_or_heat_without_a_number_is_left_out_not_the_file():
    """A number an exporter wrote wrong costs that event or heat, not the meet:
    the file still loads, the board still has every other event."""
    events = (
        '<EVENT eventid="1" number="1x"><SWIMSTYLE distance="50" stroke="FREE"/>'
        '<HEATS><HEAT heatid="1" number="1"/></HEATS></EVENT>'
        '<EVENT eventid="2"><SWIMSTYLE distance="50" stroke="FREE"/>'
        '<HEATS><HEAT heatid="2" number="1"/></HEATS></EVENT>'
        '<EVENT eventid="3" number=" 3 "><SWIMSTYLE distance="50" stroke="FREE"/>'
        '<HEATS><HEAT heatid="3" number="1"/><HEAT heatid="4" number=""/>'
        '<HEAT heatid="5" number="two"/></HEATS></EVENT>'
    )
    clubs = """<CLUB name="Aqua"><ATHLETES>
      <ATHLETE athleteid="1" lastname="Un" firstname="Ann"><ENTRIES>
        <ENTRY eventid="3" heatid="3" lane="2"/>
        <ENTRY eventid="3" heatid="5" lane="3"/>
        <ENTRY eventid="3" heat="1b" lane="4"/>
      </ENTRIES></ATHLETE></ATHLETES></CLUB>"""
    data = load_lenex(_splash(events, clubs))
    assert list(data.event_names) == [3]
    assert {h: list(lanes) for h, lanes in data.start_list[3].items()} == {1: [2]}
    assert data.meet_info["sessions"][0]["events"] == [3]


def test_an_entrys_heat_number_that_is_not_one_is_skipped():
    """Structure B: an ENTRY under its EVENT with no `heatid` falls back to a
    `heat` number, which some exporters write; one that is not a number is
    skipped rather than refusing the file."""
    xml = lenex3_xml(
        '<EVENT eventid="1" number="1"><SWIMSTYLE distance="50" stroke="FREE"/>'
        '<HEATS><HEAT heatid="1" number="1"/></HEATS><ENTRIES>'
        '<ENTRY athleteid="1" heat="1" lane="3"/>'
        '<ENTRY athleteid="2" heat="A" lane="4"/>'
        '<ENTRY athleteid="3" heat="9" lane="5"/>'
        "</ENTRIES></EVENT>"
    )
    data = load_lenex(make_lxf(xml))
    assert list(data.start_list[1][1]) == [3]


def _swimoff(swimoff_status, final_seeded=True, results=False):
    """Prelims, a swim-off for a tie, and the final, all event 3 (Splash)."""
    events = (
        '<EVENT eventid="1" number="3" round="PRE" status="OFFICIAL">'
        '<SWIMSTYLE distance="50" stroke="BREAST"/>'
        '<HEATS><HEAT heatid="1" number="1" status="OFFICIAL"/></HEATS></EVENT>'
        '<EVENT eventid="2" number="3" round="SOP" preveventid="1">'
        '<SWIMSTYLE distance="50" stroke="BREAST"/>'
        f'<HEATS><HEAT heatid="2" number="1" status="{swimoff_status}"/></HEATS></EVENT>'
        '<EVENT eventid="3" number="3" round="FIN" preveventid="1">'
        '<SWIMSTYLE distance="50" stroke="BREAST"/>'
        '<HEATS><HEAT heatid="3" number="1" status="SEEDED"/></HEATS></EVENT>'
    )
    final = '<ENTRY eventid="3" heatid="3" lane="4"/>' if final_seeded else ""
    result = (
        '<RESULTS><RESULT eventid="2" heatid="2" lane="4" swimtime="00:00:40.10"/>'
        "</RESULTS>"
        if results
        else ""
    )
    clubs = f"""<CLUB name="Aqua"><ATHLETES>
      <ATHLETE athleteid="1" lastname="Un" firstname="Ann"><ENTRIES>
        <ENTRY eventid="1" heatid="1" lane="4"/>
        <ENTRY eventid="2" heatid="2" lane="4"/>{final}
      </ENTRIES>{result}</ATHLETE>
      <ATHLETE athleteid="2" lastname="Deux" firstname="Bea"><ENTRIES>
        <ENTRY eventid="1" heatid="1" lane="5"/>
        <ENTRY eventid="2" heatid="2" lane="5"/>
      </ENTRIES></ATHLETE></ATHLETES></CLUB>"""
    return load_lenex(_splash(events, clubs))


@pytest.mark.parametrize(
    ("status", "results", "live"),
    [
        ("SEEDED", False, "swimoff"),
        ("RUNNING", False, "swimoff"),
        ("INOFFICIAL", False, "final"),
        ("UNOFFICIAL", False, "final"),
        ("OFFICIAL", False, "final"),
        # Splash leaves a heat whose results were typed in by hand SEEDED.
        ("SEEDED", True, "final"),
    ],
)
def test_a_final_waits_for_its_swimoff(status, results, live):
    """Seeded before the swim-off is swum, the final would put its own heat 1 on
    the board when the console calls event 3 heat 1 for the tied swimmers."""
    data = _swimoff(status, results=results)
    assert data.live_rounds == {3: live}
    # The swim-off's two tied swimmers, or the final's one.
    assert sorted(data.start_list[3][1]) == ([4, 5] if live == "swimoff" else [4])


def test_a_swimoff_before_its_final_is_seeded_is_live():
    assert _swimoff("SEEDED", final_seeded=False).live_rounds == {3: "swimoff"}


def test_a_file_without_statuses_keeps_the_last_seeded_round_live():
    """Nothing says whether the swim-off was swum: the final takes the number, as
    before the rule."""
    data = load_lenex(
        _splash(
            '<EVENT eventid="2" number="3" round="SOP"><SWIMSTYLE distance="50" '
            'stroke="FREE"/><HEATS><HEAT heatid="2" number="1"/></HEATS></EVENT>'
            '<EVENT eventid="3" number="3" round="FIN"><SWIMSTYLE distance="50" '
            'stroke="FREE"/><HEATS><HEAT heatid="3" number="1"/></HEATS></EVENT>',
            """<CLUB name="Aqua"><ATHLETES><ATHLETE athleteid="1" lastname="Un"
            firstname="Ann"><ENTRIES><ENTRY eventid="2" heatid="2" lane="4"/>
            <ENTRY eventid="3" heatid="3" lane="4"/></ENTRIES></ATHLETE>
            </ATHLETES></CLUB>""",
        )
    )
    assert data.live_rounds == {3: "final"}


@pytest.mark.parametrize(
    ("attrs", "club"),
    [
        # Splash: the federation's code, and a short *name* beside it.
        ('code="MUST" shortname="Mustang Boucherville"', "MUST"),
        ('code=" " shortname="Rouge et Or"', "Rouge et Or"),
        ('shortname="AQUA"', "AQUA"),
        ('code=""', "Club Name"),
    ],
)
def test_a_lane_shows_the_clubs_code_before_its_short_name(attrs, club):
    clubs = f"""<CLUB name="Club Name" {attrs}><ATHLETES>
      <ATHLETE athleteid="1" lastname="Un" firstname="Ann">
        <ENTRIES><ENTRY eventid="1" heatid="1" lane="3"/></ENTRIES>
      </ATHLETE></ATHLETES></CLUB>"""
    start = load_lenex(_splash(_event('distance="50" stroke="FREE"'), clubs)).start_list
    assert start[1][1][3]["club"] == club
