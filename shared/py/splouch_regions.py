"""Where an organizer is based, for both servers, from one file.

The control plane records each organizer's country and state/province and assigns
them a region — the set of servers that carry their meets
(docs/architecture/scaling.md). The admin sets it when creating the key; the
organizer can correct the country and state/province from the Pi's Cloud tab. Both
offer the same list, so it lives here.

ISO 3166-1 alpha-2 codes only: the names are drawn by the browser
(`Intl.DisplayNames`) in the reader's language, so no locale file carries 34
country names three times over.
"""

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

PROVINCE_MAX = 64


def clean_location(country, province):
    """(country, province) as stored: a known code or '', and bounded free text."""
    country = str(country or "").strip().upper()
    return (
        country if country in COUNTRIES else "",
        str(province or "").strip()[:PROVINCE_MAX],
    )
