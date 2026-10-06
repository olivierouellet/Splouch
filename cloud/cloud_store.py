"""The meets this worker is serving.

* ``_meets`` — meets whose Pi is connected to this worker. Pure memory; a restart
  loses them and the relays reconnect.
* Everything else a meet's pages need — its start list, settings and icon, live or
  finished — is in the node's store (``cloud_meetstore``), on disk and shared by the
  node's workers, so it stays in the meet's region and survives a restart. A meet
  this node does not have is looked up on the control plane only to find where it
  lives (``cloud_node.fetch_meet``), and the visitor is sent there.

The lock is a plain ``threading.Lock`` rather than an async one on purpose: every
critical section here is a few dict operations with no ``await`` inside, and the
blocking calls around them are pushed to a thread by the callers
(``run_in_threadpool``). See docs/architecture/async-architecture.md.
"""

import threading

import cloud_meetstore
import cloud_node

# _meets: meet_id -> a dict of relay_key, relay_sid, organizer, name, location,
#   sport, app_window_title, meet_date, settings, connected_at, clock_at,
#   last_scoreboard, last_results, last_next_heats and schedule_data.
_meets = {}
_relay_sids = {}  # relay connection id -> meet_id
_lock = threading.Lock()

# What the node's store keeps of a meet: what its pages read. Never the relay key.
_RECORD_FIELDS = (
    "organizer",
    "name",
    "location",
    "sport",
    "app_window_title",
    "meet_date",
    "settings",
    "schedule_data",
    "connected_at",
)


def record_of(meet):
    """What the node's store keeps of a meet: the fields its pages read."""
    return {k: meet.get(k) for k in _RECORD_FIELDS}


def _retire_mem(meet_id):
    """Drop a live meet from memory; returns it, or None. Caller holds _lock.

    The caller then keeps it offline in the node's store and tells the control
    plane, both off the loop (``store_offline``, ``cloud_node.retire``).
    """
    return _meets.pop(meet_id, None)


def store_offline(meet_id, meet):
    """Keep a meet whose Pi left in the node's store, expiring as the control plane
    will — midnight after its last session day. Blocking."""
    expires = cloud_meetstore.compute_expiry(meet.get("meet_date", "")).isoformat()
    cloud_meetstore.save(meet_id, record_of(meet), expires=expires)


def _get_meet(meet_id):
    """Live meet if connected here, else None. Caller holds _lock."""
    return _meets.get(meet_id)


def meet_for(meet_id):
    """Live meet if connected here, else this node's stored record, else the control
    plane's card for it — which says where it lives, so the visitor can be sent
    there — else None.

    Blocking (reads the store, may fetch) — call from a threadpool route, or wrap in
    ``run_in_threadpool`` on the loop.
    """
    if not cloud_node.valid_meet_id(meet_id):
        return None  # from a query string or a socket frame: never a meet id
    with _lock:
        meet = _meets.get(meet_id)
    if meet:
        return meet
    stored = cloud_meetstore.get(meet_id)
    if stored and stored["live"]:
        # Live on another of this node's workers: its card says which, and the
        # visitor goes there. Without one (control plane unreachable, or the meet
        # since retired) the stored record is served as it stands, offline.
        card = cloud_node.fetch_meet(meet_id)
        if card and card.get("live"):
            return card
        return {**stored, "live": False}
    if stored:
        return stored
    return cloud_node.fetch_meet(meet_id)
