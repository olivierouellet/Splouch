import zipfile
import xml.etree.ElementTree as ET
from collections import namedtuple

LenexData = namedtuple('LenexData', ['event_names', 'start_list', 'heat_times', 'meet_info', 'event_distances'])

# Biggest inner XML we will read out of a .lxf. A real meet's start list is a few
# hundred KB; this is room for an unusually large one and a hard stop well before
# a crafted archive can exhaust the Pi's memory. `ZipFile.open` streams, so an
# archive that claims a small size and then expands (a zip bomb) is caught by
# reading through this cap rather than by trusting the header.
MAX_XML_BYTES = 64 * 1024 * 1024


def _check_no_doctype(data):
    """Refuse a document type declaration before handing bytes to the parser.

    ElementTree expands entities defined in an internal DTD, so a file carrying
    the classic nested definitions ("billion laughs") expands to gigabytes during
    the parse and takes the server down with it — from an upload, on the machine
    running the meet. No Lenex exporter emits a DOCTYPE, so refusing one removes
    the whole class of attack rather than reasoning about which entity forms are
    safe.

    Checked on the bytes rather than through a parser hook: the accelerated
    XMLParser exposes no handler to install, and XML requires the declaration to
    sit in the prolog, ahead of the root element, where a scan can see it.
    """
    i = 0
    while i < len(data):
        i = data.find(b'<', i)
        if i < 0:
            return                       # no element at all; let the parser complain
        if data.startswith(b'<!DOCTYPE', i):
            raise ValueError('Lenex file contains a DOCTYPE declaration; '
                             'refusing to parse it.')
        if data[i + 1:i + 2] not in (b'?', b'!'):
            return                       # root element reached — the prolog is clean
        # A processing instruction, comment or other declaration: step over it.
        end = data.find(b'>', i)
        if end < 0:
            return
        i = end + 1


def _open_lenex_xml(path):
    """Return the parsed XML tree for a Lenex file at *path*.

    A .lxf file is a zip that, per the spec, holds a single .lef XML. Real-world
    exporters vary, so be forgiving: match the inner file case-insensitively, and
    if none ends in .lef fall back to a .xml member or the only file present. A
    plain (unzipped) .lef XML file is also accepted.
    """
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith('/')]
            xml_name = (
                next((n for n in names if n.lower().endswith('.lef')), None)
                or next((n for n in names if n.lower().endswith('.xml')), None)
                or (names[0] if len(names) == 1 else None)
            )
            if xml_name is None:
                raise ValueError(
                    f'No Lenex XML (.lef) found inside {path}; '
                    f'archive contains: {", ".join(names) or "(empty)"}'
                )
            with z.open(xml_name) as fh:
                data = fh.read(MAX_XML_BYTES + 1)
            if len(data) > MAX_XML_BYTES:
                raise ValueError(
                    f'Lenex XML inside {path} is larger than '
                    f'{MAX_XML_BYTES // (1024 * 1024)} MB; refusing to parse it.'
                )
            _check_no_doctype(data)
            return ET.ElementTree(ET.fromstring(data))
    # Not a zip — assume a raw .lef/.xml Lenex document.
    with open(path, 'rb') as fh:
        data = fh.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError(
            f'{path} is larger than {MAX_XML_BYTES // (1024 * 1024)} MB; '
            f'refusing to parse it.'
        )
    _check_no_doctype(data)
    return ET.ElementTree(ET.fromstring(data))


def load_lenex(path):
    """
    Parse a Lenex .lxf file (zip containing a .lef XML).

    Returns a LenexData namedtuple:
        event_names  — {event_number: str}
        start_list   — {event_number: {heat_number: {lane: {'name': str, 'club': str}}}}
    """
    tree = _open_lenex_xml(path)

    root = tree.getroot()

    # Detect namespace (Lenex 3.0 uses one, 2.0 does not)
    ns_raw = root.tag.split('}')[0].lstrip('{') if '}' in root.tag else ''
    ns = {'l': ns_raw} if ns_raw else {}
    prefix = 'l:' if ns_raw else ''

    def find(node, tag):
        if ns:
            return node.findall(f'.//{prefix}{tag}', ns)
        return node.findall(f'.//{tag}')

    def find_first(node, tag):
        if ns:
            return node.find(f'.//{prefix}{tag}', ns)
        return node.find(f'.//{tag}')

    def find_direct(node, tag):
        """Non-recursive: direct children only."""
        if ns:
            return node.findall(f'{prefix}{tag}', ns)
        return node.findall(tag)

    # Athlete lookup: athleteid → (lastname, firstname)
    athletes = {}
    for a in find(root, 'ATHLETE'):
        athletes[a.get('athleteid')] = (
            a.get('lastname', ''),
            a.get('firstname', ''),
        )

    # Club lookup: athleteid → club shortname
    clubs = {}
    # Relay lookup: relayid → club shortname
    relay_clubs = {}
    for c in find(root, 'CLUB'):
        shortname = c.get('shortname', c.get('code', c.get('name', '')))
        for a in find(c, 'ATHLETE'):
            clubs[a.get('athleteid')] = shortname
        for r in find(c, 'RELAY'):
            rid = r.get('relayid', '')
            if rid:
                relay_clubs[rid] = shortname

    _gender_map = {'M': "Men's", 'F': "Women's", 'X': 'Mixed'}
    _stroke_map = {
        'FREESTYLE': 'Freestyle',   'FREE': 'Freestyle',
        'BACKSTROKE': 'Backstroke', 'BACK': 'Backstroke',
        'BREASTSTROKE': 'Breaststroke', 'BREAST': 'Breaststroke',
        'BUTTERFLY': 'Butterfly',   'FLY': 'Butterfly',
        'MEDLEY': 'Medley',
    }

    def event_name_str(event):
        prename = event.get('prename', '')
        name    = event.get('name', '')
        if prename or name:
            return f'{prename} {name}'.strip()
        # Construct from SWIMSTYLE element
        style = find_first(event, 'SWIMSTYLE')
        if style is not None:
            parts = [
                _gender_map.get(event.get('gender', ''), ''),
                style.get('distance', ''),
                _stroke_map.get(style.get('stroke', '').upper(),
                                style.get('stroke', '').capitalize()),
            ]
            return ' '.join(p for p in parts if p)
        return f'Event {event.get("number", "")}'

    # Build event names and start list — pass 1: events and heats
    event_names      = {}
    start_list       = {}
    heat_times       = {}   # {event_num: {heat_num: daytime_str}}
    event_distances  = {}   # {event_num: int} distance in metres
    eventid_map      = {}   # eventid  → event_number  (Splash-style)
    heatid_map       = {}   # heatid   → (event_number, heat_number)

    for event in find(root, 'EVENT'):
        ev_num = int(event.get('number'))
        event_names[ev_num] = event_name_str(event)
        start_list[ev_num]  = {}
        heat_times[ev_num]  = {}
        style = find_first(event, 'SWIMSTYLE')
        if style is not None:
            try:
                event_distances[ev_num] = int(style.get('distance', 0))
            except (ValueError, TypeError):
                pass
        eid = event.get('eventid', '')
        if eid:
            eventid_map[eid] = ev_num
        for heat in find(event, 'HEAT'):
            h_num = int(heat.get('number'))
            start_list[ev_num][h_num] = {}
            daytime = heat.get('daytime', '')
            if daytime:
                heat_times[ev_num][h_num] = daytime
            hid = heat.get('heatid', '')
            if hid:
                heatid_map[hid] = (ev_num, h_num)

    def _relay_swimmers(entry):
        """Return sorted list of {'pos': int, 'name': str} for RELAYPOSITION children."""
        swimmers = []
        for rp in find(entry, 'RELAYPOSITION'):
            r_aid = rp.get('athleteid', '')
            if not r_aid:
                continue
            last, first = athletes.get(r_aid, ('', ''))
            swimmer_name = f'{first} {last}'.strip()
            if swimmer_name:
                swimmers.append({'pos': int(rp.get('number', 0)), 'name': swimmer_name, 'first': first})
        swimmers.sort(key=lambda x: x['pos'])
        return swimmers

    def _add_entry(entry, ev_num, h_num, aid=None):
        lane = int(entry.get('lane', 0))
        if not lane:
            return
        if aid is None:
            aid = entry.get('athleteid', '')
        if aid:
            last, first = athletes.get(aid, ('', ''))
            name = f'{first} {last}'.strip()
            club = clubs.get(aid, '')
            swimmers = []
        else:
            relay_el = find_first(entry, 'RELAY')
            name = relay_el.get('name', '') if relay_el is not None else ''
            rid  = relay_el.get('relayid', '') if relay_el is not None else ''
            club = relay_clubs.get(rid, '')
            swimmers = _relay_swimmers(entry)
        start_list[ev_num][h_num][lane] = {
            'name': name, 'club': club,
            'seed_time': entry.get('entrytime', ''),
            'swimmers': swimmers,
        }

    # Pass 2: populate entries — detect which layout the file uses.
    #
    # Structure A — ENTRY inside HEAT (hand-crafted / simple files):
    #   EVENT > HEATS > HEAT > ENTRIES > ENTRY (has athleteid, lane)
    #
    # Structure B — ENTRY at EVENT level (standard Lenex 3.0):
    #   EVENT > ENTRIES > ENTRY (has heatid, athleteid, lane)
    #
    # Structure C — ENTRY under ATHLETE (Splash Meet Manager):
    #   CLUB > ATHLETES > ATHLETE (athleteid) > ENTRIES > ENTRY (has eventid/heatid, lane)

    any_entry_in_heat  = any(find(heat, 'ENTRY')
                             for event in find(root, 'EVENT')
                             for heat in find(event, 'HEAT'))
    any_entry_in_event = any(find_direct(entries_el, 'ENTRY')
                             for event in find(root, 'EVENT')
                             for entries_el in find_direct(event, 'ENTRIES'))

    # Structure A — ENTRY nested inside HEAT.
    if any_entry_in_heat:
        for event in find(root, 'EVENT'):
            ev_num = int(event.get('number'))
            for heat in find(event, 'HEAT'):
                h_num = int(heat.get('number'))
                for entry in find(heat, 'ENTRY'):
                    _add_entry(entry, ev_num, h_num)

    # Structure B — ENTRIES directly under EVENT, linked to a heat by heatid.
    # Real Lenex 3.0 files leave the HEATs empty and list every entry here, so
    # this runs independently of Structure A (not only alongside it).
    if any_entry_in_event:
        for event in find(root, 'EVENT'):
            ev_num = int(event.get('number'))
            heatid_to_num = {h.get('heatid', ''): int(h.get('number'))
                             for h in find(event, 'HEAT') if h.get('heatid')}
            for entries_el in find_direct(event, 'ENTRIES'):
                for entry in find_direct(entries_el, 'ENTRY'):
                    hid   = entry.get('heatid', '')
                    h_num = heatid_to_num.get(hid) or int(entry.get('heat', 0) or 0)
                    if h_num:
                        _add_entry(entry, ev_num, h_num)

    # Structure C (Splash) — entries under ATHLETE / RELAY, keyed by eventid+heatid.
    # Only when entries aren't already at the heat or event level.
    if not any_entry_in_heat and not any_entry_in_event:

        def _resolve_heat(entry):
            hid = entry.get('heatid', '')
            if hid and hid in heatid_map:
                return heatid_map[hid]
            eid = entry.get('eventid', '')
            if eid and eid in eventid_map:
                h_num = int(entry.get('heat', 0) or 0)
                ev_num = eventid_map[eid]
                if h_num in start_list.get(ev_num, {}):
                    return ev_num, h_num
            return None, None

        # Individual swimmer entries
        for athlete in find(root, 'ATHLETE'):
            aid = athlete.get('athleteid', '')
            for entry in find(athlete, 'ENTRY'):
                ev_num, h_num = _resolve_heat(entry)
                if ev_num is not None:
                    _add_entry(entry, ev_num, h_num, aid=aid)

        # Relay entries (CLUB > RELAY > ENTRIES > ENTRY)
        for club in find(root, 'CLUB'):
            club_short = club.get('shortname', club.get('code', club.get('name', '')))
            club_full  = club.get('name', club_short)
            for relay in find(club, 'RELAY'):
                relay_num = relay.get('number', '')
                team_name = f'{club_full} {relay_num}'.strip() if relay_num else club_full
                for entry in find(relay, 'ENTRY'):
                    ev_num, h_num = _resolve_heat(entry)
                    if ev_num is None:
                        continue
                    lane = int(entry.get('lane', 0))
                    if not lane:
                        continue
                    start_list[ev_num][h_num][lane] = {
                        'name': team_name, 'club': club_short,
                        'seed_time': entry.get('entrytime', ''),
                        'swimmers': _relay_swimmers(entry),
                    }

    # Meet / pool / session metadata
    _course_to_metres = {'LCM': 50, 'SCM': 25, 'SCY': 25}
    meet_info = {}
    meet_el = find_first(root, 'MEET')
    if meet_el is not None:
        meet_info['name']     = meet_el.get('name', '')
        meet_info['city']     = meet_el.get('city', '')
        meet_info['hostclub'] = meet_el.get('hostclub', '')
        course = meet_el.get('course', '').upper()
        meet_info['pool_length_lenex'] = _course_to_metres.get(course, 0)
        meet_info['course'] = course
        pool_el = find_first(meet_el, 'POOL')
        meet_info['pool'] = pool_el.get('name', '') if pool_el is not None else ''
        sessions = []
        for s in find(meet_el, 'SESSION'):
            sessions.append({
                'date':        s.get('date', ''),
                'daytime':     s.get('daytime', ''),
                'endtime':     s.get('endtime', ''),
                'warmupfrom':  s.get('warmupfrom', ''),
                'warmupuntil': s.get('warmupuntil', ''),
            })
        meet_info['sessions'] = sessions

    return LenexData(event_names=event_names, start_list=start_list,
                     heat_times=heat_times, meet_info=meet_info,
                     event_distances=event_distances)
