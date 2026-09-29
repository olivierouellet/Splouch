"""HyTek Meet Manager CSV heat-sheet parser.

CSV column layout (observed in MM4–MM8 exports):

  Fixed:
    row[6]   "#N Event Name"  or  "Event N Event Name"

  Variable-position header columns (somewhere in columns 60-106):
    "Lane"       — anchor; data values appear at [header_col + offset]
    "Name"       — individual events: "Lastname, Firstname" at +offset
    "Relay"      — relay events: relay letter (A/B/…) at +offset
    "Age"        — swimmer age at +offset
    "Team"       — club/team code at +offset
    "Seed Time"  — seed time string at +offset ("NT" / "SCR" = no seed)
    "Heat N of M Finals" — heat label at offset-1

  Column offset is 6, 7, or 8 depending on the MM version; auto-detected
  by finding which cell at [Lane_col + offset] is an integer (the lane number).
"""

import csv


class HytekParser:
    """Parse HyTek Meet Manager CSV heat-sheet files."""

    def __init__(self, path: str | None = None) -> None:
        self.events: dict = {}  # (event_num, heat_num) → {lane: display_str}
        self.event_names: dict = {}  # event_num → name string
        self.seed_times: dict = {}  # (event_num, heat_num) → {lane: time_str}
        if path:
            self.load(path)

    def clear(self) -> None:
        self.events.clear()
        self.event_names.clear()
        self.seed_times.clear()

    def load(self, path: str) -> None:
        self.clear()
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            self.load_from_file(f)

    def load_from_file(self, file) -> None:
        self.clear()
        for row in csv.reader(file):
            self._parse_row([c.strip() for c in row])

    # ── Internal ──────────────────────────────────────────────────────────────

    def _parse_row(self, row: list[str]) -> None:
        if len(row) < 107:
            row += [""] * (107 - len(row))

        # ── Event number and name (column 6) ──────────────────────────────────
        cell = row[6].lstrip("#")
        cell = cell.removeprefix("Event ")
        num_str, _, evt_name = cell.strip().partition(" ")
        try:
            event_num = int(num_str)
        except ValueError:
            return
        self.event_names[event_num] = evt_name.strip()

        # ── Locate "Lane" header column (60-106) ──────────────────────────────
        lane_hdr = next((i for i in range(60, 107) if row[i] == "Lane"), None)
        if lane_hdr is None:
            return

        # ── Detect column offset (6–8): position of the lane number ───────────
        offset = next(
            (o for o in range(6, 9) if row[lane_hdr + o].isdigit()),
            None,
        )
        if offset is None:
            return

        # ── Heat and lane numbers ──────────────────────────────────────────────
        try:
            heat_num = int(row[lane_hdr + offset - 1].split()[1])
            lane_num = int(row[lane_hdr + offset])
        except (ValueError, IndexError):
            return

        # ── Column headers in range after Lane ────────────────────────────────
        headers = row[lane_hdr:107]

        # ── Team code ─────────────────────────────────────────────────────────
        team = ""
        if "Team" in headers:
            team = row[lane_hdr + headers.index("Team") + offset]

        # ── Display string (name or relay) ────────────────────────────────────
        if "Name" in headers:
            raw = row[lane_hdr + headers.index("Name") + offset]
            last, _, first = raw.partition(",")
            swimmer = f"{first.strip()} {last.strip()}".strip()
            entry = f"{team[:4]:<4} {swimmer}"
        elif "Relay" in headers:
            letter = row[lane_hdr + headers.index("Relay") + offset].strip()
            entry = f"{team[:4]:<4} {letter}".strip() if letter else team[:4]
        else:
            entry = ""

        # ── Seed time ─────────────────────────────────────────────────────────
        seed = ""
        if "Seed Time" in headers:
            raw_seed = row[lane_hdr + headers.index("Seed Time") + offset]
            if raw_seed not in ("NT", "SCR", ""):
                seed = raw_seed

        # ── Store ──────────────────────────────────────────────────────────────
        key = (event_num, heat_num)
        self.events.setdefault(key, {})[lane_num] = entry
        self.seed_times.setdefault(key, {})[lane_num] = seed

    # ── Public accessors ──────────────────────────────────────────────────────

    def get_event_name(self, event_num: int) -> str:
        return self.event_names.get(event_num, "")

    def get_display_string(self, event_num: int, heat_num: int, lane: int) -> str:
        return self.events.get((event_num, heat_num), {}).get(lane, "")

    def get_seed_time(self, event_num: int, heat_num: int, lane: int) -> str:
        return self.seed_times.get((event_num, heat_num), {}).get(lane, "")
