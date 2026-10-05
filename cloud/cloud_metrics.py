"""What Prometheus reads from a worker and from the control plane: `/metrics`.

Counts only — how many meets, sockets, frames, how late the event loop runs — and
never a meet id, a visitor id, an IP or a name, in line with `/privacy`
(docs/architecture/scaling.md, *Monitoring*). The per-worker and per-node split comes
from the scrape target, not from a label here.

Never public: `/metrics` answers only a client on a private network — Prometheus,
scraping the containers over the Docker network (`cloud/monitoring/`). Behind Caddy
the client is the real visitor (uvicorn trusts Caddy's `X-Forwarded-For`), so a
request from the internet gets a 404 whatever route it came in by.

Event-loop lag is the number to watch on a worker. A worker is one Python process
on one core; when it saturates, timers fire late before its CPU graph looks alarming.
`lag_loop` measures exactly that: a one-second sleep, and how much later it woke.
"""

import asyncio
import ipaddress
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Info,
    ProcessCollector,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily

# One registry per app, so a worker's `/metrics` never runs the control plane's
# collectors (they read Postgres) when both are imported in one process (tests).
WORKER = CollectorRegistry()
CONTROL = CollectorRegistry()
for _registry in (WORKER, CONTROL):
    ProcessCollector(registry=_registry)  # CPU seconds, memory, open files

FRAMES = Counter("splouch_frames", "Frames received from Pi relays.", registry=WORKER)
SENDS = Counter("splouch_sends", "Frames sent to attendee sockets.", registry=WORKER)
CONTROL_ERRORS = Counter(
    "splouch_control_errors",
    "Calls to the control plane that failed.",
    registry=WORKER,
)
LOOP_LAG = Gauge(
    "splouch_event_loop_lag_seconds",
    "How late a one-second timer last fired on this process's event loop.",
    registry=WORKER,
)
CONTROL_LOOP_LAG = Gauge(
    "splouch_event_loop_lag_seconds",
    "How late a one-second timer last fired on this process's event loop.",
    registry=CONTROL,
)


async def lag_loop(gauge=LOOP_LAG, interval=1.0):
    """Measure event-loop lag into `gauge` for as long as the app runs."""
    while True:
        start = time.monotonic()
        await asyncio.sleep(interval)
        gauge.set(max(time.monotonic() - start - interval, 0.0))


def version_info(registry, role, version):
    Info("splouch", "The version this process runs.", registry=registry).info(
        {"role": role, "version": version}
    )


class Snapshot:
    """A collector that asks a callback for gauges at scrape time.

    `read()` returns `{(metric, label value or None): value}`; one family per
    metric name, labelled by `label` when the key carries a value.
    """

    def __init__(self, read, docs, label=None):
        self.read = read
        self.docs = docs
        self.label = label

    def collect(self):
        families = {}
        for (name, label_value), value in self.read().items():
            if name not in families:
                families[name] = GaugeMetricFamily(
                    name,
                    self.docs.get(name, name),
                    labels=[self.label] if self.label else [],
                )
            families[name].add_metric([label_value] if self.label else [], value)
        yield from families.values()


def body(registry):
    """The `/metrics` response for one app: (bytes, content type)."""
    return generate_latest(registry), CONTENT_TYPE_LATEST


def private_client(request):
    """Whether the caller is off the public internet: a container, a WireGuard
    peer, this host. Anything with a globally routable address is not."""
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global
