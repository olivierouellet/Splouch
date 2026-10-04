"""The attendance id travels from the picker to a meet page on another host
(docs/app.md `C-10`) — in the fragment, never the query.

A query string reaches the server's logs, rides along in a `Referer`, and stays in
any link the spectator shares; a fragment does none of that. The meet page has to
take it before its frames join, and then drop it from the address bar.
"""

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_picker_hands_it_over_only_across_hosts_and_in_the_fragment():
    page = read("cloud", "templates", "picker.html")
    script = page[page.index("docs/app.md `C-10`") :]
    assert "url.origin === location.origin) return" in script
    assert "url.hash = 'vid=' +" in script
    assert "vid=" not in re.sub(r"url\.hash = 'vid=' \+", "", script.split("})();")[0])


def test_the_shell_takes_it_before_its_frames_and_clears_it():
    shell = read("shared", "templates", "mobile.html")
    take = shell.index("location.hash")
    assert take < shell.index("<iframe"), "a frame could join before the id is stored"
    assert (
        "history.replaceState(null, '', location.pathname + location.search)" in shell
    )
