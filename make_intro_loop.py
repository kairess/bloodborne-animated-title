# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow", "imageio-ffmpeg"]
# ///
"""Title background = intro clip played once, then a seamless loop clip forever.

  uv run make_intro_loop.py --intro vidu/intro.mp4 --intro-end 3.5 --loop vidu/loop.mp4 --name storm --size 1024x576

Both clips are motion-interpolated to the 30 fps GFX timeline so every timeline frame shows a new image.
"""
import argparse
import struct
from pathlib import Path

import numpy as np

import bc1
import gfx
import video_tools as vt
from bbfmt import DXGI_BC1, Texture, Tpf, dcx_read, dcx_write, swizzle
from make_poc import BG_NAMES, HERE, MENU

GFX_FPS = 30
TM_TEXT = "StaticRegionText_401100"  # the floating (TM) mark next to the logo


def pad_rows(frames, h):
    return np.pad(frames, ((0, 0), (0, h - frames.shape[1]), (0, 0), (0, 0)), mode="edge") if frames.shape[1] < h else frames


def hide_placement_by_name(g, name):
    """Move a named PlaceObject2 far off-stage (keeps the object so the engine can still find it)."""
    for t in g.tags:
        if t.code != gfx.TAG_DEFINESPRITE:
            continue
        for i, c in enumerate(t.children):
            if c.code == gfx.TAG_PLACEOBJECT2 and c.body[0] & 0x20 and name.encode() + b"\0" in c.body:
                flags = c.body[0]
                assert flags & 0x04 and not flags & 0x08, "expected matrix and no cxform"
                p = 3 + (2 if flags & 2 else 0)
                _, end = gfx.read_matrix(c.body, p)
                c.body = c.body[:p] + gfx.write_matrix(65536, 65536, -40000, -40000) + c.body[end:]
                return True
    return False


def find_placement_by_name(g, name):
    for t in g.tags:
        if t.code == gfx.TAG_DEFINESPRITE:
            for c in t.children:
                if c.code == gfx.TAG_PLACEOBJECT2 and c.body[0] & 0x20 and name.encode() + b"\0" in c.body:
                    return t, c
    return None, None


def extend_with_reveal(sprite, depth, matrix, at, total):
    """Give a one-frame sprite a `total`-frame timeline in which the (already hidden) object at `depth` is moved
    back to `matrix` on frame `at`. The instance itself is never recreated, so text the game filled into it at
    load time (localized StaticRegionText) survives — a freshly placed copy would show its placeholder."""
    kids = [c for c in sprite.children if c.code != gfx.TAG_END]  # frame 0 incl. its ShowFrame
    for k in range(1, total):
        if k == at:
            kids.append(gfx.Tag(gfx.TAG_PLACEOBJECT2, bytes([0x05]) + depth + matrix))  # move + matrix, same instance
        kids.append(gfx.Tag(gfx.TAG_SHOWFRAME, b""))
    kids.append(gfx.Tag(gfx.TAG_END, b""))
    sprite.children = kids
    sprite.sprite_hdr = sprite.sprite_hdr[:2] + struct.pack("<H", total)


def patch_gfx_intro_loop(raw, bg_export, intro_specs, loop_specs, fill, scale, hide_tm=True, loop_order=None, tm_at=None):
    """loop_order: optional play order of loop_specs indices (repeats allowed, e.g. a ping-pong).
    tm_at: frame index on which the TM text appears; until then it sits off-stage (same instance throughout)."""
    g = gfx.Gfx(raw)
    tm_sprite = tm_reveal = None
    if tm_at is not None:
        tm_sprite, orig = find_placement_by_name(g, TM_TEXT)
        assert orig is not None, "TM text placement not found"
        _, end = gfx.read_matrix(orig.body, 5)
        tm_reveal = (orig.body[1:3], orig.body[5:end])  # depth, original matrix bytes
        assert sum(c.code == gfx.TAG_SHOWFRAME for c in tm_sprite.children) == 1, "expected a single-frame sprite"
        hide_tm = True
    if hide_tm:  # before any new tags exist, so only the original placement can match
        assert hide_placement_by_name(g, TM_TEXT), "TM text placement not found"
    bg_id = next(cid for cid, n in g.external_images().items() if n == bg_export)
    shape_idx, _ = g.find_shape_using_bitmap(bg_id)
    shape_tag = g.tags[shape_idx]
    sprite, place_idx = g.find_place_of(shape_tag.char_id)
    nid = g.max_char_id() + 1
    new_tags = []

    def add_frames(specs):
        nonlocal nid
        ids = []
        for name, w, h in specs:
            new_tags.append(gfx.external_image2(nid, name, w, h)); img = nid; nid += 1
            new_tags.append(gfx.Tag(shape_tag.code, gfx.clone_bitmap_shape(shape_tag.body, nid, img, scale, scale, fill)))
            ids.append(nid); nid += 1
        return ids

    intro_ids, loop_ids = add_frames(intro_specs), add_frames(loop_specs)
    loop_sprite = None
    if loop_ids:
        loop_sprite = nid; nid += 1
        new_tags.append(gfx.flipbook_sprite(loop_sprite, [loop_ids[i] for i in loop_order] if loop_order else loop_ids, 1))
    # outer sprite: intro frames, then the self-looping loop sprite, then a very long tail so the intro
    # doesn't replay (65535 frames max = ~36 min at 30 fps)
    outer = nid; nid += 1
    kids = []
    for k, sid in enumerate(intro_ids):
        kids += [gfx.place_new(1, sid) if k == 0 else gfx.place_replace(1, sid), gfx.Tag(gfx.TAG_SHOWFRAME, b"")]
    if loop_sprite:  # without a loop the last intro frame simply stays on screen
        kids.append(gfx.place_replace(1, loop_sprite) if kids else gfx.place_new(1, loop_sprite))
    total = 65000
    kids += [gfx.Tag(gfx.TAG_SHOWFRAME, b"") for _ in range(total - len(intro_ids))]
    kids.append(gfx.Tag(gfx.TAG_END, b""))
    new_tags.append(gfx.Tag(gfx.TAG_DEFINESPRITE, b"", kids, struct.pack("<HH", outer, total)))

    g.tags[shape_idx + 1:shape_idx + 1] = new_tags
    place = sprite.children[place_idx]
    sprite.children[place_idx] = gfx.Tag(place.code, place.body[:3] + struct.pack("<H", outer) + place.body[5:])
    if tm_reveal:
        # the TM's sprite runs in step with the outer sprite (both start on the same frame)
        extend_with_reveal(tm_sprite, *tm_reveal, tm_at, total)
    return g.build()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intro", type=Path, required=True)
    ap.add_argument("--intro-end", type=float, help="cut the intro here (seconds); the loop clip should start on this frame")
    ap.add_argument("--loop", type=Path, required=True)
    ap.add_argument("--loop-drop-last", action="store_true", default=True)
    ap.add_argument("--name", default="intro_loop")
    ap.add_argument("--size", default="1024x576")
    ap.add_argument("--interp", choices=["mci", "blend", "none"], default="mci")
    ap.add_argument("--budget-mb", type=float, default=40)
    ap.add_argument("--keep-tm", action="store_true")
    args = ap.parse_args()

    w, h = map(int, args.size.lower().split("x"))
    assert w % 32 == 0 and h % 32 == 0
    vis_h = round(w * 1080 / 1920)

    _, _, ifps = vt.probe(args.intro)
    intro = vt.read_native(args.intro, w, vis_h, 0, args.intro_end)
    if args.intro_end is None:
        intro = intro[:-1]  # the loop clip starts on the intro's last frame; don't show it twice
    _, _, lfps = vt.probe(args.loop)
    loop = vt.read_native(args.loop, w, vis_h)
    if args.loop_drop_last:
        loop = loop[:-1]
    if args.interp != "none":
        intro = vt.retime(intro, ifps, GFX_FPS, loop=False, mode=args.interp)
        loop = vt.retime(loop, lfps, GFX_FPS, loop=True, mode=args.interp)
    n = len(intro) + len(loop)
    mb = n * w * h / 2 / 2**20
    print(f"intro {len(intro)} + loop {len(loop)} frames at {w}x{h} = {mb:.1f} MB")
    if mb > args.budget_mb:
        raise SystemExit(f"over budget {args.budget_mb} MB")

    preview = HERE / "build" / args.name
    preview.mkdir(parents=True, exist_ok=True)
    vt.write_mp4(np.concatenate([intro, loop, loop]), GFX_FPS, preview / "preview_30fps.mp4")

    tpf = Tpf.parse(dcx_read((MENU / "title.tpf.dcx").read_bytes()))
    def add(frames, prefix):
        specs = []
        for k, rgb in enumerate(pad_rows(frames, h)):
            name = f"{prefix}{k:04d}"
            tpf.textures.append(Texture(name, swizzle(bc1.encode(rgb), w, h, 8), w, h, DXGI_BC1))
            specs.append((name, w, h))
        return specs
    ispecs, lspecs = add(intro, "MENU_TitleI_"), add(loop, "MENU_TitleL_")
    scale = int(round(20 * 65536 * 1920 / w))
    fill = 0x42 if w == 1920 else 0x41
    out = preview / "dvdroot_ps4" / "menu"
    out.mkdir(parents=True, exist_ok=True)
    raw = tpf.build()
    (out / "title.tpf.dcx").write_bytes(dcx_write(raw, 6))
    for gname, bg in BG_NAMES.items():
        (out / gname).write_bytes(patch_gfx_intro_loop((MENU / gname).read_bytes(), bg, ispecs, lspecs, fill, scale, not args.keep_tm))
    print(f"wrote {out} (title.tpf {len(raw) / 2**20:.1f} MB raw), preview {preview / 'preview_30fps.mp4'}")


if __name__ == "__main__":
    main()
