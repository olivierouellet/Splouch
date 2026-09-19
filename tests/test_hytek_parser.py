import csv
import io
from meet_parsers.hytek_parser import HytekParser as HytekEventLoader

# ── Helpers ────────────────────────────────────────────────────────────────────
#
# The HyTek CSV format requires:
#   row[6]              — "#N Event Name" or "Event N Event Name"
#   row[LANE]           — "Lane"          (must be at index 60-106)
#   row[LANE+1]         — "Name" or "Relay"
#   row[LANE+2]         — "Age"
#   row[LANE+3]         — "Team"
#   row[LANE+4]         — "Seed Time"
#   row[LANE+5]         — "Heat N of M   Finals"
#   row[LANE+6]         — lane number    (int as string, column_offset=6)
#   row[LANE+7]         — "Lastname, Firstname"  (individual only)
#   row[LANE+8]         — age            (individual only)
#   row[LANE+9]         — team code
#
LANE_HEADER = 62  # arbitrary index in the 60-106 range


def make_row(event_spec, heat, lane, name, team, relay=False):
    row = [''] * 110
    row[6] = event_spec
    row[LANE_HEADER]     = 'Lane'
    row[LANE_HEADER + 1] = 'Relay' if relay else 'Name'
    row[LANE_HEADER + 2] = 'Age'
    row[LANE_HEADER + 3] = 'Team'
    row[LANE_HEADER + 4] = 'Seed Time'
    row[LANE_HEADER + 5] = f'Heat {heat} of 2   Finals'
    row[LANE_HEADER + 6] = str(lane)
    if not relay:
        row[LANE_HEADER + 7] = name           # "Lastname, Firstname"
        row[LANE_HEADER + 8] = '15'           # age
    row[LANE_HEADER + 9] = team
    return row


def load_rows(*rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    buf.seek(0)
    loader = HytekEventLoader()
    loader.load_from_file(buf)
    return loader


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestHytekParser:

    def test_event_name_hash_prefix(self):
        loader = load_rows(make_row('#1 Girls 100 Freestyle', 1, 3, 'Smith, Jane', 'AQUA'))
        assert loader.event_names[1] == 'Girls 100 Freestyle'

    def test_event_name_event_prefix(self):
        loader = load_rows(make_row('Event 1 Girls 100 Freestyle', 1, 3, 'Smith, Jane', 'AQUA'))
        assert loader.event_names[1] == 'Girls 100 Freestyle'

    def test_display_string_format(self):
        # Format is "(TEAM)[:4] Name Lastname"
        loader = load_rows(make_row('#1 Girls 100 Freestyle', 1, 3, 'Smith, Jane', 'AQUA'))
        assert loader.get_display_string(1, 1, 3) == 'AQUA Jane Smith'

    def test_team_code_truncated_to_4_chars(self):
        loader = load_rows(make_row('#1 Girls 100 Freestyle', 1, 3, 'Smith, Jane', 'TOOLONG'))
        assert loader.get_display_string(1, 1, 3) == 'TOOL Jane Smith'

    def test_relay_event(self):
        loader = load_rows(make_row('#2 Mixed 200 Medley Relay', 1, 4, '', 'AQUA', relay=True))
        assert loader.event_names[2] == 'Mixed 200 Medley Relay'
        assert loader.get_display_string(2, 1, 4) == 'AQUA'

    def test_multiple_lanes(self):
        loader = load_rows(
            make_row('#1 Girls 100 Freestyle', 1, 3, 'Smith, Jane',  'AQUA'),
            make_row('#1 Girls 100 Freestyle', 1, 5, 'Doe, Mary',    'WAVE'),
        )
        assert loader.get_display_string(1, 1, 3) == 'AQUA Jane Smith'
        assert loader.get_display_string(1, 1, 5) == 'WAVE Mary Doe'

    def test_multiple_heats(self):
        loader = load_rows(
            make_row('#1 Girls 100 Freestyle', 1, 3, 'Smith, Jane',    'AQUA'),
            make_row('#1 Girls 100 Freestyle', 2, 4, 'Tremblay, Marc', 'CLUB'),
        )
        assert loader.get_display_string(1, 1, 3) == 'AQUA Jane Smith'
        assert loader.get_display_string(1, 2, 4) == 'CLUB Marc Tremblay'

    def test_missing_event_returns_empty(self):
        loader = HytekEventLoader()
        assert loader.get_event_name(99) == ''

    def test_missing_lane_returns_empty(self):
        loader = load_rows(make_row('#1 Girls 100 Freestyle', 1, 3, 'Smith, Jane', 'AQUA'))
        assert loader.get_display_string(1, 1, 9) == ''
