"""OmniSport 2000: RTD over Ethernet (UDP broadcast) and the J6 Results port set to CTS.

ED-13312: consoles from June 2013 send RTD over their ETHERNET jack as UDP
broadcasts, standard port 21000-21003 (OMNI1-4) or 3002; J6 set to CTS is "a
unidirectional protocol, utilizing the standard CTS scoreboard information".
"""

import socket
import threading
import time
from typing import cast

import state
import worker
from console_decoders import console_info_for, make_decoder, option_values
from console_decoders.cts_gen6 import CTSGen6Decoder
from console_decoders.omnisport_2000 import Omnisport2000Decoder


def _rtd(offset: int, data: str) -> bytes:
    body = [
        *b"00000000",
        0x01,
        *f"00421{offset:05d}".encode(),
        0x02,
        *data.encode(),
        0x04,
    ]
    return bytes([0x16, *body, *f"{sum(body) & 0xFF:02X}".encode(), 0x17])


def test_ethernet_entry_is_udp():
    info = console_info_for("dak_os2000_udp")
    assert info and info["transport"] == "udp"
    d = cast(Omnisport2000Decoder, make_decoder("dak_os2000_udp", {}))
    assert (d.transport, d.udp_port, d.line_base) == ("udp", 21000, 222)
    cfg = {
        "console_options": {
            "dak_os2000_udp": {"udp_port": "3002", "rtd_line_base": "200"}
        }
    }
    assert option_values("dak_os2000_udp", cfg)["udp_port"] == "3002"
    d = cast(Omnisport2000Decoder, make_decoder("dak_os2000_udp", cfg))
    assert (d.udp_port, d.line_base) == (3002, 200)


def test_serial_entries_stay_serial():
    assert make_decoder("dak_os2000_rtd", {}).transport == "serial"
    info = console_info_for("dak_os2000_rtd")
    assert info and info["transport"] == "serial"


def test_j6_cts_is_the_legacy_decoder():
    assert isinstance(make_decoder("dak_os2000_j6_cts", {}), CTSGen6Decoder)


def test_the_worker_listens_for_rtd_datagrams(monkeypatch):
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    packets: list[bytes] = []
    monkeypatch.setattr(worker, "_handle_packet", lambda b: packets.append(bytes(b)))
    monkeypatch.setattr(worker, "_set_serial_status", lambda *a: None)
    decoder = Omnisport2000Decoder({"transport": "udp", "udp_port": str(port)})
    monkeypatch.setattr(state, "_decoder", decoder)
    monkeypatch.setattr(state, "_worker_gen", 1)
    t = threading.Thread(target=worker._run_live_udp, args=(1,), daemon=True)
    t.start()

    frame = _rtd(0, "    0.1  ")
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    deadline = time.time() + 3
    while not packets and time.time() < deadline:
        sender.sendto(frame + frame, ("127.0.0.1", port))
        time.sleep(0.05)
    sender.close()
    state._worker_gen = 2
    t.join(2)
    assert packets[:2] == [frame, frame], "two frames in one datagram, split on SYN"
