# Settings Panel Modernization

## Context

Per `native_app_strategy.md`, the operator settings panel is the one UI that **stays
a browser page** (used by the meet operator from a laptop, not by spectators). It has
grown to **2,281 lines** in a single `templates/settings.html` with a lot of
hand-written JavaScript glue and a fragile custom dark theme. This document is the
roadmap to modernize it — reduce maintenance and eliminate a class of visual bugs —
**without changing the look** and **without leaving the FastAPI + Jinja + offline
stack** the server was just migrated to.

Goal: less code, no new runtime, identical appearance, incremental (tab by tab).

## Current state (measured)

- **15 tabs**, ~**59** settings endpoints across `routes/{settings,system,network,debug,appearance}.py`.
- **`settings.html` = 2,281 lines**, of which **~1,121 are JavaScript** inside `<script>`.
  - **47 `fetch()` sites** + **18 `setInterval`/`setTimeout`** polling loops — the "glue":
    every dynamic widget hand-codes `event → fetch → JSON → patch the DOM`.
  - **~100–150 of those JS lines are genuinely custom** (the xterm PTY terminal and the
    live serial-hex Debug stream over WebSocket) — **not glue; they stay**.
- **449 inline `style="…"` attributes** — the readability bloat. The same patterns
  repeat: the card panel (`background:#f5f5f5;border…;padding:8px 14px`) **×18**,
  `display:flex` **×83**, `font-weight:normal` **×76**, `min-width:` **×69**.
- **Dark theme is a hack**: **33 `!important` overrides** plus **8
  `div[style*="background:#f5f5f5"]` attribute-selector rules** that repaint inline-styled
  boxes dark. This exists *only* because **Bootstrap 3 has no dark mode**.
- Stack: **Bootstrap 3** + **jQuery 1.12.4** (BS3's dependency).

## The three levers

### Lever 1 — HTMX (removes the fetch/DOM glue)

Keep the exact Jinja/Bootstrap markup; add declarative `hx-*` attributes and have the
existing endpoints **return small HTML fragments instead of JSON**. Polling becomes
`hx-trigger="every Ns"`; `confirm()` dialogs become `hx-confirm`; the DOM-building JS
strings move into terse Jinja partials. **The rendered HTML is identical → same look.**

- Removes ~**950–1,000 lines** of glue gross; ~**150–250 lines** relocate into Jinja
  partials (markup, not logic) → **net ≈ −700 to −850 lines** across the panel.
- Vendor `htmx.min.js` into `static/js/` (like `ws.js`) — one ~50 KB file, offline, shared by every tab.

### Lever 2 — extract inline styles into classes

Replace the repeated inline-style patterns (the 18 cards, 83 flex rows, etc.) with a
handful of reusable classes (`.scard`, `.frow`, …). Do it **as you touch each tab** in
Lever 1 — you're already editing that markup. Removes most of the 449 inline styles and
makes the file dramatically more readable. On BS3 these are small custom CSS classes; on
BS5 (Lever 3) they become Bootstrap utilities.

### Lever 3 — Bootstrap 3 → 5.3 + `data-bs-theme` color modes (the finisher)

BS 5.3's [color modes](https://getbootstrap.com/docs/5.3/customize/color-modes/) give
first-class light/dark theming: set `data-bs-theme="dark"` on `<html>` and every
component (buttons, forms, tables, **modals**, dropdowns, cards) renders its dark variant
via CSS variables.

- **Deletes the entire dark-theme hack** — the 33 `!important` overrides *and* all 8
  `div[style*="…"]` attribute-selector rules. The class of bug they cause (see POC below)
  becomes structurally impossible.
- **Drops jQuery** (BS5 components are vanilla-JS / data-attribute driven).
- BS5's utility API (`d-flex gap-2`, `p-2 border rounded`, `fw-normal`) absorbs the
  remaining inline styles, so the Lever-2 custom classes largely fold into Bootstrap's own.
- Cost: a real migration — class renames across all Bootstrap templates
  (`btn-default`→`btn-secondary`, `input-sm`→`form-control-sm`, `data-toggle`→`data-bs-toggle`,
  `panel`→`card`, `pull-right`→`float-end`, grid gutters) and verifying nothing else needs
  jQuery (`meet.html`, `info.html`, `help.html` also load Bootstrap).

## Phased plan

| Phase | What | Risk | Reversible |
| --- | --- | --- | --- |
| **1. HTMX per tab** | Vendor htmx; convert one tab at a time (add `hx-*`, split JS-built markup into Jinja partials under `templates/partials/`, switch its endpoints to `return render(partial)`, delete the tab's JS). | Low — each tab is isolated; untouched tabs are unaffected. | Per tab |
| **2. Inline styles → classes** | Extract the repeated `.scard`/flex/label patterns into CSS classes while editing each tab in Phase 1. | Low | Per tab |
| **3. BS3 → BS5 + color modes** | Class renames across templates, drop jQuery, add `data-bs-theme="dark"`, **delete the ~125-line override block**. Do it once, deliberately, after Phases 1–2. | Medium — touches every Bootstrap template. | All-or-nothing |

Phases 1–2 deliver most of the reduction on the **current stack, no new runtime, no
visual change**. Phase 3 is the finisher that retires the fragile theming.

## POC evidence (Network tab)

A throwaway in-place conversion of the Network tab (HTMX + class extraction + a Bootstrap
modal for the wifi-connect form) measured:

- `settings.html`: **2,281 → 2,115 lines (−166)** for one tab — **171 lines of network JS
  deleted** (10 functions: `scanWifi`, `toggleWifi`, `connectWifi`, `_applyWifiStatus`,
  `showJoinForm`, `cancelJoin`, `loadClients`, `setEthIp`, `setEthDhcp`, `_netTabActive`).
- New Jinja partials: **+68 lines** (the relocated markup, terser than the JS strings).
- `routes/network.py`: roughly line-neutral (JSON returns → `render(partial)`).
- **Net ≈ −100 lines for one tab**, and the whole `fetch → JSON → DOM` pattern gone.

It also surfaced the theming bug that motivates Lever 3: moving a card's inline
`background:#f5f5f5` into a `.scard` class stopped the `div[style*="…"]` override from
matching, so the box rendered light instead of dark. The fix was to give `.scard` the dark
values directly — exactly what `data-bs-theme` would do for free.

## What stays custom (any approach)

The **Terminal** (xterm PTY) and the live **serial-hex Debug** stream are genuine
WebSocket code (~100–150 lines), not glue. They remain hand-written regardless of HTMX,
NiceGUI, or Bootstrap version.

## Estimated end state

`settings.html` ~**2,281 → ~1,400 lines**; no `fetch`/DOM glue; no custom dark-theme
overrides; no jQuery; a small `templates/partials/` set; a lean set of Bootstrap-utility /
data-attribute widgets that look identical to today.

## Alternative considered — NiceGUI

Writing the panel in Python (NiceGUI) was prototyped: ~40% less code for the form/list
tabs and elegant dialogs/refreshables. Rejected as the default because it ships a
Vue/Quasar runtime and its **own persistent WebSocket per tab** (reintroducing a socket
layer we just removed with the Socket.IO → plain-WS migration), fits the custom hardware
tabs poorly, and its blocking callbacks would need the same threadpool care we just added.
HTMX keeps the FastAPI/Jinja/offline architecture intact. NiceGUI remains a reasonable
choice only if the goal shifts to "never write HTML again."

## Open decisions

- **Connect UI**: Bootstrap **modal** (POC) vs. the original inline form — cosmetic preference.
- **Commit to Phase 3?** Phases 1–2 stand alone; the BS5 upgrade is worth it primarily for
  color modes (killing the theming hack) and shedding jQuery, but it's the largest step.
- **Long term**: whether the browser settings panel is ever replaced (unlikely — the
  strategy doc keeps it as a web page).
