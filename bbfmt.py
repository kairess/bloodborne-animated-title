"""Bloodborne (PS4) container formats: DCX (DFLT) and TPF, plus PS4 BC texture tiling."""
import struct
import zlib

import numpy as np

# ---------------------------------------------------------------- DCX

def dcx_read(data: bytes) -> bytes:
    assert data[:4] == b"DCX\0" and data[0x28:0x2C] == b"DFLT", "only DCX DFLT supported"
    usize, csize = struct.unpack_from(">II", data, 0x1C)
    out = zlib.decompress(data[0x4C:0x4C + csize])
    assert len(out) == usize
    return out


def dcx_write(raw: bytes, level: int = 9) -> bytes:
    comp = zlib.compress(raw, level)
    hdr = b"DCX\0" + struct.pack(">IIIII", 0x10000, 0x18, 0x24, 0x44, 0x4C)
    hdr += b"DCS\0" + struct.pack(">II", len(raw), len(comp))
    hdr += b"DCP\0DFLT" + struct.pack(">I", 0x20) + bytes([9, 0, 0, 0]) + bytes(12) + bytes([0, 1, 1, 0])
    hdr += b"DCA\0" + struct.pack(">I", 8)
    assert len(hdr) == 0x4C
    return hdr + comp

# ---------------------------------------------------------------- PS4 tiling

def _morton_xy():
    xs, ys = [], []
    for i in range(64):
        xs.append((i & 1) | ((i >> 1) & 2) | ((i >> 2) & 4))
        ys.append(((i >> 1) & 1) | ((i >> 2) & 2) | ((i >> 3) & 4))
    return np.array(xs), np.array(ys)

_MX, _MY = _morton_xy()


def tiled_size(w, h, bpb):
    bw, bh = (w + 3) // 4, (h + 3) // 4
    return ((bw + 7) // 8 * 8) * ((bh + 7) // 8 * 8) * bpb


def deswizzle(src: bytes, w, h, bpb):
    """PS4 tiled BC blocks -> linear BC blocks."""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    tx, ty = (bw + 7) // 8, (bh + 7) // 8
    a = np.frombuffer(src, np.uint8, tx * ty * 64 * bpb).reshape(ty, tx, 64, bpb)
    out = np.zeros((ty * 8, tx * 8, bpb), np.uint8)
    for i in range(64):
        out[_MY[i]::8, _MX[i]::8] = a[:, :, i]
    return out[:bh, :bw].tobytes()


def swizzle(lin: bytes, w, h, bpb):
    """Linear BC blocks -> PS4 tiled BC blocks (padded to 8x8-block tiles)."""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    tx, ty = (bw + 7) // 8, (bh + 7) // 8
    full = np.zeros((ty * 8, tx * 8, bpb), np.uint8)
    full[:bh, :bw] = np.frombuffer(lin, np.uint8).reshape(bh, bw, bpb)
    out = np.zeros((ty, tx, 64, bpb), np.uint8)
    for i in range(64):
        out[:, :, i] = full[_MY[i]::8, _MX[i]::8]
    return out.tobytes()

# ---------------------------------------------------------------- TPF (PS4)

DXGI_BC1, DXGI_BC7 = 71, 98
BPB = {DXGI_BC1: 8, DXGI_BC7: 16}
FORMAT_BYTE = {DXGI_BC1: 0, DXGI_BC7: 102}


class Texture:
    def __init__(self, name, data, w, h, dxgi, fmt=None, typ=0, mips=1, flags1=0, tex_count=1, unk2=0xD):
        self.name, self.data, self.w, self.h, self.dxgi = name, data, w, h, dxgi
        self.fmt = FORMAT_BYTE[dxgi] if fmt is None else fmt
        self.typ, self.mips, self.flags1, self.tex_count, self.unk2 = typ, mips, flags1, tex_count, unk2

    def __repr__(self):
        return f"<{self.name} {self.w}x{self.h} dxgi={self.dxgi} fmt={self.fmt} {len(self.data)}B>"


class Tpf:
    def __init__(self):
        self.textures = []
        self.platform, self.flag2, self.encoding = 4, 3, 1

    @classmethod
    def parse(cls, d: bytes):
        assert d[:4] == b"TPF\0"
        t = cls()
        count = struct.unpack_from("<i", d, 8)[0]
        t.platform, t.flag2, t.encoding = d[12], d[13], d[14]
        p = 16
        for _ in range(count):
            off, size, fmt, typ, mips, fl1 = struct.unpack_from("<IiBBBB", d, p); p += 12
            w, h, tc, unk2 = struct.unpack_from("<hhii", d, p); p += 12
            name_off, has_float, dxgi = struct.unpack_from("<Iii", d, p); p += 12
            assert has_float == 0, "float struct not supported"
            if t.encoding == 1:
                e = name_off
                while d[e:e + 2] != b"\0\0":
                    e += 2
                name = d[name_off:e].decode("utf-16-le")
            else:
                name = d[name_off:d.index(b"\0", name_off)].decode("shift_jis")
            t.textures.append(Texture(name, d[off:off + size], w, h, dxgi, fmt, typ, mips, fl1, tc, unk2))
        return t

    def build(self) -> bytes:
        n = len(self.textures)
        names = b""
        name_offs = []
        base_names = 16 + 36 * n
        for tex in self.textures:
            name_offs.append(base_names + len(names))
            names += tex.name.encode("utf-16-le" if self.encoding == 1 else "shift_jis") + (b"\0\0" if self.encoding == 1 else b"\0")
        data_start = base_names + len(names)
        data_start = (data_start + 0xF) & ~0xF  # same 16-byte alignment as FromSoftware's files
        body = bytearray()
        offs = []
        for tex in self.textures:
            if len(body) % 0x10:
                body += bytes(0x10 - len(body) % 0x10)
            offs.append(data_start + len(body))
            body += tex.data
        out = bytearray(b"TPF\0" + struct.pack("<ii", sum(len(t.data) for t in self.textures), n))
        out += bytes([self.platform, self.flag2, self.encoding, 0])
        for tex, off, no in zip(self.textures, offs, name_offs):
            out += struct.pack("<IiBBBB", off, len(tex.data), tex.fmt, tex.typ, tex.mips, tex.flags1)
            out += struct.pack("<hhii", tex.w, tex.h, tex.tex_count, tex.unk2)
            out += struct.pack("<Iii", no, 0, tex.dxgi)
        out += names
        out += bytes(data_start - len(out))
        out += body
        return bytes(out)
