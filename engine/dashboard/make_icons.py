"""Render the home-screen icons in ui/icons/ from the app's own logo mark.

The mark is the `Logo` component in ui/app.js: three concentric dashed rings on the app
background, rotated -90 so each ring's gap starts from the top. Keeping this script beside the
PNGs means the icons have a source — a committed binary nobody can regenerate is the thing that
goes stale the first time the mark changes.

Needs Pillow, which is NOT an aios runtime dependency. This is a build tool, run by hand:

    python engine/dashboard/make_icons.py

Icons are full-bleed on the app background, so one file serves `purpose: "any maskable"`: the
rings span 70% of the tile including stroke, inside the 80% maskable safe zone, so a circular or
squircle OS crop takes only background.
"""
from pathlib import Path

from PIL import Image, ImageDraw

BG = "#0c0d0f"       # --bg-0
FG = "#f4f5f7"       # --accent
SS = 4               # supersample, then downscale for antialiasing

# (radius, dash-on %, dash-offset %) — verbatim from the three <circle> elements in app.js.
RINGS = [(320, 84, 7), (205, 78, 26), (82, 80, 10)]
STROKE = 62          # stroke-width in the same 1000x1000 viewBox
OUT = Path(__file__).parent / "ui" / "icons"


def render(px):
    n = px * SS
    img = Image.new("RGB", (n, n), BG)
    d = ImageDraw.Draw(img)
    k = n / 1000.0                       # viewBox units -> pixels
    w = max(1, round(STROKE * k))
    for r, on, off in RINGS:
        # Pillow strokes INWARD from the bounding ellipse; SVG centres the stroke on the path.
        # Grow the box by half the stroke so the ring lands where app.js draws it — without this
        # every ring is half a stroke too small, and the innermost one closes into a disc.
        rr = r * k + w / 2
        box = [n / 2 - rr, n / 2 - rr, n / 2 + rr, n / 2 + rr]
        # SVG dashoffset shifts the pattern backwards; pathLength=100, so 1 unit = 3.6 degrees.
        # The -90 matches the group's rotate(-90 500 500): the gap opens at the top.
        start = -off * 3.6 - 90
        d.arc(box, start, start + on * 3.6, fill=FG, width=w)
    return img.resize((px, px), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for px, name in ((192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")):
        p = OUT / name
        render(px).save(p, optimize=True)
        print(f"{p.relative_to(Path(__file__).parents[2])}  {p.stat().st_size} bytes")
