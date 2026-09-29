"""On-disk cache of the last ``GET /config`` the server answered with.

Solves a chicken-and-egg problem. The waiting screen has to be drawn *before*
``/config` arrives — that is its entire purpose — but ``/config`` is what carries
the meet's language, theme and lane count. Without a cache the kiosk would show
an untranslated, unthemed screen for however long the server takes to boot, every
single time.

With it, only the very first boot after installation looks generic. From then on
the display comes up in the right language and colours even if the server never
answers at all.

Qt-free on purpose (see ``scoreboard/README.md``): this is `json` and `os`, and
keeping it out of ``client.py`` lets CI test it without PySide6.
"""

import json
import os

_CACHE_PATH = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "splouch",
    "scoreboard-config.json",
)


def cache_path() -> str:
    """Where the cache lives — exposed for diagnostics and tests."""
    return _CACHE_PATH


def load_cached_config():
    """The last config seen, or ``None``.

    ``None`` covers every failure the same way — no file yet, unreadable, corrupt,
    or holding something that is not a config object. The caller falls back to
    built-in defaults, which is always safe; there is no failure here worth
    crashing a scoreboard over.
    """
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_cached_config(raw: dict) -> bool:
    """Persist *raw*. Returns whether anything was actually written.

    Two deliberate details:

    * **Atomic.** Written to a temp file and moved into place, so a power cut
      mid-write cannot leave a truncated cache that poisons every later boot. The
      kiosk is a Pi somebody switches off at the wall.
    * **Skips unchanged writes.** ``/config`` is re-fetched on every reconnect and
      every ``reload``; rewriting an identical file each time is pure SD-card wear
      for no benefit.
    """
    try:
        if load_cached_config() == raw:
            return False
        payload = json.dumps(raw, sort_keys=True)
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, _CACHE_PATH)
        return True
    except Exception:
        return False
