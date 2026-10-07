"""The states and provinces a client names in full (`app.md` `P-01`, `P-21`).

`shared/regions/subdivisions.json` is the master; the iOS and Android apps carry
verbatim copies. What this file guards: the shape the apps decode, that no
spelling — code, name or alias, folded as `S-09` folds — names two subdivisions of
one country, or the filter would merge them, and that a name in the reader's
language is one of its subdivision's spellings, so a meet sent with it matches.
"""

import json
from pathlib import Path

import pytest

from cloud.cloud_follows import fold

PATH = (
    Path(__file__).resolve().parent.parent / "shared" / "regions" / "subdivisions.json"
)
FILE = json.loads(PATH.read_text(encoding="utf-8"))
DATA = FILE["countries"]
NAMES = FILE["names"]


@pytest.mark.parametrize("country", sorted(DATA))
def test_each_subdivision_has_a_name(country):
    assert len(country) == 2 and country.isupper()
    for code, spellings in DATA[country].items():
        assert code.isupper() and code.isalnum(), code
        assert spellings and all(isinstance(s, str) and s.strip() for s in spellings), (
            code
        )


@pytest.mark.parametrize("country", sorted(DATA))
def test_no_spelling_names_two_subdivisions(country):
    seen = {}
    for code, spellings in DATA[country].items():
        for s in {fold(code), *(fold(x) for x in spellings)}:
            assert s not in seen, f"{country}: {s!r} is both {seen[s]} and {code}"
            seen[s] = code


def test_quebec_keeps_its_accent():
    assert DATA["CA"]["QC"][0] == "Québec"


@pytest.mark.parametrize("country", sorted(NAMES))
def test_each_name_in_a_language_is_a_spelling(country):
    for lang, names in NAMES[country].items():
        assert lang in ("en", "fr", "es"), lang
        for code, name in names.items():
            spellings = DATA[country][code]
            assert name in spellings, f"{country}-{code}: {name!r} is not a spelling"
            assert name != spellings[0], f"{country}-{code}: {lang} repeats the name"


def test_canada_is_named_in_french():
    assert NAMES["CA"]["fr"]["BC"] == "Colombie-Britannique"
    assert set(NAMES) == {"CA"} and set(NAMES["CA"]) == {"fr"}
