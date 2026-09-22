<!--
Thanks for this. CONTRIBUTING.md has the setup, the conventions, and the reasoning
behind the ones that look arbitrary: https://github.com/olivierouellet/Splouch/blob/master/CONTRIBUTING.md
-->

## What this changes

<!-- One or two sentences. The *why* matters more than the diff — that is where this
     tree keeps its reasoning. -->

## Checks

```bash
uv run pytest tests/
uv run ruff check
uv run ty check
```

- [ ] All three pass.
- [ ] Synced with `--extra scoreboard`, so the Qt tests actually ran rather than skipping.
- [ ] No `ruff format` run over the tree.
- [ ] Any suppression is a `# noqa` on its own line, with a reason.

## Hardware

- [ ] Tested against a real timing console — which one: <!-- model + firmware -->
- [ ] Tested against a recorded session only.
- [ ] Doesn't touch console decoding.

## Also

- [ ] Docs updated, if this changes something an operator sees or does.
- [ ] Commits read `[Topic] What changed`, one topic each.
- [ ] New frontend assets ship with the repo — no CDN links, no runtime downloads — and
      are listed in the licence table in `docs/development.md`.

<!-- Closes #NNN -->
