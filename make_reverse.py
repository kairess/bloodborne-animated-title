# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow", "imageio-ffmpeg"]
# ///
"""Title background = one clip played backwards once (last frame -> first frame), then the first frame stays.

  uv run make_reverse.py --clip vidu/q4_final_bloodmoon_1080.mp4 --name bloodmoon_rev --size 1920x1088

The clip is motion-interpolated to the 30 fps GFX timeline. Logo/TM layers of the menu are left untouched.
"""
import argparse

import numpy as np

import bc1
import video_tools as vt
from bbfmt import DXGI_BC1, Texture, Tpf, dcx_read, dcx_write, swizzle
from make_intro_loop import GFX_FPS, pad_rows, patch_gfx_intro_loop
from make_poc import BG_NAMES, HERE, MENU


def soften_reds(frames, fps, strength, until, fade=0.5):
    """Lower the saturation of strongly saturated red pixels (the bleeding/ash title) in frames before `until`
    seconds, easing the effect out over `fade` seconds so nothing pops. Other colours are left alone."""
    out = frames.copy()
    for i in range(min(len(frames), round((until + fade) * fps))):
        t = i / fps
        amt = strength * (1.0 if t <= until else max(0.0, 1 - (t - until) / fade))
        if amt <= 0:
            continue
        x = frames[i].astype(np.float32) / 255
        r, g, b = x[..., 0], x[..., 1], x[..., 2]
        mx, mn = x.max(-1), x.min(-1)
        sat = (mx - mn) / np.maximum(mx, 1e-3)
        red = np.clip((r - np.maximum(g, b)) / np.maximum(mx - mn, 1e-3), 0, 1)  # 1 = pure red hue
        wgt = np.clip((sat - 0.25) / 0.3, 0, 1) * red * (mx > 0.08)
        y = (0.299 * r + 0.587 * g + 0.114 * b)[..., None]
        k = (1 - amt * wgt)[..., None]
        out[i] = (np.clip(y + (x - y) * k, 0, 1) * 255).round().astype(np.uint8)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--name", default="reverse")
    ap.add_argument("--size", default="1920x1088")
    ap.add_argument("--interp", choices=["mci", "blend", "none"], default="mci")
    ap.add_argument("--budget-mb", type=float, default=400)
    ap.add_argument("--red-desat", type=float, default=0, help="0..1: how much to desaturate the red title/ash")
    ap.add_argument("--red-until", type=float, default=4.2, help="clip time (s, forward) after which reds are left alone")
    ap.add_argument("--preview-only", action="store_true")
    args = ap.parse_args()

    w, h = map(int, args.size.lower().split("x"))
    assert w % 32 == 0 and h % 32 == 0
    vis_h = round(w * 1080 / 1920)
    _, _, fps = vt.probe(args.clip)
    f = vt.read_native(args.clip, w, vis_h)
    if args.red_desat:
        orig = f
        f = soften_reds(f, fps, args.red_desat, args.red_until)
        build = HERE / "build" / args.name
        build.mkdir(parents=True, exist_ok=True)
        # before/after of the title part (forward time), top = original, bottom = softened
        n = round((args.red_until + 0.5) * fps)
        cmp = np.concatenate([orig[:n], f[:n]], axis=1)[:, ::2, ::2]
        vt.write_mp4(np.ascontiguousarray(cmp[::-1]), fps, build / "compare_title.mp4")
    f = f[::-1].copy()
    if args.interp != "none":
        f = vt.retime(f, fps, GFX_FPS, loop=False, mode=args.interp)
    mb = len(f) * w * h / 2 / 2**20
    print(f"{len(f)} frames @30fps ({len(f) / GFX_FPS:.2f}s) at {w}x{h} = {mb:.1f} MB")
    if mb > args.budget_mb:
        raise SystemExit(f"over budget {args.budget_mb} MB")

    build = HERE / "build" / args.name
    build.mkdir(parents=True, exist_ok=True)
    vt.write_mp4(f, GFX_FPS, build / "preview_30fps.mp4")
    if args.preview_only:
        return

    tpf = Tpf.parse(dcx_read((MENU / "title.tpf.dcx").read_bytes()))
    specs = []
    for k in range(len(f)):
        name = f"MENU_TitleI_{k:04d}"
        rgb = pad_rows(f[k:k + 1], h)[0]
        tpf.textures.append(Texture(name, swizzle(bc1.encode(rgb), w, h, 8), w, h, DXGI_BC1))
        specs.append((name, w, h))
    del f
    scale = int(round(20 * 65536 * 1920 / w))
    fill = 0x42 if w == 1920 else 0x41
    out = build / "dvdroot_ps4" / "menu"
    out.mkdir(parents=True, exist_ok=True)
    raw = tpf.build()
    (out / "title.tpf.dcx").write_bytes(dcx_write(raw, 6))
    for gname, bg in BG_NAMES.items():
        (out / gname).write_bytes(patch_gfx_intro_loop((MENU / gname).read_bytes(), bg, specs, [], fill, scale, hide_tm=False))
    print(f"wrote {out} (title.tpf {len(raw) / 2**20:.1f} MB raw), preview {build / 'preview_30fps.mp4'}")


if __name__ == "__main__":
    main()
