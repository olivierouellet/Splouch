"""Where an organizer is based, for both servers, from one file.

The control plane records each organizer's country and state/province and assigns
them a region — the set of servers that carry their meets
(docs/architecture/scaling.md). The admin sets it when creating the key; the
organizer can correct the country and state/province from the Pi's Cloud tab. Both
offer the same list, so it lives here.

ISO 3166-1 alpha-2 codes only: the names are drawn by the browser
(`Intl.DisplayNames`) in the reader's language, so no locale file carries 34
country names three times over. A state or province is its ISO 3166-2 code,
picked from `shared/regions/subdivisions.json` — the file the apps carry copies
of — for the countries it lists, and none elsewhere.
"""

import json
import os

from splouch_fold import fold

# The regions a deployment can have. Their display names are locale strings
# (`region_<code>`), never stored here.
REGIONS = ("ca", "us", "mx", "eu")

# Country → the region an organizer there is suggested. The admin may still pick
# another; nothing assigns a region from this on its own.
_EUROPE = [
    "AT",
    "BE",
    "BG",
    "CH",
    "CY",
    "CZ",
    "DE",
    "DK",
    "EE",
    "ES",
    "FI",
    "FR",
    "GB",
    "GR",
    "HR",
    "HU",
    "IE",
    "IS",
    "IT",
    "LI",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "NO",
    "PL",
    "PT",
    "RO",
    "SE",
    "SI",
    "SK",
]
COUNTRIES = {"CA": "ca", "US": "us", "MX": "mx", **dict.fromkeys(_EUROPE, "eu")}


def _load_subdivisions():
    """`subdivisions.json`: beside this file in the cloud image, in
    `shared/regions/` in a checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (
        os.path.join(here, "subdivisions.json"),
        os.path.join(here, os.pardir, "regions", "subdivisions.json"),
    ):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    return {"countries": {}, "names": {}}


_FILE = _load_subdivisions()
# Country → subdivision code → [its own name, other spellings…].
SUBDIVISIONS = _FILE["countries"]
_NAMES = _FILE.get("names", {})
# Country → folded code or spelling → code.
_SPELLINGS = {
    country: {fold(s): code for code, names in subs.items() for s in (code, *names)}
    for country, subs in SUBDIVISIONS.items()
}


def province_code(country, province):
    """The subdivision code *province* spells in *country* — by code or by any
    listed spelling, folded (`app.md` `P-01`) — else *province* as sent."""
    province = str(province or "").strip()
    return _SPELLINGS.get(country, {}).get(fold(province), province)


def province_name(country, code, lang=""):
    """A subdivision's name for a reader of *lang*: its own, or the one in the
    reader's language where the country has two official ones."""
    names = SUBDIVISIONS.get(country, {}).get(code)
    if not names:
        return code
    return _NAMES.get(country, {}).get(lang, {}).get(code, names[0])


def province_choices(lang=""):
    """Country → [(code, name)] by name, for a state/province drop-down."""
    return {
        country: sorted(
            ((code, province_name(country, code, lang)) for code in subs),
            key=lambda c: fold(c[1]),
        )
        for country, subs in SUBDIVISIONS.items()
    }


def clean_location(country, province):
    """(country, province) as stored: known codes or ''. A province is one of the
    country's subdivisions, given by code or by any spelling of it."""
    country = str(country or "").strip().upper()
    if country not in COUNTRIES:
        return "", ""
    code = province_code(country, province)
    return country, code if code in SUBDIVISIONS.get(country, {}) else ""
