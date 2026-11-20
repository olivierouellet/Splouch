"""Preview a lane board on an LED wall before buying the modules.

Renders 8 lanes of sample results at the wall's native resolution in a bold bitmap font
(2 px strokes), then draws every pixel as a round LED. Run from the repo root:

    uv run python tools/led_sim.py --modules 6x3
    uv run python tools/led_sim.py --modules 10x6 --scale 2

To judge it from the stands: a wall W m wide seen from D m looks as wide as the
image shown S cm wide seen from S / W x D cm.
"""

import argparse
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- Font ------------------------------------------------------------------------------
# Each glyph is a 1 px skeleton, cap height 10 rows; the brush thickens it to 2 px, so
# letters come out 7 x 11. Accents are drawn as-is (no brush) on rows -3/-2: 1 px above
# their letter, 2 px below the line above. The cedilla is on row 10.

SKELETONS = {
    "A": "..##.. .#..#. #....# #....# #....# ###### #....# #....# #....# #....#",
    "B": "#####. #....# #....# #....# #####. #....# #....# #....# #....# #####.",
    "C": ".####. #....# #..... #..... #..... #..... #..... #..... #....# .####.",
    "D": "####.. #...#. #....# #....# #....# #....# #....# #....# #...#. ####..",
    "E": "###### #..... #..... #..... #####. #..... #..... #..... #..... ######",
    "F": "###### #..... #..... #..... #####. #..... #..... #..... #..... #.....",
    "G": ".####. #....# #..... #..... #..### #....# #....# #....# #....# .####.",
    "H": "#....# #....# #....# #....# ###### #....# #....# #....# #....# #....#",
    "I": "#####. ..#... ..#... ..#... ..#... ..#... ..#... ..#... ..#... #####.",
    "J": "...### ....#. ....#. ....#. ....#. ....#. ....#. #...#. #...#. .###..",
    "K": "#....# #...#. #..#.. #.#... ##.... #.#... #..#.. #...#. #....# #....#",
    "L": "#..... #..... #..... #..... #..... #..... #..... #..... #..... ######",
    "M": "#....# ##..## #.##.# #....# #....# #....# #....# #....# #....# #....#",
    "N": "#....# ##...# ##...# #.#..# #.#..# #..#.# #..#.# #...## #...## #....#",
    "O": ".####. #....# #....# #....# #....# #....# #....# #....# #....# .####.",
    "P": "#####. #....# #....# #....# #####. #..... #..... #..... #..... #.....",
    "Q": ".####. #....# #....# #....# #....# #....# #....# #..#.# #...#. .###.#",
    "R": "#####. #....# #....# #....# #####. #..#.. #...#. #...#. #....# #....#",
    "S": ".####. #....# #..... #..... .####. .....# .....# .....# #....# .####.",
    "T": "###### ..#... ..#... ..#... ..#... ..#... ..#... ..#... ..#... ..#...",
    "U": "#....# #....# #....# #....# #....# #....# #....# #....# #....# .####.",
    "V": "#....# #....# #....# #....# #....# .#..#. .#..#. .#..#. ..##.. ..##..",
    "W": "#....# #....# #....# #....# #....# #....# #.##.# #.##.# ##..## #....#",
    "X": "#....# #....# .#..#. .#..#. ..##.. ..##.. .#..#. .#..#. #....# #....#",
    "Y": "#....# #....# .#..#. .#..#. ..##.. ..#... ..#... ..#... ..#... ..#...",
    "Z": "###### .....# ....#. ....#. ...#.. ..#... .#.... .#.... #..... ######",
    "0": ".####. #....# #....# #....# #....# #....# #....# #....# #....# .####.",
    "1": "..#... .##... #.#... ..#... ..#... ..#... ..#... ..#... ..#... #####.",
    "2": ".####. #....# .....# .....# ....#. ...#.. ..#... .#.... #..... ######",
    "3": ".####. #....# .....# .....# ..###. .....# .....# .....# #....# .####.",
    "4": "....#. ...##. ..#.#. .#..#. #...#. ###### ....#. ....#. ....#. ....#.",
    "5": "###### #..... #..... #####. .....# .....# .....# .....# #....# .####.",
    "6": "..###. .#.... #..... #..... #####. #....# #....# #....# #....# .####.",
    "7": "###### .....# ....#. ....#. ...#.. ...#.. ..#... ..#... ..#... ..#...",
    "8": ".####. #....# #....# #....# .####. #....# #....# #....# #....# .####.",
    "9": ".####. #....# #....# #....# #....# .##### .....# .....# ....#. .###..",
    ".": ". . . . . . . . . #",
    ":": ". . . # . . . . . #",
    "-": ".... .... .... .... #### .... .... .... .... ....",
    "/": ".....# .....# ....#. ....#. ...#.. ..#... .#.... .#.... #..... #.....",
    "'": "# # . . . . . . . .",
}
ACCENTS = {
    "acute": "...###. .###...",
    "grave": ".###... ...###.",
    "circumflex": "..###.. ##...##",
    "diaeresis": "##...## ##...##",
}
ACCENTED = {
    "É": ("E", "acute"), "È": ("E", "grave"), "Ê": ("E", "circumflex"), "Ë": ("E", "diaeresis"),
    "À": ("A", "grave"), "Â": ("A", "circumflex"), "Î": ("I", "circumflex"), "Ï": ("I", "diaeresis"),
    "Ô": ("O", "circumflex"), "Ù": ("U", "grave"), "Û": ("U", "circumflex"), "Ü": ("U", "diaeresis"),
}  # fmt: skip
SPACE = 4
CAP = 11  # cap height after the brush
ABOVE = 4  # rows above the cap: 1 free, 2 accent, 1 gap
LINE = ABOVE + CAP + 1  # 16: then the cap, then the cedilla


def _points(rows: str, top: int = 0) -> set[tuple[int, int]]:
    return {
        (x, y + top)
        for y, row in enumerate(rows.split())
        for x, c in enumerate(row)
        if c == "#"
    }


def glyph(ch: str) -> tuple[set[tuple[int, int]], int]:
    """Lit pixels (x, y from the top of the cap) and advance width, after the 2 px brush."""
    if ch == " ":
        return set(), SPACE
    if ch == "Ç":
        pts = _points(SKELETONS["C"]) | {(2, 10)}
    else:
        base, mark = ACCENTED.get(ch, (ch, ""))
        pts = _points(SKELETONS.get(base, SKELETONS["-"]))
    lit = {(x + dx, y + dy) for x, y in pts for dx in (0, 1) for dy in (0, 1)}
    advance = max(x for x, _ in lit) + 2
    if ch in ACCENTED:
        lit |= _points(ACCENTS[mark], top=-3)
    return lit, advance


def text_width(s: str, scale: int) -> int:
    return sum(glyph(c)[1] for c in s.upper()) * scale - scale


def draw_text(
    d: ImageDraw.ImageDraw, x: int, top: int, s: str, fill, scale: int
) -> None:
    """Draw s with its line box (accents included) starting at y = top."""
    for ch in s.upper():
        lit, adv = glyph(ch)
        for px, py in lit:
            x0, y0 = x + px * scale, top + (py + ABOVE) * scale
            d.rectangle((x0, y0, x0 + scale - 1, y0 + scale - 1), fill=fill)
        x += adv * scale


def fit(s: str, width: int, scale: int) -> str:
    while s and text_width(s, scale) > width:
        s = s[:-1]
    return s


# --- Board -----------------------------------------------------------------------------

WHITE, YELLOW, CYAN, RED = (255, 255, 255), (255, 200, 0), (0, 220, 255), (255, 40, 40)
EVENT, HEAT = "É12 100 L.LIBRE F", "SÉRIE 3/5"
LANES = [
    ("1", "TREMBLAY", "1:02.34", "3"),
    ("2", "GAGNON-BOUCHARD", "1:01.87", "2"),
    ("3", "NGUYEN", "", ""),
    ("4", "CÔTÉ", "59.92", "1"),
    ("5", "ST-PIERRE", "1:03.10", "4"),
    ("6", "MCKENZIE-LAFLAMME", "1:04.55", "5"),
    ("7", "LÉVESQUE", "", ""),
    ("8", "OUELLET", "1:05.01", "6"),
]


def render(w: int, h: int, scale: int) -> tuple[Image.Image, list[str]]:
    img = Image.new("RGB", (w, h), "black")
    d = ImageDraw.Draw(img)
    row_h = h // 9
    pad = (row_h - LINE * scale) // 2
    if pad < 0:
        raise SystemExit(
            f"rows are {row_h} px, the font needs {LINE * scale}: lower --scale"
        )
    gap = SPACE * scale
    left = 1

    def row(i: int) -> int:
        return i * row_h + pad

    draw_text(d, left, row(0), EVENT, CYAN, scale)
    draw_text(d, w - 1 - text_width(HEAT, scale), row(0), HEAT, CYAN, scale)

    lane_w = text_width("8", scale)
    place_w = text_width("8", scale)
    time_w = text_width("1:00.00", scale)
    name_x = left + lane_w + gap
    name_room = w - 1 - place_w - gap - time_w - gap - name_x
    cut = []
    for i, (lane, name, time, place) in enumerate(LANES, start=1):
        draw_text(d, left, row(i), lane, YELLOW, scale)
        shown = fit(name, name_room, scale)
        if shown != name:
            cut.append(f"{name} → {shown}")
        draw_text(d, name_x, row(i), shown, WHITE, scale)
        time_x = w - 1 - place_w - gap - text_width(time, scale)
        draw_text(d, time_x, row(i), time, WHITE, scale)
        draw_text(
            d,
            w - 1 - text_width(place, scale),
            row(i),
            place,
            RED if place == "1" else YELLOW,
            scale,
        )
    return img, cut


# --- Rich layout (large walls, TTF font) -----------------------------------------------

GREY, DIM = (170, 170, 170), (60, 60, 60)
RICH_EVENT = "ÉPR. 12 · 100 m LIBRE · FILLES 11-12"
RICH_NEXT = "SUIVANT  ÉPR. 13 · 100 m LIBRE · GARÇONS 11-12"
RICH_CLOCK = "1:05.4"
RICH_LANES = [
    ("1", "Léa", "TREMBLAY", "CAMO", "1:02.34", "3"),
    ("2", "Charlotte", "GAGNON-BOUCHARD", "CNQ", "1:01.87", "2"),
    ("3", "Mai", "NGUYEN", "PPO", "", ""),
    ("4", "Florence", "CÔTÉ", "CSQ", "59.92", "1"),
    ("5", "Juliette", "ST-PIERRE", "CAMO", "1:03.10", "4"),
    ("6", "Anne-Sophie", "MCKENZIE-LAFLAMME", "RIM", "1:04.55", "5"),
    ("7", "Zoé", "LÉVESQUE", "CNQ", "", ""),
    ("8", "Olivia", "OUELLET", "PPO", "1:05.01", "6"),
]
FONTS = [  # first one found; the macOS ones are for previewing only, not for shipping
    "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
]


def font_for_cap(path: str, cap: int) -> ImageFont.FreeTypeFont:
    """The largest size whose capital H is at most `cap` px tall."""
    size = cap
    while True:
        f = ImageFont.truetype(path, size + 1)
        top, bottom = f.getbbox("H")[1::2]
        if bottom - top > cap:
            return ImageFont.truetype(path, size)
        size += 1


def render_rich(
    w: int, h: int, path: str, aa: bool, cap_ratio: float, lanes: int, footer: bool
) -> tuple[Image.Image, list[str], int]:
    img = Image.new("RGB", (w, h), "black")
    d = ImageDraw.Draw(img)
    d.fontmode = "L" if aa else "1"
    head_h = round(h * 0.11)
    foot_h = head_h if footer else 0
    lane_h = (h - head_h - foot_h) // lanes
    head_h = h - foot_h - lanes * lane_h  # give the rounding to the header
    big = font_for_cap(path, round(lane_h * cap_ratio))
    cap = round(big.getbbox("H")[3] - big.getbbox("H")[1])
    head = font_for_cap(path, round(head_h * 0.55))
    club_font = font_for_cap(path, round(cap * 0.75))

    def put(x, y0, row_h, s, font, fill, right=False):
        top, bottom = font.getbbox("H")[1::2]
        y = y0 + (row_h - (bottom - top)) // 2 - top
        if right:
            x -= round(font.getlength(s))
        d.text((x, y), s, font=font, fill=fill)

    m = 3  # side margin
    put(m, 0, head_h, RICH_EVENT, head, CYAN)
    put(w - m, 0, head_h, "SÉRIE 3/5", head, CYAN, right=True)
    d.line((0, head_h - 1, w, head_h - 1), fill=DIM)

    em = round(big.getlength("0"))
    lane_x = m
    name_x = lane_x + em + em // 2
    place_r = w - m
    time_r = place_r - em - em // 2
    club_r = time_r - round(big.getlength("1:00.00")) - em
    club_w = max(round(club_font.getlength(c)) for *_, c, _, _ in RICH_LANES)
    name_room = club_r - club_w - em - name_x
    cut = []
    for i, (lane, first, last, club, time, place) in enumerate(RICH_LANES[:lanes]):
        y0 = head_h + i * lane_h
        put(lane_x, y0, lane_h, lane, big, YELLOW)
        for shown_first in (first, first[0] + ".", ""):
            name = f"{shown_first} {last}".strip()
            if big.getlength(name) <= name_room:
                break
        if shown_first != first:
            cut.append(f"{first} {last} → {name}")
        x = name_x
        if shown_first:
            put(x, y0, lane_h, shown_first, big, GREY)
            x += round(big.getlength(shown_first + " "))
        put(x, y0, lane_h, last, big, WHITE)
        put(club_r, y0, lane_h, club, club_font, CYAN, right=True)
        put(time_r, y0, lane_h, time, big, WHITE, right=True)
        put(place_r, y0, lane_h, place, big, RED if place == "1" else YELLOW, True)

    if footer:
        y0 = h - foot_h
        foot = font_for_cap(path, round(foot_h * 0.45))
        d.line((0, y0, w, y0), fill=DIM)
        put(m, y0, foot_h, RICH_NEXT, foot, GREY)
        put(w - m, y0, foot_h, RICH_CLOCK, head, YELLOW, right=True)
    return img, cut, cap


def as_leds(img: Image.Image, dot: int) -> Image.Image:
    """Each pixel as a round LED, `dot` image pixels apart."""
    w, h = img.size
    big = Image.new("RGB", (w * dot, h * dot), (12, 12, 12))
    d = ImageDraw.Draw(big)
    r = dot * 0.38
    raw = img.tobytes()
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 3
            c = (raw[i], raw[i + 1], raw[i + 2])
            if c == (0, 0, 0):
                c = (28, 28, 28)  # unlit LED
            cx, cy = x * dot + dot / 2, y * dot + dot / 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c)
    return big


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--modules", default="6x3", help="modules across x down (default 6x3)"
    )
    p.add_argument(
        "--module-px", type=int, default=48, help="pixels per module side (default 48)"
    )
    p.add_argument(
        "--pitch", type=float, default=6.67, help="pixel pitch in mm (default 6.67)"
    )
    p.add_argument(
        "--scale", type=int, default=1, help="font size multiplier (default 1)"
    )
    p.add_argument(
        "--dot",
        type=int,
        default=0,
        help="preview pixels per LED (default: fit about 2400 px wide)",
    )
    out = Path(tempfile.gettempdir()) / "led_sim"
    p.add_argument(
        "--out", type=Path, default=out, help=f"output folder (default {out})"
    )
    p.add_argument(
        "--layout",
        choices=("lanes", "rich"),
        default="lanes",
        help="lanes: bitmap font, 9 rows; rich: TTF font, first names, clubs, "
        "next event and clock (for large walls)",
    )
    p.add_argument("--font", help="TTF for --layout rich (default: first found)")
    p.add_argument(
        "--aa", action="store_true", help="anti-alias the TTF (LEDs dim per pixel)"
    )
    p.add_argument(
        "--cap",
        type=float,
        default=0.62,
        help="rich layout: capital height as a share of the lane row (default 0.62)",
    )
    p.add_argument(
        "--lanes",
        type=int,
        default=8,
        choices=range(1, 9),
        help="rich layout (default 8)",
    )
    p.add_argument(
        "--no-footer", action="store_true", help="rich layout: no next event or clock"
    )
    a = p.parse_args()

    cols, rows = (int(n) for n in a.modules.lower().split("x"))
    w, h = cols * a.module_px, rows * a.module_px
    if a.layout == "rich":
        font = a.font or next((f for f in FONTS if Path(f).exists()), None)
        if font is None:
            raise SystemExit("no TTF found: pass --font")
        img, cut, cap = render_rich(w, h, font, a.aa, a.cap, a.lanes, not a.no_footer)
        stem = f"board_{cols}x{rows}_rich{a.lanes}_{'aa' if a.aa else 'sharp'}"
        style = Path(font).stem
    else:
        img, cut = render(w, h, a.scale)
        cap = CAP * a.scale
        stem = f"board_{cols}x{rows}_s{a.scale}"
        style = f"bitmap x{a.scale}"
    dot = a.dot or max(2, 2400 // w)

    a.out.mkdir(parents=True, exist_ok=True)
    img.save(a.out / f"{stem}_native.png")
    as_leds(img, dot).save(a.out / f"{stem}_leds.png")

    mm = a.pitch
    print(
        f"{cols}x{rows} modules: {w}x{h} px, {w * mm / 1000:.2f} x {h * mm / 1000:.2f} m"
    )
    print(f"{style}: capitals {cap} px = {cap * mm / 10:.1f} cm")
    if cut:
        print(f"cut: {', '.join(cut)}")
    print(f"wrote {a.out / stem}_native.png and _leds.png")


if __name__ == "__main__":
    main()
