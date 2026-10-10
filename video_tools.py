"""ffmpeg helpers: probe, decode at native rate, motion-compensated frame-rate conversion (loop-aware)."""
import re
import subprocess

import imageio_ffmpeg
import numpy as np

FF = imageio_ffmpeg.get_ffmpeg_exe()


def probe(path):
    info = subprocess.run([FF, "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Video: .*?, (\d+)x(\d+).*?, ([\d.]+) fps", info)
    return int(m[1]), int(m[2]), float(m[3])


def read_native(path, w, h, start=0.0, duration=None):
    """All frames at the clip's own frame rate, scaled/cropped to w x h."""
    cmd = [FF, "-v", "error", "-ss", str(start)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(path), "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)


def retime(frames, src_fps, dst_fps, loop=False, mode="mci"):
    """Motion-interpolate to dst_fps. loop=True also interpolates across the last->first seam and returns
    exactly round(n * dst/src) frames that tile seamlessly."""
    n, h, w, _ = frames.shape
    want = round(n * dst_fps / src_fps)
    pad = 6
    src = np.concatenate([frames, frames[:pad]]) if loop else np.concatenate([frames, frames[-1:].repeat(pad, 0)])
    if mode == "mci":
        vf = f"minterpolate=fps={dst_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    else:  # simple frame blending, no motion estimation
        vf = f"minterpolate=fps={dst_fps}:mi_mode=blend"
    cmd = [FF, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(src_fps), "-i", "-",
           "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    out = subprocess.run(cmd, input=src.tobytes(), capture_output=True, check=True).stdout
    res = np.frombuffer(out, np.uint8).reshape(-1, h, w, 3)
    assert len(res) >= want, (len(res), want)
    return res[:want]


def write_mp4(frames, fps, path):
    n, h, w, _ = frames.shape
    cmd = [FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, input=frames.tobytes(), check=True)
