# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow"]
# ///
"""Build proof-of-concept title mods that answer: does a GFX flipbook work, how big can title.tpf get,
and does the engine decode JPEG bitmaps embedded in a GFX.

Each variant goes to build/<variant>/dvdroot_ps4/menu/. Install one with install.ps1.
"""
import argparse
import io
import os
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import bc1
import gfx
from bbfmt import DXGI_BC1, Texture, Tpf, dcx_read, dcx_write, deswizzle, swizzle

HERE = Path(__file__).resolve().parent
# the game's original menu folder (unmodified dump); override with BB_MENU=<game>\dvdroot_ps4\menu
MENU = Path(os.environ.get("BB_MENU") or HERE.parent / "dvdroot_ps4" / "menu")
# The engine picks one of these at boot; CUSA03173 (EU) with the DLC unlocked uses title_dlc_eu.gfx.
BG_NAMES = {"title.gfx": "MENU_Title_00001", "title_dlc.gfx": "MENU_Title_00004", "title_dlc_eu.gfx": "MENU_Title_00001"}

VARIANTS = {
    # name: (frames, width, height, hold)  hold = timeline frames per image at 30 fps
    "poc_a_60x1024": (60, 1024, 576, 2),     # ~17 MB, 4 s loop at 15 fps — does a flipbook work at all?
    "poc_b_240x1024": (240, 1024, 576, 1),   # ~68 MB, 8 s at 30 fps — memory stress
    "poc_c_120x1920": (120, 1920, 1088, 1),  # ~120 MB full-res — heavy stress
}


def original_background(tpf, name, w, h):
    t = next(x for x in tpf.textures if x.name == name)
    img = bc1.decode(deswizzle(t.data, t.w, t.h, 8), t.w, t.h)[:, :1920]
    canvas = np.zeros((1080, 1920, 3), np.uint8)
    canvas[:1024] = img
    return Image.fromarray(canvas).resize((w, h * 1080 // 1088 if h == 1088 else h), Image.LANCZOS)


def poc_frame(base, k, n, label, w, h):
    im = Image.new("RGB", (w, h))
    im.paste(base, (0, 0))
    im = Image.blend(im, Image.new("RGB", (w, h)), 0.35)
    d = ImageDraw.Draw(im, "RGBA")
    x = int((k / n) * (w + w // 4)) - w // 8
    d.rectangle([x, 0, x + w // 8, h], fill=(255, 220, 160, 70))  # sweeping light band shows motion
    s = max(12, h // 14)
    font = ImageFont.load_default(size=s)
    d.text((s, s), f"{label}  frame {k + 1:03d}/{n}", font=font, fill=(255, 255, 255))
    d.ellipse([w - 3 * s, s, w - s, 3 * s], fill=(200, 30, 30, 255) if k % 2 == 0 else (30, 200, 30, 255))
    return np.asarray(im)


def patch_gfx(raw, bg_export, image_specs, fill_type, sx, sy, hold, overlay=None):
    """Replace the background shape placement with a flipbook sprite of the given external images.
    overlay=(export, x, y) puts a 1:1 clipped bitmap (with transparent border) above the flipbook."""
    g = gfx.Gfx(raw)
    bg_id = next(cid for cid, name in g.external_images().items() if name == bg_export)
    shape_idx, _ = g.find_shape_using_bitmap(bg_id)
    shape_tag = g.tags[shape_idx]
    sprite, place_idx = g.find_place_of(shape_tag.char_id)
    nid = g.max_char_id() + 1
    new_tags, shape_ids = [], []
    for name, w, h in image_specs:
        new_tags.append(gfx.external_image2(nid, name, w, h)); img_id = nid; nid += 1
        new_tags.append(gfx.Tag(shape_tag.code, gfx.clone_bitmap_shape(shape_tag.body, nid, img_id, sx, sy, fill_type)))
        shape_ids.append(nid); nid += 1
    new_tags.append(gfx.flipbook_sprite(nid, shape_ids, hold))
    book_id = nid; nid += 1
    place = sprite.children[place_idx]
    depth = struct.unpack_from("<H", place.body, 1)[0]
    if overlay:
        export, ox, oy, ow, oh = overlay
        new_tags.append(gfx.external_image2(nid, export, ow, oh)); img_id = nid; nid += 1
        new_tags.append(gfx.Tag(shape_tag.code, gfx.clone_bitmap_shape(shape_tag.body, nid, img_id, 20 * 65536, 20 * 65536, 0x41, ox * 20, oy * 20)))
        logo_id = nid
        depth -= 1  # background drops one depth (free in the original layout), logo takes its old slot
        sprite.children.insert(place_idx + 1, gfx.place_new(depth + 1, logo_id))
    g.tags[shape_idx + 1:shape_idx + 1] = new_tags
    sprite.children[place_idx] = gfx.Tag(place.code, place.body[:1] + struct.pack("<HH", depth, book_id) + place.body[5:])
    return g.build()


def build_flipbook(variant, frames, w, h, hold):
    tpf = Tpf.parse(dcx_read((MENU / "title.tpf.dcx").read_bytes()))
    base = original_background(tpf, "MENU_Title_00001", w, h)
    specs = []
    for k in range(frames):
        name = f"MENU_TitleV_{k:04d}"
        rgb = poc_frame(base, k, frames, variant, w, h) if h != 1088 else np.pad(poc_frame(base, k, frames, variant, w, 1080), ((0, 8), (0, 0), (0, 0)))
        tpf.textures.append(Texture(name, swizzle(bc1.encode(rgb), w, h, 8), w, h, DXGI_BC1))
        specs.append((name, w, h))
    # 1 bitmap pixel -> 1920/w stage pixels (16.16 fixed point, in twips)
    scale = int(round(20 * 65536 * 1920 / w))
    fill = 0x42 if w == 1920 else 0x41  # upscaled frames use smoothed + clipped sampling
    out = HERE / "build" / variant / "dvdroot_ps4" / "menu"
    out.mkdir(parents=True, exist_ok=True)
    raw_tpf = tpf.build()
    (out / "title.tpf.dcx").write_bytes(dcx_write(raw_tpf, 6))
    for gname, bg in BG_NAMES.items():
        (out / gname).write_bytes(patch_gfx((MENU / gname).read_bytes(), bg, specs, fill, scale, scale, hold))
    print(f"{variant}: {frames} frames {w}x{h}, title.tpf {len(raw_tpf) / 2**20:.1f} MB raw")


def build_jpeg_test():
    """Background replaced by a tiled JPEG embedded in the GFX (DefineBitsJPEG2) — no TPF change."""
    im = Image.new("RGB", (512, 256), (40, 0, 60))
    d = ImageDraw.Draw(im)
    d.text((40, 90), "JPEG OK", font=ImageFont.load_default(size=72), fill=(255, 255, 0))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
    out = HERE / "build" / "poc_jpeg" / "dvdroot_ps4" / "menu"
    out.mkdir(parents=True, exist_ok=True)
    for gname, bg in BG_NAMES.items():
        g = gfx.Gfx((MENU / gname).read_bytes())
        bg_id = next(cid for cid, name in g.external_images().items() if name == bg)
        shape_idx, _ = g.find_shape_using_bitmap(bg_id)
        shape_tag = g.tags[shape_idx]
        jid = g.max_char_id() + 1
        jpeg_tag = gfx.Tag(21, struct.pack("<H", jid) + buf.getvalue(), long=True)  # DefineBitsJPEG2
        g.tags.insert(shape_idx, jpeg_tag)
        shape_tag.body = gfx.clone_bitmap_shape(shape_tag.body, shape_tag.char_id, jid, 20 * 65536, 20 * 65536, 0x40)
        (out / gname).write_bytes(g.build())
    print("poc_jpeg: built")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("variants", nargs="*", default=[*VARIANTS, "poc_jpeg"])
    args = ap.parse_args()
    for v in args.variants:
        build_jpeg_test() if v == "poc_jpeg" else build_flipbook(v, *VARIANTS[v])
