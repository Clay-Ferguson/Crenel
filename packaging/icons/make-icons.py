#!/usr/bin/env python3
"""Generate the installable PNG icon set from the artwork beside this script.

Only needed when the artwork changes; the PNGs it writes are checked in, so
build-deb.sh never has to convert anything (and never needs Pillow).

    uv run --with pillow packaging/icons/make-icons.py

The artwork is `source.png` (or `source.jpeg` / `source.jpg`) in this folder,
ideally square and at least 512 pixels across. A non-square image is cropped
to its centered square. If the art is a rounded-square icon drawn on a
backdrop — as Sonar's render is — set CROP to the icon's outer edge and RADIUS
to its corner radius (both in source pixels), and everything outside is
masked to transparent so it does not show as a tile on a light panel.
"""
from pathlib import Path

from PIL import Image, ImageDraw

CROP: tuple[int, int, int, int] | None = None  # left, top, right, bottom
RADIUS = 0  # corner radius in source pixels, applied when CROP is set
SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
SS = 4  # mask supersampling, for a clean antialiased corner

root = Path(__file__).resolve().parent
sources = [root / name for name in ("source.png", "source.jpeg", "source.jpg")]
source = next((path for path in sources if path.is_file()), None)
if source is None:
    raise SystemExit(f"No artwork found: put source.png (or .jpeg) in {root}")

art = Image.open(source).convert("RGBA")
if CROP is not None:
    art = art.crop(CROP)
else:
    side = min(art.size)
    left, top = (art.width - side) // 2, (art.height - side) // 2
    art = art.crop((left, top, left + side, top + side))

if CROP is not None and RADIUS:
    side = art.width
    mask = Image.new("L", (side * SS, side * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, side * SS - 1, side * SS - 1), radius=RADIUS * SS, fill=255
    )
    art.putalpha(mask.resize((side, side), Image.Resampling.LANCZOS))

for size in SIZES:
    out = root / "hicolor" / f"{size}x{size}" / "apps" / "crenel.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    art.resize((size, size), Image.Resampling.LANCZOS).save(out, optimize=True)
    print(out.relative_to(root.parents[1]))
