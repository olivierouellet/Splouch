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
npm ci                                   # Biome and TypeScript, for JS/CSS — dev only, never on a Pi
cd server && uv run python app.py        # http://localhost:5000
```

Sync the `scoreboard` extra even if you never touch the kiosk. The extra is optional for
*deploying* — the server Pi and the cloud VM never pull 341 MB of Qt — but not for
developing: a sixth of the suite drives the Qt board, and without PySide6 none of it runs.
Those files skip at module level, which pytest reports as one skipped line each rather
than one per test, so a run that never touched the kiosk still looks green.

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

All of these must pass. The first four are the Python suite; the rest take seconds
and only matter if you touched the file types they check:

```bash
uv run pytest tests/      # about a minute with Qt, seconds without
uv run ruff format --check
uv run ruff check
uv run ty check

uv run shellcheck -S warning install.sh install/*.sh install/scripts/*.sh
uv run shfmt -d install.sh install/
uv run actionlint
uv run zizmor --offline .github/workflows/
uv run yamllint --strict .
uv run rumdl check .
npx biome ci
npx tsc -p shared/static/js/jsconfig.json
uv run djlint server/templates cloud/templates shared/templates --lint
uv run taplo fmt --check
uv run taplo lint

# Only if you touched cloud/requirements.in: regenerate, then commit the result.
uv pip compile cloud/requirements.in --universal --python-version 3.13 \
  --generate-hashes -o cloud/requirements.txt
```

`uv run ruff format` and `uv run ruff check --fix` fix the Ruff two for you, and
VS Code with the recommended Ruff extension does both on save. `uv run shfmt -w
install.sh install/`, `uv run rumdl fmt .`, `npx biome check --write` and `uv run taplo
fmt` do the same for shell, Markdown, JavaScript and CSS, and TOML.

They are clean on `master`, tests included, and are expected to stay that way. `ty` is
still pre-1.0, so treat a new diagnostic from it as a question rather than a verdict —
but so far the answer has been worth having every time.

### Tooling by language

What checks each kind of file, and whether CI fails on it. Vendored minified files
(Bootstrap, htmx) are shipped as-is and checked by nothing.

| Language | Where | Linter | Formatter | Types | Tests | Coverage | Gated in CI |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Python 3.13** | `server/`, `scoreboard/`, `cloud/`, `shared/py/`, `tests/` | Ruff (`ruff check`, rules in `pyproject.toml`) | Ruff (`ruff format`, imports by the `I` rules) | ty | pytest | pytest-cov — printed in the CI log, never gated | Yes, all four |
| **Shell** | `install.sh`, `install/` | ShellCheck (`-S warning`) | shfmt (settings in `.editorconfig`) | — | — | — | Yes, both |
| **JavaScript** | `shared/static/js/` | Biome (`biome.jsonc`) | Biome | `// @ts-check`, run by `tsc` (`jsconfig.json`; page globals in `globals.d.ts`) | Each page's scripts run on load by [`tests/jsc.py`](tests/jsc.py) (JavaScriptCore on macOS, Node in CI) | — | Yes, Biome, tsc and pytest |
| **HTML / Jinja** | `server/templates/`, `cloud/templates/`, `shared/templates/` | djLint (`[tool.djlint]` in `pyproject.toml`) | — (djLint's would reflow every inline script) | — | Rendered and asserted on by pytest | — | Yes, djLint and pytest |
| **CSS** | `shared/static/css/` | Biome (`biome.jsonc`) | Biome | — | Some rules are asserted on by pytest, which reads the source | — | Yes, Biome and pytest |
| **TOML** | `shared/locales/`, `server/themes/`, `pyproject.toml` | `taplo lint` (syntax; `pyproject.toml` against its schema) | Taplo (`taplo.toml`, columns kept aligned) | — | Locale key parity with English in [`tests/test_i18n.py`](tests/test_i18n.py) | — | Yes, Taplo and pytest |
| **Markdown** | `*.md`, `docs/` | rumdl in CI, markdownlint in the editor — both read `.markdownlint.json` | `rumdl fmt` fixes what the check flags | — | — | — | Yes, rumdl |
| **YAML** | `.github/`, `cloud/docker-compose.yml` | yamllint (`.yamllint.yml`); actionlint and zizmor (security) for workflows; schema validation in the editor (Red Hat YAML extension) | — | — | — | — | Yes, yamllint, actionlint and zizmor |
| **JSON** | every `*.json` / `*.jsonc` but `package-lock.json` | Biome (`biome.jsonc`) | Biome, 2-space as npm writes it | — | — | — | Yes, Biome |
| **Python requirements** | `cloud/requirements.txt` from `requirements.in` | CI regenerates it with the command in its header and fails on any difference | — | — | — | — | Yes |

Every linter and formatter above is pinned: Biome and TypeScript by `package-lock.json`, the rest
in the `lint` dependency group by `uv.lock`. Only Node, for the JavaScript smoke tests,
comes from the CI image's apt. The VS Code extensions in `.vscode/extensions.json`
run Ruff, ty, ShellCheck, markdownlint, Biome and Taplo, and VS Code reads
`jsconfig.json` itself, so a file clean in the editor is one CI accepts. djLint has
no editor extension here; run it before pushing a template change.

Biome's config turns off seven recommended rules, each with its reason in
`biome.jsonc`. Most come from one mismatch: Biome reads every `.js` as an ES
module, and these are classic `<script>` files whose top-level functions are called
from the templates. The scripts are deliberately ES5 — `?.` or `=>` that an older
phone cannot parse takes the whole script down — so keep new code in the same
dialect.

---

## Conventions

These are the ones that trip people up. They are not style preferences for their own
sake; each has a reason in the tree.

**Ruff owns layout and import order.** Code is formatted with `ruff format` and imports
are sorted by Ruff's `I` rules, both on Ruff's defaults; CI fails on either drifting.
Don't hand-align assignments or hand-wrap import blocks — the formatter will undo it.

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

Open an issue — the form asks for what's needed: what happened, which component, the
console, the version, and the serial monitor around the failure.

If you ran Splouch against a real timing console, use the **Console support report**
instead, whether it worked or not. Five of the six supported consoles have never been
confirmed on hardware, and a raw capture from yours is what lets the decoder be fixed by
someone who doesn't own one.

For anything security-sensitive, don't open a public issue — follow
[SECURITY.md](SECURITY.md), which also sets out what Splouch assumes about the pool-deck
network and what is out of scope.

---

## Licence

Splouch is [MIT](LICENSE). Contributions are accepted under the same terms.
