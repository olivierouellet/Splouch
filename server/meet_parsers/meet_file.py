"""Which meet files Splouch reads, and the parser for each.

A Hy-Tek CSV heat sheet loads into `HytekParser` (`state.load_event_info`); every
other format into a `LenexData` (`state.set_lenex`). Extensions are compared
lower-cased: Hy-Tek names its exports `HFILE001.HY3`, and the Pi's filesystem is
case-sensitive.
"""

import glob
import os

from meet_parsers.hy3_parser import load_hy3
from meet_parsers.lenex_parser import load_lenex

CSV = ".csv"
# Read into a `LenexData`, in the order the newest of them is picked at startup.
STRUCTURED = {".lxf": load_lenex, ".hy3": load_hy3}
EXTENSIONS = (CSV, *STRUCTURED)


def extension(path):
    return os.path.splitext(path)[1].lower()


def is_meet_file(path):
    return extension(path) in EXTENSIONS


def is_csv(path):
    return extension(path) == CSV


def load_structured(path):
    """A `.lxf` or `.hy3` meet file as a `LenexData`."""
    return STRUCTURED[extension(path)](path)


def meet_files(folder, extensions=EXTENSIONS):
    """Every meet file in *folder* with one of *extensions*, any case."""
    return [
        f
        for f in glob.glob(os.path.join(folder, "*"))
        if extension(f) in extensions and os.path.isfile(f)
    ]
