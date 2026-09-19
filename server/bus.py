"""Realtime message bus — plain-WebSocket fan-out to connected clients.

Each *channel* (``/scoreboard``, ``/results`` …) is a set of connected
WebSockets. Messages are JSON frames ``{"event", "data"}``.

The serial worker runs in a background *thread* (blocking serial I/O), so it
cannot ``await`` anything. It calls :func:`emit`, which is thread-safe: it
schedules the async broadcast onto the uvicorn event loop captured at startup
via :func:`set_loop`. Async request handlers can call :func:`emit` too — from
inside the loop thread ``run_coroutine_threadsafe`` simply schedules the send
and returns without blocking.
"""
import asyncio
import threading

_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop):
    """Capture the running event loop. Call once from the ASGI startup hook."""
    global _loop
    _loop = loop


class ConnectionManager:
    """Tracks connected WebSockets per channel and fans messages out to them."""

    def __init__(self):
        self.channels: dict[str, set] = {}

    async def connect(self, ws, channel):
        await ws.accept()
        self.channels.setdefault(channel, set()).add(ws)

    def disconnect(self, ws, channel):
        conns = self.channels.get(channel)
        if conns:
            conns.discard(ws)

    async def send(self, ws, event, data=None):
        """Send one frame to a single socket, ignoring a dead connection."""
        try:
            await ws.send_json({'event': event, 'data': data})
        except Exception:
            pass

    async def broadcast(self, channel, event, data=None):
        """Send one frame to every socket in a channel; drop any that fail."""
        targets = list(self.channels.get(channel, ()))
        if not targets:
            return
        frame = {'event': event, 'data': data}
        # Send to every client concurrently so one slow/backed-up socket (a phone
        # on flaky venue Wi-Fi) can't delay the rest — including the on-site kiosk,
        # which shares this channel and streams the running-time clock. Still one
        # loop: this overlaps the sends' I/O waits, it is not parallelism.
        results = await asyncio.gather(*(ws.send_json(frame) for ws in targets),
                                       return_exceptions=True)
        for ws, result in zip(targets, results, strict=True):
            if isinstance(result, Exception):
                self.disconnect(ws, channel)


manager = ConnectionManager()


def emit(channel, event, data=None):
    """Broadcast to a channel. Thread-safe, non-blocking, fire-and-forget.

    Safe to call from the serial worker thread or from async request handlers.
    Silently drops the message if the event loop isn't running yet.
    """
    loop = _loop
    if loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast(channel, event, data), loop)
    except Exception:
        pass


def run_bg(fn, *args):
    """Run ``fn(*args)`` in a daemon thread."""
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t
