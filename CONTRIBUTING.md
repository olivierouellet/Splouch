# Contributing to Splouch

Thanks for taking an interest. Splouch runs on a pool deck, on two Raspberry Pis, with
no internet — so the bar for a change is not just "it works here", it is "it still works
at 8am on meet day with nobody to debug it". That shapes most of what follows.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## What helps most

| | |
| --- | --- |
| **Console decoders** | Most supported consoles are marked ⚠️ *Untested* in the [README](README.md#supported-consoles). If you have one on a wire, confirming a decoder — or fixing it — is the single most useful thing you can do. |
| **Recorded sessions** | A `.raw` capture from a real meet lets everyone else test against your console without owning one. See [`server/console_recordings/README.md`](server/console_recordings/README.md). |
| **Translations** | Labels and event names live in `shared/locales/`. |
| **Bug reports from real meets** | Anything that surprised you at the pool. |

---

## Setup

```bash
uv sync --extra scoreboard               # everything, PySide6 included
cd server && uv run python app.py        # http://localhost:5000
```

Sync the `scoreboard` extra even if you never touch the kiosk. The extra is optional for
*deploying* — the server Pi and the cloud VM never pull 341 MB of Qt — but not for
developing: 180 of the 1224 tests drive the Qt board, and without PySide6 they don't run.
Seven of those files skip at module level, which pytest reports as seven skipped lines
rather than 180, so a run that never touched the kiosk still looks green.

Plain `uv sync` gives you the server-only install. Worth knowing for a fast inner loop —
it turns the suite from 68s into 7s, since Qt is nearly all of the runtime — as long as
the full run happens before you open the PR.

`.python-version` pins the interpreter to **3.13**, which is what Raspberry Pi OS Trixie
ships. `uv` honours it automatically. Newer Pythons run the suite fine, but a green run
on one is not evidence about the version on the pool deck.

To work with swimmer names, copy the fixture meet in and click **Reload Names** in Meet
Setup:

```bash
cp tests/fixtures/splash.lxf ~/SplouchData/meet/
```

To work without a console, upload a recorded session via the **Test** tab.

[docs/development.md](docs/development.md) covers the data flow, the module layout, and
how to add a console decoder.

---

## Before you open a pull request

All three must pass:

```bash
uv run pytest tests/      # 1224 tests, ~70s
uv run ruff check
uv run ty check
```

They are clean on `master`, tests included, and are expected to stay that way. `ty` is
still pre-1.0, so treat a new diagnostic from it as a question rather than a verdict —
but so far the answer has been worth having every time.

---

## Conventions

These are the ones that trip people up. They are not style preferences for their own
sake; each has a reason in the tree.

**Don't run `ruff format`.** It rewrites 58% of the lines here, flattening the aligned
assignments and hand-wrapped import blocks that make modules like `state` readable. Lint
only — `uv run ruff check`.

**No `per-file-ignores`.** Every suppression is a `# noqa` on the line it applies to,
with a reason, so a silenced rule stays visible where it was silenced and the rest of the
file keeps being checked.

**Respect the module boundary.** `paths` → `i18n` → `state`, one direction of travel;
[`tests/test_module_boundaries.py`](tests/test_module_boundaries.py) fails if it is ever
reversed. A test that redirects a directory patches the module that *reads* it (`paths`,
not `state`).

**Keep FastAPI signatures annotated with `Request`.** FastAPI builds the request from
those signatures, and a `Protocol` in one is taken for a query parameter — the module
still imports and the endpoint breaks. Plain helpers are free to ask for less; see
`web.HasHeaders`.

**Spell Qt enums the scoped Qt 6 way** — `Qt.AlignmentFlag.X`, not `Qt.X`. PySide6
resolves both at runtime, but only the scoped spelling is in its stubs.

**Everything is served locally.** No CDN links, no runtime downloads. New frontend assets
ship with the repo or are fetched during `install.sh`, and go in the licence table at the
bottom of [docs/development.md](docs/development.md).

### Commits

One topic per commit, with the area in brackets and a title that says what changed:

```text
[Scoreboard] Show the lap countdown from the start of the heat
[Server] Derive split_step from the pool's touchpads, not from the decoder
[Cloud] Stop a deploy re-applying the install-time domain
```

Common tags: `[Server]`, `[Scoreboard]`, `[Kiosk]`, `[Cloud]`, `[Settings]`, `[Install]`,
`[Docs]`, `[Tooling]`, `[Security]`. Add a body when the *why* isn't obvious from the
diff — that is where this tree keeps its reasoning.

---

## Reporting a bug

Open an issue with:

* What you were doing, and what the scoreboard showed instead.
* **Console make and model**, and how it is wired (adapter, baud).
* The relevant chunk of the serial monitor (**Settings → Timing**), or the recorded
  session if you have one.
* Whether it happened on Pi #1 (server), Pi #2 (kiosk), the browser, or the cloud relay.

For anything security-sensitive, don't open a public issue — follow
[SECURITY.md](SECURITY.md), which also sets out what Splouch assumes about the pool-deck
network and what is out of scope.

---

## Licence

Splouch is [MIT](LICENSE). Contributions are accepted under the same terms.
