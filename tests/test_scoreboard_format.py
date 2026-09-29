"""Delta formatting for the Qt display.

The point of these tests is agreement: `scoreboard.format.fmt_delta` must produce
the same string the browser scoreboard shows for the same swimmer. The browser
gets a pre-rendered HTML blob from `meet_data._delta_html`; the Qt display gets
the structured seconds and formats them itself, so the two formatters can drift
apart silently. `test_matches_the_server_formatter` is what stops that.

Qt-free by design — `scoreboard.format` imports nothing from PySide6, so this runs
in CI without the `scoreboard` extra installed.
"""

import os
import re
import sys

import pytest

from scoreboard.format import fmt_clock, fmt_delta, parse_clock


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (-0.46, "-0.46"),  # faster than seed
        (1.12, "+1.12"),  # slower than seed
        (0.0, "+0.00"),  # exactly on seed — signed '+', matching the server
        (-0.01, "-0.01"),  # smallest representable gap
        (59.99, "+59.99"),  # just under the minute rollover
        (-62.5, "-1:02.50"),  # past a minute — switches to m:ss.hh
        (62.5, "+1:02.50"),
        (-600.0, "-10:00.00"),  # ten minutes off seed (a badly wrong seed time)
    ],
)
def test_formats_like_the_browser(seconds, expected):
    assert fmt_delta(seconds) == expected


@pytest.mark.parametrize("value", [None, "", "n/a", object()])
def test_missing_or_unparseable_delta_renders_empty(value):
    """No seed time means an empty cell, never '0.00' or a crash."""
    assert fmt_delta(value) == ""


def test_accepts_a_numeric_string():
    """JSON round-trips can hand us a string; treat it as the number it is."""
    assert fmt_delta("-0.46") == "-0.46"


def test_matches_the_server_formatter():
    """Cross-check against the server's own HTML formatter over a wide range.

    Both sides derive from the same integer hundredths, so the text inside the
    server's <span> must equal what the Qt display draws — for every delta, not
    just the ones someone thought to write a case for.
    """
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"
        ),
    )
    from meet_data import _delta_html

    for hundredths in list(range(-70000, 70001, 37)):
        html = _delta_html(hundredths)
        server_text = re.sub(r"<[^>]+>", "", html)
        assert fmt_delta(hundredths / 100.0) == server_text, (
            f"diverged at {hundredths} hundredths: {html}"
        )


# ── Race clock ─────────────────────────────────────────────────────────────────
# Mirrors parseHundredths/formatHundredths in the browser templates. The Qt board
# re-bases on each `running_time` frame and interpolates in between, so these two
# have to agree with the console's own formatting or the clock would jump on every
# console update.


@pytest.mark.parametrize(
    "text, hundredths",
    [
        ("0.00", 0),
        ("5.23", 523),
        ("28.41", 2841),
        ("59.99", 5999),
        ("1:00.00", 6000),
        ("1:05.23", 6523),
        ("12:34.56", 75456),
    ],
)
def test_parse_clock(text, hundredths):
    assert parse_clock(text) == hundredths


@pytest.mark.parametrize(
    "hundredths, text",
    [
        (0, "0.00"),
        (523, "5.23"),
        (5999, "59.99"),
        (6000, "1:00.00"),
        (6523, "1:05.23"),
        (75456, "12:34.56"),
    ],
)
def test_fmt_clock(hundredths, text):
    """Seconds are padded only once there is a minutes part — `5.23`, `1:05.23`."""
    assert fmt_clock(hundredths) == text


def test_clock_round_trips():
    for hundredths in range(0, 200000, 7):
        assert parse_clock(fmt_clock(hundredths)) == hundredths


@pytest.mark.parametrize("junk", [None, "", "  ", "abc", "1:2:3.44", "12.3", "12.345"])
def test_unparseable_clock_reads_as_none(junk):
    """The caller falls back to showing the console's raw string, not a crash."""
    assert parse_clock(junk) is None


def test_negative_clock_is_clamped():
    """A clock cannot run backwards; guard the interpolation arithmetic."""
    assert fmt_clock(-1) == "0.00"
