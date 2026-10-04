"""Load test for the cloud relay: fake Pis publish, fake attendees watch.

Answers stage 0 of docs/architecture/scaling.md — how many attendees one relay
process carries before frames arrive late. Each fake Pi asks the cloud where to
publish (`POST /api/assign`), registers a meet on the worker socket it is given
with the ticket, and sends `update_scoreboard` frames; each fake attendee joins one
of those meets on `/ws/scoreboard` on that worker. The Pi stamps every frame with the time it was
sent, and because Pis and attendees live in this one process, the delay an
attendee measures is the relay's fan-out time plus the network, with no clock skew.

    uv run python tests/relay_load.py --url https://staging.example --key <relay key> \\
        --meets 20 --attendees 5000 --duration 120

Watch the server while it runs (`docker stats`, `htop`): the number that matters
is the attendee count at which p95 delay climbs past a few hundred milliseconds,
or the relay process pins one core at 100 %.

Run it against a staging relay, not production. Each fake meet is registered as a
real one (`loadtest-…` in the picker) and is kept as a retained meet afterwards —
delete them from `/admin`. One load generator tops out around 10–20k sockets; for
more, run several copies on other machines with distinct `--uid-prefix` values.
"""

import argparse
import asyncio
import contextlib
import json
import random
import resource
import statistics
import time
import urllib.request
from urllib.parse import urlparse

from websockets.asyncio.client import connect


class Stats:
    def __init__(self):
        self.connected = 0
        self.failed = 0
        self.dropped = 0
        self.frames = 0
        self.delays = []  # seconds, reset every report

    def window(self):
        delays, self.delays = self.delays, []
        frames, self.frames = self.frames, 0
        return frames, delays


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


def frame(seq, started):
    """A partial scoreboard of about the size a real heat produces."""
    lane = random.randint(1, 8)
    return {
        "lt_seq": seq,
        "lt_sent": time.time(),
        "running_time": f"{time.monotonic() - started:.1f}",
        f"lane_time{lane}": f"{random.randint(25, 59)}.{random.randint(0, 99):02d}",
        f"lane_place{lane}": str(random.randint(1, 8)),
        f"lane_splits{lane}": random.randint(0, 4),
        "current_event": "12",
        "current_heat": "3",
        "event_name": "Girls 11-12 100 LC Meter Freestyle",
    }


def assign(url, key, uid):
    """`POST /api/assign`, as a Pi asks it: the worker socket and a ticket."""
    req = urllib.request.Request(
        url.rstrip("/") + "/api/assign",
        data=json.dumps({"key": key, "meet_uid": uid}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


async def fake_pi(url, key, uid, rate, stop, ready):
    """Register one meet and publish frames at `rate` per second until `stop`."""
    a = await asyncio.to_thread(assign, url, key, uid)
    async with connect(a["relay_url"], max_size=None) as ws:
        register = {"key": key, "ticket": a["ticket"], "meet_uid": uid, "name": uid}
        await ws.send(json.dumps({"event": "register", "data": register}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("event") == "registered":
                # Attendees join on the worker the meet landed on.
                worker = urlparse(a["relay_url"])
                base = f"{worker.scheme}://{worker.netloc}"
                ready.set_result((msg["data"]["meet_id"], base))
                break
            if msg.get("event") == "rejected":
                ready.set_exception(RuntimeError(f"relay rejected: {msg['data']}"))
                return
        started, seq = time.monotonic(), 0
        while not stop.is_set():
            seq += 1
            payload = {"event": "update_scoreboard", "data": frame(seq, started)}
            await ws.send(json.dumps(payload))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), 1 / rate)


async def fake_attendee(base, meet_id, vid, stats, stop):
    try:
        async with connect(f"{base}/ws/scoreboard", max_size=None) as ws:
            await ws.send(
                json.dumps(
                    {"event": "join_meet", "data": {"meet_id": meet_id, "vid": vid}}
                )
            )
            stats.connected += 1
            try:
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 1)
                    except TimeoutError:
                        continue
                    data = json.loads(raw).get("data") or {}
                    sent = data.get("lt_sent") if isinstance(data, dict) else None
                    if sent is not None:
                        stats.frames += 1
                        stats.delays.append(time.time() - sent)
            finally:
                stats.connected -= 1
    except Exception:
        if stop.is_set():
            return
        stats.dropped += 1


async def report(stats, stop, every=1.0):
    print(
        f"{'t':>5} {'conn':>7} {'fail':>5} {'drop':>5} {'frames/s':>9} "
        f"{'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8}"
    )
    t0 = time.monotonic()
    worst_p95 = 0.0
    all_delays = []
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), every)
        frames, delays = stats.window()
        delays.sort()
        all_delays.extend(delays)
        p95 = pct(delays, 0.95) * 1000
        worst_p95 = max(worst_p95, p95)
        print(
            f"{time.monotonic() - t0:5.0f} {stats.connected:7d} {stats.failed:5d} "
            f"{stats.dropped:5d} {frames / every:9.0f} {pct(delays, 0.5) * 1000:8.0f} "
            f"{p95:8.0f} {pct(delays, 0.99) * 1000:8.0f}",
            flush=True,
        )
    all_delays.sort()
    if all_delays:
        print(
            f"\nwhole run: {len(all_delays)} frames, median "
            f"{statistics.median(all_delays) * 1000:.0f} ms, p95 "
            f"{pct(all_delays, 0.95) * 1000:.0f} ms, p99 "
            f"{pct(all_delays, 0.99) * 1000:.0f} ms, worst 1 s p95 {worst_p95:.0f} ms"
        )


def raise_fd_limit():
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = hard if hard != resource.RLIM_INFINITY else 1 << 20
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


async def main(args):
    fds = raise_fd_limit()
    if fds < args.attendees + args.meets + 64:
        print(f"warning: only {fds} file descriptors for {args.attendees} sockets")

    stop = asyncio.Event()
    stats = Stats()
    loop = asyncio.get_running_loop()

    readies = [loop.create_future() for _ in range(args.meets)]
    pis = [
        asyncio.create_task(
            fake_pi(
                args.url,
                args.key,
                f"{args.uid_prefix}-{i}",
                args.rate,
                stop,
                readies[i],
            )
        )
        for i in range(args.meets)
    ]
    meets = await asyncio.gather(*readies)
    print(
        f"{len(meets)} meets registered; ramping {args.attendees} attendees "
        f"at {args.ramp}/s"
    )

    reporter = asyncio.create_task(report(stats, stop))
    attendees = []
    for i in range(args.attendees):
        meet_id, base = meets[i % len(meets)]  # spread evenly over the meets
        attendees.append(
            asyncio.create_task(
                fake_attendee(base, meet_id, f"{args.uid_prefix}-v{i}", stats, stop)
            )
        )
        if (i + 1) % args.ramp == 0:
            await asyncio.sleep(1)

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), args.duration)
    stop.set()
    await asyncio.gather(*pis, *attendees, return_exceptions=True)
    await reporter


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--url", required=True, help="the cloud's base URL, e.g. https://host"
    )
    p.add_argument("--key", required=True, help="an active relay key from /admin")
    p.add_argument("--meets", type=int, default=10, help="fake Pis (default 10)")
    p.add_argument("--attendees", type=int, default=1000, help="total (default 1000)")
    p.add_argument(
        "--rate", type=float, default=2.0, help="frames per second per meet (default 2)"
    )
    p.add_argument(
        "--ramp",
        type=int,
        default=200,
        help="attendee connections opened per second (default 200)",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=60,
        help="seconds to hold after ramping (default 60)",
    )
    p.add_argument("--uid-prefix", default="loadtest", help="meet_uid prefix")
    asyncio.run(main(p.parse_args()))
