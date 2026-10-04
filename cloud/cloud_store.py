"""The meets this worker is serving.

* ``_meets`` — meets whose Pi is connected to this worker. Pure memory; a restart
  loses them and the relays reconnect.
* Retained meets — a meet whose console disconnected, still served until it
  expires — are not kept here. They live in the control plane's registry; a page
  for one fetches its record through ``cloud_node`` (cached briefly).

The lock is a plain ``threading.Lock`` rather than an async one on purpose: every
critical section here is a few dict operations with no ``await`` inside, and the
blocking calls around them are pushed to a thread by the callers
(``run_in_threadpool``). See docs/architecture/async-architecture.md.
"""

import threading

import cloud_node

# _meets: meet_id -> a dict of relay_key, relay_sid, organizer, name, location,
#   sport, app_window_title, meet_date, settings, connected_at, clock_at,
#   last_scoreboard, last_results, last_next_heats and schedule_data.
_meets = {}
_relay_sids = {}  # relay connection id -> meet_id
_lock = threading.Lock()

# Fields a retired meet's record keeps, so its pages serve without a fetch.
_RECORD_FIELDS = (
    "organizer",
    "relay_key",
    "name",
    "location",
    "sport",
    "app_window_title",
    "meet_date",
    "settings",
    "schedule_data",
    "connected_at",
)


def _retire_mem(meet_id):
    """Drop a live meet from memory, keeping its record in the page cache.

    Caller holds _lock. The control plane is told separately (``cloud_node.retire``,
    off the loop): this only stops the worker treating the meet as live.
    """
    meet = _meets.pop(meet_id, None)
    if meet:
        record = {k: meet.get(k) for k in _RECORD_FIELDS}
        record["live"] = False
        cloud_node.remember(meet_id, record)


def _get_meet(meet_id):
    """Live meet if connected here, else None. Caller holds _lock."""
    return _meets.get(meet_id)


def meet_for(meet_id):
    """Live meet if connected here, else its record from the control plane, else None.

    Blocking (may fetch) — call from a threadpool route, or wrap in
    ``run_in_threadpool`` on the loop.
    """
    with _lock:
        meet = _meets.get(meet_id)
    return meet or cloud_node.fetch_meet(meet_id)
