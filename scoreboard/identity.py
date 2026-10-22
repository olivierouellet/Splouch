"""This display's own ID — what the server keys a TV's settings by.

A hostname will not do: TV Pis are usually flashed from one image and all answer
to the same name, and the connection the server sees is gone at every reconnect.
So the first launch makes up a UUID and keeps it in ``~/.config/splouch/``, next
to ``scoreboard.env`` — config rather than cache, because losing it makes the
server forget the TV's name and view.

Cloning a configured SD card clones the ID too. The server notices two
connections with one ID and says so in Settings → Displays → TVs; deleting the
file on one of them and restarting it gives it a new one.

Qt-free, so it can be tested without PySide6.
"""

import contextlib
import os
import re
import uuid

_VALID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def id_path() -> str:
    """Where the ID lives. Read at call time, so a test can move it."""
    return os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
        "splouch",
        "display-id",
    )


def device_id() -> str:
    """This display's ID, made and saved on first call.

    A file that is missing, unreadable or holds anything but a UUID is replaced.
    If it cannot be written the ID still serves this run — the TV then shows up
    as a new one after a restart, which is better than not showing at all.
    """
    path = id_path()
    with contextlib.suppress(OSError):
        with open(path, encoding="utf-8") as f:
            found = f.read().strip().lower()
        if _VALID.match(found):
            return found
    new = str(uuid.uuid4())
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new + "\n")
        os.replace(tmp, path)
    return new
