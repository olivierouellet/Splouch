# Hardcoded values — potential settings candidates

Reviewed 2026-05-10. Values already in settings are marked ✓.

| Value | Location | What it does | Verdict |
| --- | --- | --- | --- |
| `9600 baud` serial speed | `Tremplin.py` `serial.Serial(..., 9600)` | CTS Gen6 serial port baud rate | Fixed by hardware protocol — not configurable |
| Podium colours | `scoreboard_style.css` / Theme tab | Row highlight colours for 1st/2nd/3rd | ✓ Already in Theme tab |
| Number of lanes | `settings.json` / Meet Setup tab | 4, 6, or 8 lanes | ✓ Already in Meet Setup |
| Intro → Splash timeout | `settings.json` / Flow tab | Seconds before returning to Splash after a new event/heat if race doesn't start | ✓ Already in Flow tab |
| Results → Splash timeout | `settings.json` / Flow tab | Seconds before returning to Splash after results are shown | ✓ Already in Flow tab |
