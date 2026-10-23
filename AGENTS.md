# Agent instructions

For AI coding assistants (Claude Code, GitHub Copilot, Codex, Cursor and others) working in this repository.
People should start with [CONTRIBUTING.md](CONTRIBUTING.md), and [docs/development.md](docs/development.md) for the data flow and module layout. Both say more
than this file; when they disagree, they win.

## What this is

A swim meet scoreboard that runs on two Raspberry Pis on a pool deck with no internet: the server
(`server/`), the Qt kiosk board (`scoreboard/`), the cloud relay (`cloud/`) and shared code (`shared/`). The
bar is "it still works at 8am on meet day with nobody to debug it". `docs/app.md` and `docs/api.md` are the
contracts that the [iOS](https://github.com/olivierouellet/Splouch-ios) and
[Android](https://github.com/olivierouellet/Splouch-android) apps build against: a change to either is a
change for those repos too.

## Before you finish

```bash
uv sync --extra scoreboard --group lint   # keep both flags: a plain `uv sync` removes the linters and Qt
uv run pytest tests/
uv run ruff format --check && uv run ruff check && uv run ty check
```

Then the checks in CONTRIBUTING.md → *Before you open a pull request* for the file types you touched
(shell, workflows, YAML, Markdown, JavaScript and CSS, templates, TOML, `uv.lock`). All are clean on `master`
and must stay that way.

A green run can mean less than it looks: without PySide6 the Qt tests skip, and without `TEST_DATABASE_URL`
the cloud's Postgres tests skip. Say which ran.

## Conventions

* **Python 3.13**, what Raspberry Pi OS Trixie ships. A pass on a newer Python is no evidence about the Pi.
* **Ruff owns layout and import order.** No `per-file-ignores`: a suppression is a `# noqa` on its line,
  with the reason.
* **The module boundary runs one way:** `paths` → `i18n` → `state`, checked by
  `tests/test_module_boundaries.py`. A test that redirects a directory patches `paths`.
* Keep FastAPI endpoint signatures annotated with `Request`.
* Spell Qt enums the scoped Qt 6 way: `Qt.AlignmentFlag.X`, not `Qt.X`.
* **Everything is served locally:** no CDN links and no runtime downloads. New frontend assets ship in the
  repo or come from `install.sh`, and go in the licence table in `docs/development.md`.
* The page scripts in `shared/static/js/` are classic ES5 `<script>` files: no `?.`, no `=>`.
* Labels and event names live in `shared/locales/` (TOML, aligned by Taplo); every locale has English's keys.
* Changing `docs/api.md` or `docs/app.md` changes the apps' contract: say so, and never renumber a feature ID.

## Commits and publishing

* Commit titles read `[Topic] What changed`, one topic per commit. Topics in use: `[Server]`, `[Scoreboard]`, `[Kiosk]`, `[Cloud]`, `[Settings]`, `[Install]`, `[Docs]`,
  `[Tooling]`, `[Security]`. Add a body when the why isn't obvious from the diff.
* **Never commit, push, tag, publish a release, or open an issue or pull request unless asked.** Approval for
  one of these is not approval for the next.
* `master` is protected against force-pushes and deletion: never rewrite history that is already pushed.
* Never post comments on issues or pull requests on the user's behalf without their review.

## AI policy

This project follows its [AI policy](AI_POLICY.md): a person reviews and understands every change, no
autonomous contributions, and nothing is posted on someone's behalf without their review.
