"""The Ethernet static-IP form: the server model and the page's check agree.

Settings → Network sends an address, prefix, router and optional DNS. The router is
required — without it eth0 has no route out and the Cloud relay drops. The page
checks the form before sending (`_ethFormError` in settings.js) so the error comes
out localized; the `EthIP` model checks again on the server. Both run the same
cases here, so the two rule sets can't drift apart.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from jsc import HAS_JS_ENGINE, js_argv
from routes.network import EthIP

REPO = Path(__file__).resolve().parent.parent
SETTINGS_JS = REPO / "shared" / "static" / "js" / "settings.js"

# Each case: address, prefix, router, DNS, and whether it should pass.
CASES = [
    ("192.168.1.50", 24, "192.168.1.1", "", True),
    ("192.168.1.50", 24, "192.168.1.1", "1.1.1.1", True),
    ("10.0.5.9", 16, "10.0.0.1", "", True),
    ("192.168.1.50", 24, "", "", False),  # router required
    ("192.168.1.50", 24, "192.168.2.1", "", False),  # router outside the subnet
    ("192.168.1.50", 24, "192.168.1.50", "", False),  # router is the Pi
    ("192.168.1.0", 24, "192.168.1.1", "", False),  # network address
    ("192.168.1.255", 24, "192.168.1.1", "", False),  # broadcast address
    ("300.1.1.1", 24, "300.1.1.2", "", False),
    ("192.168.1.50", 31, "192.168.1.51", "", False),  # prefix out of 8–30
    ("192.168.1.50", 24, "192.168.1.1", "dns.example", False),
]


def _server_ok(ip, prefix, gateway, dns):
    try:
        EthIP(ip=ip, prefix=prefix, gateway=gateway or None, dns=dns or None)
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize(("ip", "prefix", "gateway", "dns", "valid"), CASES)
def test_server_model(ip, prefix, gateway, dns, valid):
    assert _server_ok(ip, prefix, gateway, dns) is valid


def _shipped(name):
    src = SETTINGS_JS.read_text(encoding="utf-8")
    m = re.search(rf"^function {name}\(.*?^\}}", src, re.DOTALL | re.MULTILINE)
    assert m, name
    return m.group(0)


@pytest.mark.skipif(not HAS_JS_ENGINE, reason="needs a JS engine to run settings.js")
def test_page_check_agrees_with_server():
    js = "\n".join(
        [
            (
                "var T = {js_enter_ip: 'E', js_eth_bad_ip: 'I',"
                " js_eth_bad_gateway: 'G', js_eth_bad_dns: 'D'};"
            ),
            _shipped("_ipv4ToInt"),
            _shipped("_ethFormError"),
            f"var c = {json.dumps([c[:4] for c in CASES])};",
            (
                "JSON.stringify(c.map(function (x) {"
                " return _ethFormError(x[0], String(x[1]), x[2], x[3]); }))"
            ),
        ]
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(js)
        path = fh.name
    try:
        res = subprocess.run(js_argv(path), capture_output=True, text=True, check=False)
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stderr
    errors = json.loads(res.stdout)
    for case, err in zip(CASES, errors, strict=True):
        assert (err == "") is case[4], (case, err)
