# Development

## Setup

```bash
uv sync
(cd server && uv run python app.py)      # or: cd server && uv run uvicorn app:app --port 5000
uv run --with pytest pytest tests/
```

The server runs from `server/` (its working directory), so its flat modules import each
other by bare name. `static/` and `locales/` live in the sibling `shared/` dir (shared
with the cloud relay); `themes/` and bundled `console_recordings/` stay under `server/`
(what is in them, and how `.cts` and `.raw` differ: [`server/console_recordings/README.md`](../server/console_recordings/README.md)).

## Testing with a live console

Upload a recorded `.cts` or `.raw` session via the **Test** tab in the admin UI to replay timing data without a live console.

## Testing with swimmer names

A sample Lenex file with fictional swimmers is included at `tests/fixtures/splash.lxf`. Copy it to `~/SplouchData/meet/` and click **Reload Names** in Meet Setup:

```bash
cp tests/fixtures/splash.lxf ~/SplouchData/meet/
```

---

## Data flow

```text
Timing console
  │  Serial connection
  ▼
Pi #1 /dev/ttyUSB0
  │  server/console_decoders/ — parses raw bytes into lane/place/time events
  │
  ├── bus.py (plain-WebSocket message bus)
  │     emit → /scoreboard channel → all connected browsers
  │     emit → /results channel    → results pages
  │
  ├── meet_data.py
  │     Lenex / Hytek lookup → swimmer name + club per lane
  │
  └── relay.py (optional)
        outbound WebSocket to cloud server
        forwards update_scoreboard, results_snapshot, next_heats, schedule_snapshot
```

## Adding a console decoder

### Built-in decoder (committed to the repo)

1. Create `server/console_decoders/<name>.py` implementing the `ConsoleDecoder` interface from `server/console_decoders/base.py`.
2. Add a `<name>_serial.md` protocol reference alongside it.
3. Register the decoder in `server/console_decoders/__init__.py` by adding entries to `CONSOLE_OPTIONS` and `DECODERS`.

### Local-only decoder (not committed — e.g. for proprietary protocols)

Place a `.py` file in `~/Scoreboard/console_decoders/`. It is loaded automatically at startup and whenever the Settings page is opened, without restarting the service.

The file must define `CONSOLE_OPTIONS` and `DECODERS` at module level:

```python
from console_decoders.base import ConsoleDecoder, SerialConfig

class MyDecoder(ConsoleDecoder):
    @property
    def serial_config(self):
        return SerialConfig(baud=9600, bytesize=8, parity='N', stopbits=1)

    def is_packet_start(self, byte, buffer):
        return byte == 0x02  # STX starts a new frame

    def feed(self, packet):
        # Parse packet bytes, return update dict
        # Keys: current_event, current_heat, lane_time{n}, lane_place{n}, etc.
        return {}

    def reset_lanes(self):   return {}
    def race_finished(self): return False
    def set_seed_times(self, times): pass
    def configure(self, cfg): pass
    def get_lane_time(self, lane):  return ''
    def get_lane_place(self, lane): return ''

# Both of these must be present for the decoder to be registered.
CONSOLE_OPTIONS = [
    # (settings_key, dropdown_label, decoder_key)
    ('my_console', 'My Console (Manufacturer)', 'my_console'),
]
DECODERS = {
    'my_console': MyDecoder,
}
```

The decoder appears in **Settings → Timing → Console type** immediately after saving or reopening the page. Keys already registered by the built-in decoders are silently skipped, so naming collisions are harmless.

---

## Bundled assets

All frontend assets are served locally — no internet is required at the pool. The realtime client (`shared/static/js/ws.js`) ships with the repo; xterm.js is downloaded during `install.sh`.

| Asset | Licence |
| --- | --- |
| Overpass Mono | SIL OFL 1.1 |
| DSEG7 Classic | SIL OFL 1.1 |
| DSEG14 Classic | SIL OFL 1.1 |
| Share Tech Mono | SIL OFL 1.1 |
| Orbitron | SIL OFL 1.1 |
| Roboto Mono | SIL OFL 1.1 |
| xterm.js 5.3.0 | MIT |
| Tabler Icons | MIT |
| jQuery 1.12.4 | MIT |
