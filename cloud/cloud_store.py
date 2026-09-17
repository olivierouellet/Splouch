"""The meets this relay is serving, live and retained.

Two dicts and the lock that guards them:

* ``_meets`` — meets with a Pi currently connected. Pure memory; a restart loses
  them and the relays reconnect.
* ``_retained`` — the snapshot a meet leaves behind when its console disconnects,
  so the schedule, picker image and icon keep serving until the meet expires.
  Persisted, one small metadata file per meet plus separate files for the big
  fields, so a write touches only the meet that changed.

Both are bound once here and only ever mutated in place, never reassigned — which
is what lets ``cloud_server`` import the objects themselves and keep its existing
``with _lock:`` blocks working unchanged.

The lock is a plain ``threading.Lock`` rather than an async one on purpose: every
critical section here is a few dict operations with no ``await`` inside, and the
blocking file I/O around them is pushed to a thread by the callers
(``run_in_threadpool``). See notes/async_architecture.md.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import threading

import cloud_paths
from cloud_paths import atomic_write


# _meets: meet_id -> {
#   relay_key, relay_sid, organizer, name, location, sport, meet_date,
#   settings, connected_at, clock_at,
#   last_scoreboard, last_results, last_next_heats, schedule_data
# }
# _retained: meet_id -> persisted snapshot that outlives the relay connection, so
#   a meet keeps showing (schedule, picker image, icon) after the console
#   disconnects, until it expires. Persisted per meet under RETAINED_DIR. Fields:
#   organizer, relay_key, name, location, sport, app_window_title, meet_date,
#   settings, schedule_data, last_seen (iso), expires_at (iso or None while live).
_meets      = {}
_relay_sids = {}   # relay connection id -> meet_id
_lock       = threading.Lock()

# Fields copied from a live meet into its retained snapshot.
_RETAINED_FIELDS = ('organizer', 'relay_key', 'name', 'location', 'sport',
                    'app_window_title', 'meet_date', 'settings', 'schedule_data')

# The retained store is persisted as one small metadata file per meet plus
# separate files for the big fields — so a persist writes only the meet that
# changed (not all 30), and the base64 logo/background and the full start list
# never sit in the metadata JSON that gets rewritten on every register. In memory
# `_retained[meet_id]` still holds the whole record (images + schedule),
# reassembled on load, so every read/serve route is unchanged. Files per meet:
#   <id>.json            metadata + settings MINUS the two *_b64 images
#   <id>.schedule.json   schedule_data (start list)
#   <id>.icon / .picker  the home-icon / picker-image base64 strings
# See info/async_architecture.md ("Scaling the cloud persistence").
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')   # meet id -> safe filename



def _meet_file(meet_id, suffix):
    return os.path.join(cloud_paths.RETAINED_DIR, meet_id + suffix)



def _write_blob(path, text):
    """Write a blob file, or remove it when the value is empty."""
    if text:
        atomic_write(path, text)
    elif os.path.exists(path):
        os.remove(path)


def _split_record(rec):
    """Split a full retained record into (meta, schedule, icon_b64, picker_b64).

    `meta` is a shallow copy safe to serialize — the big fields are pulled out of
    it, not out of the shared record."""
    meta     = dict(rec)
    schedule = meta.pop('schedule_data', None)
    settings = dict(meta.get('settings') or {})
    icon     = settings.pop('home_icon_b64', '')
    picker   = settings.pop('picker_image_b64', '')
    meta['settings'] = settings
    return meta, schedule, icon, picker


def _write_meet_files(meet_id, rec, write_schedule, write_images):
    """Persist one meet's per-file record. Blocking (disk I/O) — call off the
    loop. The metadata file is always written; the big blobs only when the event
    that changed them asks (register -> images, schedule_snapshot -> schedule), so
    an unchanged blob isn't rewritten on every reconnect."""
    if not _ID_RE.match(meet_id or ''):
        return
    meta, schedule, icon, picker = _split_record(rec)
    atomic_write(_meet_file(meet_id, '.json'), json.dumps(meta, indent=2))
    if write_schedule:
        _write_blob(_meet_file(meet_id, '.schedule.json'),
                    json.dumps(schedule) if schedule else '')
    if write_images:
        _write_blob(_meet_file(meet_id, '.icon'),   icon)
        _write_blob(_meet_file(meet_id, '.picker'), picker)


def _delete_meet_files(meet_id):
    if not _ID_RE.match(meet_id or ''):
        return
    for suffix in ('.json', '.schedule.json', '.icon', '.picker'):
        try:
            os.remove(_meet_file(meet_id, suffix))
        except OSError:
            pass


def _load_retained():
    """Load every per-meet file back into one in-memory dict of full records."""
    os.makedirs(cloud_paths.RETAINED_DIR, exist_ok=True)
    store = {}
    for path in glob.glob(os.path.join(cloud_paths.RETAINED_DIR, '*.json')):
        if path.endswith('.schedule.json'):
            continue
        mid = os.path.basename(path)[:-len('.json')]
        try:
            with open(path) as f:
                rec = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        sp = _meet_file(mid, '.schedule.json')
        if os.path.exists(sp):
            try:
                with open(sp) as f:
                    rec['schedule_data'] = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        settings = rec.setdefault('settings', {})
        for suffix, field in (('.icon', 'home_icon_b64'), ('.picker', 'picker_image_b64')):
            bp = _meet_file(mid, suffix)
            if os.path.exists(bp):
                try:
                    with open(bp) as f:
                        settings[field] = f.read()
                except OSError:
                    pass
        store[mid] = rec
    return store


_retained = _load_retained()


def _persist_meet_mem(meet_id, meet):
    """In-memory write-through of a live meet into the retained store — no disk.
    Caller holds _lock."""
    snap = _retained.get(meet_id, {})
    snap.update({k: meet.get(k) for k in _RETAINED_FIELDS})
    snap['last_seen']   = datetime.datetime.now().isoformat(timespec='seconds')
    snap['expires_at']  = None   # live — never expires while connected
    _retained[meet_id] = snap


def _record_copy_locked(meet_id):
    """Shallow copy of a retained record, to serialize off the lock. Its big
    fields (settings, schedule_data) are rebound wholesale, never mutated in
    place, so sharing their references with the writer thread is safe."""
    return dict(_retained[meet_id])


def _compute_expiry(meet_date, when=None):
    """Midnight after the final session date, or after `when` if no meet date."""
    when = when or datetime.datetime.now()
    base = None
    if meet_date:
        try:
            base = datetime.date.fromisoformat(meet_date)
        except ValueError:
            base = None
    if base is None:
        base = when.date()
    return datetime.datetime.combine(base, datetime.time.min) + datetime.timedelta(days=1)


def _meet_id_for(key, meet_uid):
    """Deterministic cloud meet id for a (relay key, meet_uid) pair.

    Lets one relay key publish several meets — e.g. a meet split across days into
    separate LENEX files — each landing on its own stable picker card and
    reattaching on reload.
    """
    return hashlib.sha256(f'{key}:{meet_uid}'.encode()).hexdigest()[:11]


def _retire_mem(meet_id):
    """Move a live meet into the retained store with an expiry, in memory only —
    no disk. Caller holds _lock."""
    meet = _meets.pop(meet_id, None)
    if not meet:
        return
    snap = _retained.get(meet_id, {})
    snap.update({k: meet.get(k) for k in _RETAINED_FIELDS})
    snap['last_seen']  = datetime.datetime.now().isoformat(timespec='seconds')
    snap['expires_at'] = _compute_expiry(meet.get('meet_date', '')).isoformat(timespec='seconds')
    _retained[meet_id] = snap


def _sweep_expired():
    """Drop retained meets past their expiry. Live meets are never swept."""
    now = datetime.datetime.now()
    with _lock:
        expired = []
        for meet_id in list(_retained):
            if meet_id in _meets:
                continue  # still connected — keep visible regardless of expiry
            exp = _retained[meet_id].get('expires_at')
            if exp and datetime.datetime.fromisoformat(exp) <= now:
                del _retained[meet_id]
                expired.append(meet_id)
    for meet_id in expired:
        _delete_meet_files(meet_id)


def _get_meet(meet_id):
    """Live meet if connected, else its retained snapshot, else None.

    Caller holds _lock.
    """
    return _meets.get(meet_id) or _retained.get(meet_id)


def _merged_meets():
    """meet_id -> meet dict for all live + retained meets, live preferred.

    Caller holds _lock.
    """
    merged = dict(_retained)
    merged.update(_meets)
    return merged
