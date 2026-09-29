# Asynchronous architecture

How Tremplin's local server runs concurrently: one event loop, a few background
threads, and the rules that keep them from stepping on each other. The migration
that produced this shape is described in `flask_to_fastapi.md`; this document is
about the *runtime* model that resulted.

---

## Part 1 — The structure

### The one event loop (the heart)

`uvicorn` runs the FastAPI app on a **single asyncio event loop**, in one thread
(the "main" thread). That loop is the only thing allowed to touch WebSocket
sockets. Everything the loop does must be *fast and non-blocking* — while it runs
one piece of work, every connected scoreboard is waiting.

- **`async def` handlers** (WebSocket endpoints in `Tremplin.py`, a few routes)
  run **directly on the loop**. They may only `await` — never block.
- **plain `def` handlers** (most routes in `routes/*.py`) are automatically run by
  Starlette in a **threadpool** (a small pool of helper OS threads), so their
  blocking work — reading an upload, parsing LENEX, `subprocess` — doesn't freeze
  the loop.
- When an `async` handler *must* do something blocking, it hands that work to the
  pool explicitly with **`await run_in_threadpool(fn, …)`** (e.g. WiFi connect,
  backup restore, session upload).

### The message bus (`bus.py`)

`bus.py` is the fan-out layer that replaced Flask-SocketIO.

- `ConnectionManager` holds **channels** — one set of WebSockets per path
  (`/scoreboard`, `/results`, `/settings`). `broadcast()` sends a JSON frame
  `{"event", "data"}` to everyone in a channel; it runs **on the loop**.
- `set_loop()` captures the loop at startup so other threads can reach it.
- **`emit(channel, event, data)`** is the thread-safe entry point. A background
  thread calls it; internally it uses **`asyncio.run_coroutine_threadsafe`** to
  schedule the broadcast *onto the loop* and returns immediately. This is the one
  bridge from "thread world" back into "loop world."
- **`run_bg(fn)`** starts a daemon OS thread — the replacement for SocketIO's
  background tasks.

### The background threads

Real OS threads, not loop coroutines, because their work is blocking or long-lived:

| Thread | Lives in | Job |
|---|---|---|
| **Worker** | `worker.py` `main_thread_worker` | Reads the serial console (or replays a recorded session), decodes packets, and `bus.emit`s scoreboard updates. Blocking serial I/O — must be a real thread. |
| **Finish / reset debounce** | `worker.py` `_finish_task` / `_reset_task` | Short-lived threads spawned on a race finish/un-finish; `time.sleep` a debounce window, then confirm results or wipe the board. |
| **Relay** | `relay.py` | Outbound WebSocket client that forwards events to the cloud server. Its own reconnect loop. |

### How the threads talk to the loop and to each other

The worker owns mutable state that the loop and other threads also read. Four
patterns keep that safe **without locks**:

1. **Thread → loop:** only via `bus.emit` (`run_coroutine_threadsafe`). Threads
   never touch a WebSocket directly.
2. **Single-owner + a command queue:** the console *decoder object* is touched by
   **only the worker thread**. When another thread needs a decoder operation
   (adjust splits, next heat, the debounced board wipe), it puts a callable on
   `state._worker_cmds` (a `queue.Queue`); the worker drains and runs it on its
   own thread. No two threads ever touch the decoder at once.
3. **Atomic reference swap (snapshot):** the loaded meet lives in one immutable
   object, `state.meet`. Loading a new meet builds a fresh object and rebinds the
   single name — an *atomic* operation. Readers see either the whole old meet or
   the whole new one, never a half-updated mix. `state._last_results_snapshot` uses
   the same trick with a single dict. (See `state.py`.)
4. **Generation counters (invalidation tokens):** `state._worker_gen` and
   `state._finish_timer_gen` are integers bumped to mean "anything older is now
   stale." The worker runs while `_worker_gen == my_gen`; a debounced task only
   fires if `_finish_timer_gen` still matches the value it captured.

### The cloud server

`cloud/cloud_server.py` is a separate FastAPI app with the *same* loop + bus shape,
but its shared stores (`_meets`, `_relay_sids`, `_retained`, and the analytics DB)
genuinely have **no single owner** — every spectator page render reads meet data,
and reads outnumber writes ~2–3×. Single-owner + a queue (the local decoder's
pattern) fits a *write-heavy, single-reader* object; the cloud is the opposite, so
it uses a real lock instead: `threading.Lock` (`_lock`, `_analytics_lock`).

**Why `threading.Lock` and not `asyncio.Lock`.** The state is touched from *both*
worlds — async WebSocket handlers on the loop **and** sync `def` routes in the
threadpool. `asyncio.Lock` is single-loop and not thread-safe, so it can't be taken
from a threadpool thread; `threading.Lock` is the right tool for state shared across
the loop and the pool. (The instinct "async app ⇒ asyncio.Lock" is a trap here.)

**Keeping the cloud lock cheap.** A `threading.Lock` taken *on the loop* blocks the
whole loop until it's free, so a critical section must be short and must not do I/O.
Almost all of them are just in-memory dict reads/updates (microseconds). The one
place that broke this rule was persisting the retained-meets store — fixed next.

**Scaling the cloud persistence (per-meet files).** `_retained` keeps each meet's
record so a meet keeps showing after its relay disconnects. Two things made the naive
design fall over at ~30 live meets:

- each record embeds big blobs — the base64 **logo + background image** (pushed up by
  the relay's `register`) and the **full start list** (`schedule_data`);
- it was persisted as one file, re-serialized *whole* on every write.

So every `register` (each reconnect) and every `schedule_snapshot` did a `json.dumps`
of *all 30 meets* — tens of MB — **on the loop, under the lock** (~100 ms+), then
rewrote the entire file (one meet changing rewrote all). The fix has two parts:

1. **Per-meet files.** Each meet is `retained/<id>.json` (metadata), so a persist
   serializes and writes only the meet that changed. (`<id>` is validated against
   `_ID_RE`, so a relay- or backup-supplied id can't escape the directory.)
2. **Blobs kept out of the metadata.** Images and the schedule live in their own files
   (`<id>.icon`, `<id>.picker`, `<id>.schedule.json`) and are rewritten only by the
   event that changes them (`register` → images, `schedule_snapshot` → schedule),
   never on an unrelated persist. The metadata JSON stays a few KB.

In memory `_retained[meet_id]` still holds the *whole* record (blobs reassembled on
load), so every read/serve route is unchanged — only load and save changed. Combined
with **snapshot-under-lock, write-off-loop**, a persist holds the lock only long
enough to shallow-copy one small record; the serialize + atomic (temp-file +
`os.replace`) write happen on a threadpool thread while the loop keeps broadcasting:

```python
with _lock:  # fast: in-memory only
    _persist_meet_mem(meet_id, meet)
    rec = _record_copy_locked(meet_id)  # shallow copy of one record
await run_in_threadpool(_write_meet_files, meet_id, rec, write_schedule, write_images)
```

It's the same idea as the local server's snapshot swap, applied to files.

Every other disk write follows the same discipline — **no `async` handler blocks the
loop on I/O**. The keys/credentials files are written atomically (temp + `os.replace`,
so a crash can't corrupt them and lock admins out), and the admin routes that write
them (key add/revoke, `set_expiry`, `delete_meet`, the logo/icon upload, restore) and
the relay's disconnect-retire all wrap the write in `await run_in_threadpool(...)`.
Sync `def` routes (backups, the picker page) already run in the threadpool by
default, so their reads/writes are off the loop for free.

**Analytics writes are batched.** Logging every spectator join with its own
`INSERT` + `commit` under `_analytics_lock` — on the loop, from the WS connect
handlers — would serialize thousands of disk commits during a reconnect storm (a
venue Wi-Fi blip dropping every phone at once, which the client heartbeat guarantees
will retry). Instead `_log_connection` just does a non-blocking `queue.put` (no I/O),
and a background task (`_analytics_flush_loop`, started in `lifespan`) drains the
queue every few seconds and writes the whole batch in **one** `executemany` +
`commit` via `run_in_threadpool` — off the loop. A storm of N joins becomes one
batched transaction per interval instead of N commits on the loop; the queue is
drained (and discarded, if analytics is disabled) on shutdown too.

### Diagram

```text
                       ┌─────────────────────────────────────────┐
   phones / TVs  <--->  WebSockets   (event loop, main thread)    │
   (JSON frames)       │  async handlers • bus.ConnectionManager  │
                       │  plain-def routes ──► threadpool threads  │
                       └───▲───────────────▲──────────────────────┘
                           │ run_coroutine_threadsafe (bus.emit)
        ┌──────────────────┴──────┐   ┌────────────┐   ┌──────────┐
        │ Worker thread           │   │ debounce   │   │ Relay    │
        │  owns the decoder       │   │ threads    │   │ thread   │
        │  ◄── state._worker_cmds │   │ time.sleep │   │ ──► cloud│
        └─────────────────────────┘   └────────────┘   └──────────┘
```

---

## Part 2 — The key ideas, for a beginner

If you're new to programming, here are the terms above, explained plainly.

### Concurrency vs. parallelism

**Concurrency** is dealing with many things by switching between them quickly
(one cook, many pots, stirring each in turn). **Parallelism** is many things truly
at the same time (many cooks). Tremplin is mostly *concurrent*: one loop juggling
hundreds of phones, plus a few background threads for the genuinely parallel or
blocking jobs.

### The event loop, `async` / `await`, and "blocking"

Think of the **event loop** as a single very fast receptionist handling many
visitors. Each `await` is the receptionist saying "you're waiting on something
(the network) — go sit down, I'll call you when it's ready" and helping the next
person meanwhile. That's why one loop can serve hundreds of scoreboards.

A **blocking** call is one that makes the receptionist *stop and wait* — like
reading a big file, or `time.sleep`. If that happened on the loop, **every**
scoreboard would freeze until it finished. So the rule is: blocking work never
runs on the loop.

- `async def` = "this runs on the loop; it may only `await`, never block."
- `await` = "pause here without freezing the loop; resume when ready."

### Thread

A **thread** is a separate line of execution that the operating system can run
independently. Tremplin uses threads for jobs that *must* block: reading the serial
port never stops, so it lives in its own thread where blocking is fine — it isn't
holding up the loop.

### The threadpool

Starting a brand-new thread for every request would be wasteful. A **threadpool**
is a small, reusable set of worker threads kept ready. FastAPI sends every plain
`def` route to this pool automatically, and `run_in_threadpool(fn)` lets an `async`
handler push one blocking job there. The loop stays free; the pool does the waiting.

### The GIL (Global Interpreter Lock)

Python has one **GIL**: a rule that only one thread runs Python bytecode at a time,
even on a multi-core CPU. Two consequences that shape this codebase:

- A **single** operation — assigning a value, reading a value, swapping one name to
  point at a new object — can't be interrupted half-way. It is **atomic**. That's
  *why* the meet-snapshot swap and the boolean flags need no lock.
- A **compound** operation is *not* safe. `x += 1` is really *read x, add 1, store
  x* — three steps, and another thread can slip in between them, so an update can be
  lost. Tremplin only does this on the **generation counters**, where a lost count
  is harmless (all that matters is that the number *changed*).
- The GIL does **not** rescue you from blocking: a thread stuck in `time.sleep` or a
  slow read still holds up whatever is waiting on it. That's a separate problem the
  loop/thread split solves.

### Race condition

A **race condition** is a bug where the outcome depends on *which thread happens to
go first*. Classic example: the worker is looping over a dictionary while another
thread empties it — "dictionary changed size during iteration," a crash. Real
threads (unlike the old cooperative model) can interrupt each other at awkward
moments, so this codebase was audited to remove every such window.

### Mutex / lock

A **mutex** ("mutual exclusion"), or **lock**, is the traditional fix for a race:
a token only one thread can hold at a time, so others wait their turn to touch the
shared thing. Locks are correct but easy to get wrong (forget one → race; hold two
in the wrong order → deadlock; hold one too long → everything waits).

Tremplin's **local** server deliberately avoids locks by removing the *sharing*
instead:

- **Single owner** — only the worker touches the decoder, so there's nothing to
  lock; other threads ask via the command queue.
- **Atomic swap** — publish a whole new immutable object in one step, so a reader
  never sees a half-written one.

The **cloud** server, whose data has no natural single owner, uses real
`threading.Lock`s — the honest tool when those patterns don't fit.

### Queue

A **queue** is a thread-safe line: one thread `put`s items on one end, another
`get`s them off the other, and the queue handles the locking internally. Tremplin
uses `state._worker_cmds` so any thread can *request* a decoder operation and the
worker performs it safely, one at a time, on its own thread.

### `run_coroutine_threadsafe`

The one official bridge from a plain thread back onto the event loop. A background
thread can't just call an `async` function; it calls
`asyncio.run_coroutine_threadsafe(coro, loop)`, which safely schedules that
coroutine to run on the loop. `bus.emit` wraps this so the worker can broadcast a
scoreboard update without knowing any of the details.

---

## The rules, in one place

1. Never block the event loop — offload blocking work to the threadpool
   (`run_in_threadpool`, or just use a plain `def` route).
2. Threads reach clients only through `bus.emit`, never a WebSocket directly.
3. Shared mutable state is made safe by **ownership** (one writer + a queue) or by
   **atomic swap** (publish a whole new immutable object), not by locks.
4. `+= 1` across threads is only used where a lost update is harmless (generation
   counters). Anything else that needs a read-modify-write would need a lock.
5. The cloud server is the exception: no single owner, so it uses `threading.Lock`
   (not `asyncio.Lock` — the state is shared with threadpool threads). Keep those
   critical sections short and I/O-free: snapshot under the lock, write off the loop.
