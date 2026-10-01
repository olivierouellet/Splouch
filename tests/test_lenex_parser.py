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

    def test_round_keeps_only_final_and_semifinal(self):
        """`S-01`: only the two rounds a spectator asks about get a word; a timed
        final, prelims and an event with no `round` stay out of the map."""
        events = "".join(
            f'<EVENT number="{n}" name="50 Free" {attr}><HEATS/></EVENT>'
            for n, attr in [
                (1, 'round="FIN"'),
                (2, 'round="sem"'),
                (3, 'round="TIM"'),
                (4, 'round="PRE"'),
                (5, ""),
            ]
        )
        for wrap in (lenex2_xml, lenex3_xml):
            data = load_lenex(make_lxf(wrap(events)))
            assert data.event_rounds == {1: "final", 2: "semifinal"}

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
