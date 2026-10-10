# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow", "imageio-ffmpeg"]
# ///
"""Dense contact sheet + per-frame change curve (spots cuts and flashes).

  uv run sheet.py clip.mp4 [cols rows]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import video_tools as vt

p = Path(sys.argv[1])
cols, rows = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (4, 4)
w, h, fps = vt.probe(p)
f = vt.read_native(p, 480, 270)
d = [float(np.abs(f[i].astype(int) - f[i - 1].astype(int)).mean()) for i in range(1, len(f))]
lum = [float(x.mean()) for x in f]
print(f"{p.name}: {w}x{h} {fps:g}fps {len(f)} frames ({len(f) / fps:.2f}s)")
print("frame diffs > 3x median:", [(i + 1, round(v, 1)) for i, v in enumerate(d) if v > 3 * np.median(d)])
print("luma per 0.5s:", [round(lum[i], 1) for i in range(0, len(f), max(1, round(fps / 2)))])
picks = np.linspace(0, len(f) - 1, cols * rows).round().astype(int)
sheet = Image.new("RGB", (480 * cols, 270 * rows))
for k, i in enumerate(picks):
    im = Image.fromarray(f[i])
    ImageDraw.Draw(im).text((6, 6), f"#{i} {i / fps:.2f}s", fill=(255, 255, 0))
    sheet.paste(im, ((k % cols) * 480, (k // cols) * 270))
out = p.with_name(p.stem + "_sheet16.jpg")
sheet.save(out, quality=88)
print("sheet:", out)
