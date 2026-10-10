"""Vectorised BC1 (DXT1) encoder/decoder in numpy — principal-axis endpoints, opaque 4-colour mode only."""
import numpy as np


def _to_blocks(rgb):
    h, w, _ = rgb.shape
    assert h % 4 == 0 and w % 4 == 0, "pad images to multiples of 4"
    return rgb.reshape(h // 4, 4, w // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(h // 4, w // 4, 16, 3)


def _q565(c):
    r = np.clip(np.rint(c[..., 0] * 31 / 255), 0, 31).astype(np.uint32)
    g = np.clip(np.rint(c[..., 1] * 63 / 255), 0, 63).astype(np.uint32)
    b = np.clip(np.rint(c[..., 2] * 31 / 255), 0, 31).astype(np.uint32)
    return (r << 11) | (g << 5) | b


def _e565(v):
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.float32)


def encode(rgb: np.ndarray) -> bytes:
    """rgb: HxWx3 uint8 (H, W multiples of 4). Returns linear BC1 blocks."""
    px = _to_blocks(rgb.astype(np.float32))
    mean = px.mean(2, keepdims=True)
    d = px - mean
    cov = np.einsum("...ni,...nj->...ij", d, d)
    axis = np.ones(cov.shape[:-1], np.float32)
    for _ in range(8):  # power iteration for the principal axis
        axis = np.einsum("...ij,...j->...i", cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=-1, keepdims=True), 1e-6)
    t = np.einsum("...ni,...i->...n", d, axis)
    m = mean[..., 0, :]
    e0 = m + axis * t.max(-1, keepdims=True)
    e1 = m + axis * t.min(-1, keepdims=True)
    c0, c1 = _q565(e0), _q565(e1)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    p0, p1 = _e565(c0), _e565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 2)  # bh,bw,4,3
    dist = ((px[:, :, :, None, :] - pal[:, :, None, :, :]) ** 2).sum(-1)
    idx = dist.argmin(-1).astype(np.uint32)
    idx = np.where((c0 == c1)[..., None], 0, idx)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(-1, dtype=np.uint64).astype(np.uint32)
    out = np.zeros(c0.shape + (2,), np.uint32)
    out[..., 0] = c0 | (c1 << 16)
    out[..., 1] = bits
    return out.astype("<u4").tobytes()


def decode(data: bytes, w: int, h: int) -> np.ndarray:
    bw, bh = w // 4, h // 4
    a = np.frombuffer(data, "<u4", bw * bh * 2).reshape(bh, bw, 2)
    c0, c1, bits = a[..., 0] & 0xFFFF, a[..., 0] >> 16, a[..., 1]
    p0, p1 = _e565(c0), _e565(c1)
    four = (c0 > c1)[..., None]
    p2 = np.where(four, (2 * p0 + p1) / 3, (p0 + p1) / 2)
    p3 = np.where(four, (p0 + 2 * p1) / 3, 0)
    pal = np.stack([p0, p1, p2, p3], 2)
    idx = (bits[..., None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    px = np.take_along_axis(pal, idx[..., None].astype(np.int64).repeat(3, -1), 2)
    return px.reshape(bh, bw, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(h, w, 3).astype(np.uint8)
