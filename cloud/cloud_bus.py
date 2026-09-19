"""Attendee WebSocket fan-out, grouped into per-meet channels.

The relay's twin of ``server/bus.py``: same frame shape, different membership
rule. On the Pi a channel is a page; here it is a page *and* a meet, because one
process serves every meet at once and an attendee must only ever receive the one
they joined.
"""
import asyncio

class ConnectionManager:
    """Attendee WebSockets grouped into per-meet channels."""

    def __init__(self):
        self.channels: dict[str, set] = {}

    def join(self, ws, channel):
        self.channels.setdefault(channel, set()).add(ws)

    def leave_all(self, ws):
        for conns in self.channels.values():
            conns.discard(ws)

    async def send(self, ws, event, data=None):
        try:
            await ws.send_json({'event': event, 'data': data})
        except Exception:
            pass

    async def broadcast(self, channel, event, data=None):
        targets = list(self.channels.get(channel, ()))
        if not targets:
            return
        frame = {'event': event, 'data': data}
        # Send to every attendee concurrently so one slow/backed-up client can't
        # delay delivery to the rest (still one loop — this overlaps the I/O waits,
        # it is not parallelism). return_exceptions keeps one failure from
        # cancelling the others; failed sockets are dropped.
        results = await asyncio.gather(*(ws.send_json(frame) for ws in targets),
                                       return_exceptions=True)
        for ws, result in zip(targets, results, strict=True):
            if isinstance(result, Exception):
                self.leave_all(ws)


manager = ConnectionManager()


def ch(ns, meet_id):
    """Channel key for one namespace of one meet."""
    return f'{ns}:{meet_id}'
