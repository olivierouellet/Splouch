"""`app.md` `S-09`'s fold, for both servers: the one the phones and the web page
use, so a name or a province spelled one way here matches it spelled another there.
"""

import unicodedata

# The 17 letters with no canonical decomposition.
_EXPAND = {
    "ß": "ss", "æ": "ae", "ð": "d", "ø": "o", "þ": "th", "đ": "d", "ħ": "h",
    "ı": "i", "ĳ": "ij", "ĸ": "k", "ŀ": "l", "ł": "l", "ŉ": "n", "ŋ": "n",
    "œ": "oe", "ŧ": "t", "ſ": "s",
}  # fmt: skip


def fold(s):
    """Lowercase, NFD, expand the 17, drop anything past ASCII. Spaces collapsed."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(_EXPAND.get(c, c) for c in s)
    s = "".join(c for c in s if ord(c) <= 0x7F)
    return " ".join(s.split())
