"""`/` sends a phone to the phone shell and everything else to the board.

`/live` is the TV board and clips on a phone held upright; `/mobile` is the page
built for one. The kiosk and the Qt board must keep landing on `/live`.
"""

import os
import sys

import pytest
from starlette.requests import Request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "server"))

from routes.scoreboard import route_index  # noqa: E402


def _request(ua):
    headers = [(b"user-agent", ua.encode())] if ua is not None else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


@pytest.mark.parametrize(
    "ua, target",
    [
        (
            (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
            ),
            "/mobile",
        ),
        (
            (
                "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
            ),
            "/mobile",
        ),
        (
            (
                "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
            "/live",
        ),
        (
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"
            ),
            "/live",
        ),
        (None, "/live"),
    ],
    ids=["iphone", "android", "pi-kiosk", "mac-or-ipad", "no-ua"],
)
def test_index_routes_by_device(ua, target):
    assert route_index(_request(ua)).headers["location"] == target
