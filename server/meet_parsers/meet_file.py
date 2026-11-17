"""Which meet files Splouch reads, and the parser for each.

A Hy-Tek CSV heat sheet loads into `HytekParser` (`state.load_event_info`); every
other format into a `LenexData` (`state.set_lenex`). Extensions are compared
lower-cased: Hy-Tek names its exports `HFILE001.HY3`, and the Pi's filesystem is
case-sensitive.
"""

import glob
import os
import zipfile
import zlib

from meet_parsers.hy3_parser import MAX_HY3_BYTES, load_hy3
from meet_parsers.lenex_parser import load_lenex

CSV = ".csv"
# Read into a `LenexData`, in the order the newest of them is picked at startup.
STRUCTURED = {".lxf": load_lenex, ".hy3": load_hy3}
EXTENSIONS = (CSV, *STRUCTURED)
# Uploaded only: Meet Manager zips its `.hy3` export, with a `.cl2` beside it. The
# `.hy3` inside is what is kept (`store`), so the meet folder never holds a zip.
ZIP = ".zip"
UPLOADS = (*EXTENSIONS, ZIP)


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


def is_upload(filename):
    """Can a meet file be uploaded under this name?"""
    return extension(filename) in UPLOADS


def stored_name(filename):
    """What an upload named *filename* is kept as: a zip as the `.hy3` inside it,
    named after the zip — Hy-Tek names every export's `HFILE001.HY3`."""
    base = os.path.basename(filename)
    stem, ext = os.path.splitext(base)
    return stem + ".hy3" if ext.lower() == ZIP else base


def store(tmp, dest, filename):
    """Move an upload saved at *tmp* to *dest*, unzipping it when *filename* is
    a zip. *tmp* is gone afterwards either way. Returns None, or why a zip is not
    a Hy-Tek export: no `.hy3` in it, several, one past `MAX_HY3_BYTES`, or a zip
    Python cannot read (encrypted, truncated, an unsupported compression).

    A fixed message rather than an exception, so the routes show the uploader a
    reason without passing an exception's text to the page."""
    if extension(filename) != ZIP:
        os.replace(tmp, dest)
        return None
    try:
        data, error = _hy3_in_zip(tmp)
    finally:
        os.remove(tmp)
    if error:
        return error
    with open(dest, "wb") as f:
        f.write(data)
    return None


def _hy3_in_zip(path):
    """`(data, None)`, or `(None, why)` for a zip that is not a Hy-Tek export."""
    try:
        with zipfile.ZipFile(path) as z:
            found = [
                i
                for i in z.infolist()
                if not i.is_dir() and extension(i.filename) == ".hy3"
            ]
            if len(found) != 1:
                return None, (
                    "no .hy3 file in the zip"
                    if not found
                    else "more than one .hy3 file in the zip"
                )
            # Read through the cap rather than trusting the header's size: a
            # zip bomb claims a small one.
            with z.open(found[0]) as f:
                data = f.read(MAX_HY3_BYTES + 1)
    except zipfile.BadZipFile:
        return None, "not a zip file"
    # RuntimeError: encrypted. NotImplementedError: a compression Python lacks.
    # zlib.error and EOFError: a damaged or truncated member.
    except (RuntimeError, NotImplementedError, zlib.error, EOFError, OSError):
        return None, "the zip could not be read"
    if len(data) > MAX_HY3_BYTES:
        return None, "the .hy3 in the zip is too large"
    return data, None
