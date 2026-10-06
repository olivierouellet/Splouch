"""A Schedule lane's three times, for both servers, from one file.

Every lane can carry up to three times (docs/app.md `S-22`):

* **seed** — the swimmer's entry time, from the meet file;
* **console** — what the timing console read at the finish, provisional;
* **official** — the result validated in Meet Manager, from a re-uploaded meet
  file (docs/architecture/meet-manager-results.md).

The Pi (`meet_data.build_heats`) and the cloud (`cloud_server._build_heats_json`)
both shape `GET /schedule.json` / `GET /meet/{id}/schedule` (docs/api.md §5.8), so
the fields and the rule for a fully official heat are written once, here.

Every time travels as `HH:MM:SS.hh`. Dropping a leading `00:` is the clients' job.
"""

import re

_HMS = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")
_MS = re.compile(r"^(?:(\d+):)?(\d{1,2})\.(\d{2})$")


def hundredths(s):
    """Parse a time string to integer hundredths of a second.

    Handles `SS.hh` ("58.21"), `M:SS.hh` ("0:58.21") and `HH:MM:SS.hh`
    ("00:00:58.21", the Lenex form). None for empty, zero, or unparseable input.
    """
    if not s:
        return None
    s = s.strip()
    m = _HMS.match(s)
    if m:
        h, mi, se, hu = (int(g) for g in m.groups())
        val = h * 360000 + mi * 6000 + se * 100 + hu
        return val if val > 0 else None
    m = _MS.match(s)
    if not m:
        return None
    val = int(m.group(1) or 0) * 6000 + int(m.group(2)) * 100 + int(m.group(3))
    return val if val > 0 else None


def wire_time(s):
    """*s* as `HH:MM:SS.hh`, or `""` when it is empty, `NT`, or not a time."""
    h = hundredths(s)
    if h is None:
        return ""
    return f"{h // 360000:02d}:{h // 6000 % 60:02d}:{h // 100 % 60:02d}.{h % 100:02d}"


def lane_times(seed, console, result):
    """The fields a Schedule lane carries beside `seed_time` (docs/api.md §5.8).

    *result* is the official `{"time", "status"}` for the lane, or None. The delta
    is official − seed, as `results_snapshot` gives the console's (§5.2): negative
    is faster. It is None when there is no seed, no official time, or the result
    is not a finish.
    """
    result = result or {}
    r_time = result.get("time", "")
    r_status = result.get("status", "")
    delta_seconds, delta_better = None, None
    if r_time and not r_status:
        finish, entry = hundredths(r_time), hundredths(seed)
        if finish is not None and entry is not None:
            delta_seconds = round((finish - entry) / 100.0, 2)
            delta_better = finish < entry
    return {
        "console_time": console or "",
        "result_time": r_time,
        "result_status": r_status,
        "result_delta_seconds": delta_seconds,
        "result_delta_better": delta_better,
    }


def heat_official(lanes):
    """True once every lane with a swimmer has an official time or status.

    An empty lane has nothing to validate. A heat with no swimmers is never
    official: there is nothing to compare (`S-23`).
    """
    swum = [lane for lane in lanes if lane.get("name")]
    return bool(swum) and all(
        lane.get("result_time") or lane.get("result_status") for lane in swum
    )
