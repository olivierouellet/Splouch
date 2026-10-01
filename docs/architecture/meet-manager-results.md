# Validated results from Meet Manager

> **Idea, not built.** Nothing below exists in the code yet.

Showing officially validated heat results (not just live timing console data) on the scoreboard, sourced from Splash Meet Manager.

- **Live Results (PDF)**: Meet Manager can auto-publish start lists/results as PDF reports via FTP. An embedded FTP server (`pyftpdlib`) on the server Pi could receive these, but PDFs aren't structured data — parsing the result-list table with `pdfplumber` is feasible but template/locale-fragile. A simpler fallback is to just serve the PDFs as documents on a "Results" page rather than parsing them into live data.
- **Database server (preferred, untested)**: Meet Manager can store meet data in PostgreSQL/MariaDB/MySQL instead of an `.mdb` file, with near-instant sync across clients. If Postgres ran on the server Pi, Splouch could query results directly — no FTP, no PDF parsing. The schema is undocumented, so this needs a spike: point a test Meet Manager install at a local Postgres DB, run a small meet, and inspect the resulting tables for result/heat/time data before committing to this approach.
- Either way, no separate database is needed for Splouch's own state — results would slot into the existing in-memory `state.py` structures (e.g., a `lenex_results` dict), pushed to the boards over the existing WebSocket.
