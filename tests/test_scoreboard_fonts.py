"""Font-name normalisation and the bundled-font inventory.

Qt-free: `_squash` is pure string work, and the inventory check is a directory
listing. The part that genuinely needs Qt — that each name resolves to a real
face — is exercised by the offscreen harness described in scoreboard/README.md.
"""

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scoreboard.fonts import _squash  # noqa: E402

FONT_DIR = os.path.join(REPO, "shared", "static", "fonts")

# What the server sends in `theme_fonts` -> the family name inside the font file.
# The two differ because a browser @font-face name is an arbitrary CSS label.
SERVER_NAME_TO_REAL = {
    "Overpass Mono": "Overpass Mono",
    "DSEG7Classic": "DSEG7 Classic",
    "DSEG14Classic": "DSEG14 Classic",
    "Orbitron": "Orbitron",
    "Roboto Mono": "Roboto Mono",
    "Share Tech Mono": "Share Tech Mono",
}


@pytest.mark.parametrize("server_name, real_name", SERVER_NAME_TO_REAL.items())
def test_server_font_names_squash_to_their_real_family(server_name, real_name):
    """The loose match must bridge every name the settings dropdown can produce."""
    assert _squash(server_name) == _squash(real_name)


def test_squash_ignores_spaces_hyphens_and_case():
    assert _squash("DSEG7 Classic") == "dseg7classic"
    assert _squash("Share-Tech Mono") == _squash("share tech mono")


def test_squash_keeps_genuinely_different_names_apart():
    """Normalisation must not be so loose that it matches the wrong font."""
    assert _squash("Roboto Mono") != _squash("Overpass Mono")
    assert _squash("DSEG7Classic") != _squash("DSEG14Classic")


@pytest.mark.parametrize("real_name", sorted(set(SERVER_NAME_TO_REAL.values())))
def test_every_family_ships_a_ttf_for_qt(real_name):
    """Qt cannot read woff2, so a browser-only face would fall back silently."""
    ttfs = [f for f in os.listdir(FONT_DIR) if f.lower().endswith(".ttf")]
    assert any(
        _squash(real_name) in _squash(os.path.splitext(f)[0].split("[")[0])
        for f in ttfs
    ), f"no TTF for {real_name} in {ttfs}"


def test_every_ttf_has_a_licence_file():
    """OFL requires the licence to travel with the fonts."""
    licences = os.listdir(os.path.join(FONT_DIR, "licenses"))
    assert licences, "licenses/ must not be empty"
    assert all(f.endswith(".txt") for f in licences)
    for stem in ("DSEG", "Orbitron", "OverpassMono", "RobotoMono", "ShareTechMono"):
        assert any(f.startswith(stem) for f in licences), f"no licence for {stem}"
