"""OmniSport 2000: RTD over Ethernet (UDP broadcast) and the J6 Results port set to CTS.

ED-13312: consoles from June 2013 send RTD over their ETHERNET jack as UDP
broadcasts, standard port 21000-21003 (OMNI1-4) or 3002; J6 set to CTS is "a
unidirectional protocol, utilizing the standard CTS scoreboard information".
"""

import socket
import subprocess
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
    monkeypatch.setattr(worker, "_eth_listen_addresses", lambda: ["127.0.0.1"])
    # As root (CI's container) SO_BINDTODEVICE holds, and eth0 never sees loopback.
    monkeypatch.setattr(worker, "_UDP_IFACE", "lo")
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


def _ip_reports(monkeypatch, stdout):
    def run(cmd, **_):
        assert cmd[-2:] == ["dev", "eth0"]
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(worker.subprocess, "run", run)


def test_the_listener_binds_the_wired_port_not_every_interface(monkeypatch):
    _ip_reports(
        monkeypatch,
        '[{"ifname":"eth0","addr_info":[{"family":"inet",'
        '"local":"192.168.0.20","prefixlen":24}]}]',
    )
    assert worker._eth_listen_addresses() == [
        "192.168.0.20",
        "192.168.0.255",
        "255.255.255.255",
    ]


def test_no_wired_address_means_nothing_to_bind(monkeypatch):
    _ip_reports(monkeypatch, '[{"ifname":"eth0","addr_info":[]}]')
    assert worker._eth_listen_addresses() == []
    _ip_reports(monkeypatch, "")  # no eth0 at all: `ip` prints nothing
    assert worker._eth_listen_addresses() == []


def test_the_worker_reports_a_missing_wired_address(monkeypatch):
    statuses: list[tuple] = []

    def status(*a):
        statuses.append(a)
        state._worker_gen = 2  # one attempt is enough

    monkeypatch.setattr(worker, "_set_serial_status", status)
    monkeypatch.setattr(worker, "_eth_listen_addresses", list)
    monkeypatch.setattr(state, "_decoder", Omnisport2000Decoder({"transport": "udp"}))
    monkeypatch.setattr(state, "_worker_gen", 1)
    worker._run_live_udp(1)
    assert ("error", "no IPv4 address on eth0") in statuses


# ── Recording datagram boundaries in a .raw ────────────────────────────────────


def test_raw_recording_marks_each_datagram(monkeypatch, tmp_path):
    import io

    out = io.StringIO()
    monkeypatch.setattr(state, "_record_handle", out)
    monkeypatch.setattr(state, "_record_raw", True)
    monkeypatch.setattr(state, "_record_raw_count", 0)
    worker._record_raw_boundary()  # nothing recorded yet: no blank line
    for c in b"\x16\x01":
        worker._record_raw_byte(c)
    worker._record_raw_boundary()
    worker._record_raw_boundary()  # twice in a row: still one
    for c in b"\x16":
        worker._record_raw_byte(c)
    assert out.getvalue() == "16 01\n\n16"


def test_replay_flushes_at_a_datagram_boundary(monkeypatch, tmp_path):
    """Two datagrams with no SYN in the second: the blank line still splits them."""
    path = tmp_path / "udp.raw"
    path.write_text("16 01 02\n\n41 42\n")
    packets: list[bytes] = []

    def handle(buf):
        packets.append(bytes(buf))
        if len(packets) == 2:
            state._worker_gen += 1  # a .raw loops: stop after one pass

    monkeypatch.setattr(worker, "_handle_packet", handle)
    monkeypatch.setattr(state, "_decoder", Omnisport2000Decoder({}))
    monkeypatch.setattr(state, "_worker_gen", 1)
    worker._play_recording(str(path), 1)
    assert packets == [b"\x16\x01\x02", b"AB"]


def test_old_raw_files_play_as_before(monkeypatch, tmp_path):
    path = tmp_path / "old.raw"
    path.write_text("16 01 02 16\n03 04\n")
    packets: list[bytes] = []

    def handle(buf):
        packets.append(bytes(buf))
        if len(packets) == 2:
            state._worker_gen += 1

    monkeypatch.setattr(worker, "_handle_packet", handle)
    monkeypatch.setattr(state, "_decoder", Omnisport2000Decoder({}))
    monkeypatch.setattr(state, "_worker_gen", 1)
    worker._play_recording(str(path), 1)
    assert packets == [b"\x16\x01\x02", b"\x16\x03\x04"]
