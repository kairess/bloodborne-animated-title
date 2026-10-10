"""Minimal Scaleform GFX (SWF) tag reader/writer ??enough to swap the title background for a flipbook."""
import struct

TAG_END, TAG_SHOWFRAME, TAG_DEFINESHAPE, TAG_DEFINESHAPE2, TAG_DEFINESHAPE3 = 0, 1, 2, 22, 32
TAG_PLACEOBJECT2, TAG_PLACEOBJECT3, TAG_DEFINESPRITE = 26, 70, 39
TAG_EXTERNAL_IMAGE2 = 1009


class Tag:
    def __init__(self, code, body, children=None, sprite_hdr=None, long=None):
        self.code, self.body = code, bytes(body)
        self.children, self.sprite_hdr = children, sprite_hdr  # DefineSprite only
        self.long = long  # keep the original header form so unmodified files round-trip exactly

    def encode(self) -> bytes:
        body = self.body
        if self.code == TAG_DEFINESPRITE and self.children is not None:
            body = self.sprite_hdr + b"".join(t.encode() for t in self.children)
        long = self.long if self.long is not None else self.code in (TAG_DEFINESHAPE, TAG_DEFINESHAPE2, TAG_DEFINESHAPE3, TAG_EXTERNAL_IMAGE2)
        if len(body) < 0x3F and not long:
            return struct.pack("<H", (self.code << 6) | len(body)) + body
        return struct.pack("<HI", (self.code << 6) | 0x3F, len(body)) + body

    @property
    def char_id(self):
        return struct.unpack_from("<H", self.body, 0)[0]


def parse_tags(d: bytes, p: int, end: int):
    tags = []
    while p < end:
        h = struct.unpack_from("<H", d, p)[0]; p += 2
        code, ln = h >> 6, h & 0x3F
        long = ln == 0x3F
        if long:
            ln = struct.unpack_from("<I", d, p)[0]; p += 4
        body = d[p:p + ln]
        if code == TAG_DEFINESPRITE:
            tags.append(Tag(code, body, parse_tags(body, 4, len(body)), body[:4], long))
        else:
            tags.append(Tag(code, body, long=long))
        p += ln
        if code == TAG_END:
            break
    return tags


class Gfx:
    def __init__(self, data: bytes):
        assert data[:3] in (b"GFX", b"FWS"), "uncompressed GFX/SWF expected"
        self.magic, self.version = data[:3], data[3]
        nbits = data[8] >> 3
        rect_len = (5 + 4 * nbits + 7) // 8
        self.header_rest = data[8:8 + rect_len + 4]  # RECT + frame rate + frame count
        self.tags = parse_tags(data, 8 + rect_len + 4, len(data))

    def build(self) -> bytes:
        body = self.header_rest + b"".join(t.encode() for t in self.tags)
        return self.magic + bytes([self.version]) + struct.pack("<I", 8 + len(body)) + body

    # -- queries
    def external_images(self):
        out = {}
        for t in self.tags:
            if t.code == TAG_EXTERNAL_IMAGE2:
                cid = struct.unpack_from("<I", t.body, 0)[0]
                n = t.body[10]
                out[cid] = t.body[11:11 + n].decode()
        return out

    def max_char_id(self):
        ids = []
        def walk(tags):
            for t in tags:
                if t.code == TAG_EXTERNAL_IMAGE2:
                    ids.append(struct.unpack_from("<I", t.body, 0)[0])
                elif t.code in (2, 6, 20, 21, 22, 32, 35, 36, 83, 37, 39, 10, 11, 33, 46, 48, 75, 84, 87, 1005, 1008):
                    ids.append(t.char_id)
                if t.children:
                    walk(t.children)
        walk(self.tags)
        return max(ids)

    def find_shape_using_bitmap(self, bitmap_id):
        for i, t in enumerate(self.tags):
            if t.code in (TAG_DEFINESHAPE, TAG_DEFINESHAPE2, TAG_DEFINESHAPE3):
                info = shape_bitmap_fill(t.body)
                if info and info["bitmap_id"] == bitmap_id:
                    return i, info
        return None, None

    def find_place_of(self, char_id):
        """Return (sprite_tag, index) of the PlaceObject2 that places char_id (one level of nesting)."""
        for t in self.tags:
            if t.code == TAG_DEFINESPRITE:
                for j, c in enumerate(t.children):
                    if c.code == TAG_PLACEOBJECT2 and c.body[0] & 2 and struct.unpack_from("<H", c.body, 3)[0] == char_id:
                        return t, j
        return None, None

# ---------------------------------------------------------------- bit helpers

class BitReader:
    def __init__(self, d, p):
        self.d, self.p, self.bit = d, p, 0

    def ub(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.p] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit, self.p = 0, self.p + 1
        return v

    def sb(self, n):
        v = self.ub(n)
        return v - (1 << n) if n and v & (1 << (n - 1)) else v

    def align(self):
        if self.bit:
            self.bit, self.p = 0, self.p + 1


class BitWriter:
    def __init__(self):
        self.bits = []

    def ub(self, v, n):
        self.bits += [(v >> (n - 1 - i)) & 1 for i in range(n)]

    def sb(self, v, n):
        self.ub(v & ((1 << n) - 1), n)

    def bytes(self):
        b = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, b[i:i + 8])), 2) for i in range(0, len(b), 8))


def sbits(*vals):
    n = 1
    for v in vals:
        while not (-(1 << (n - 1)) <= v < (1 << (n - 1))):
            n += 1
    return n


def read_rect(d, p):
    br = BitReader(d, p); n = br.ub(5)
    r = [br.sb(n) for _ in range(4)]; br.align()
    return r, br.p


def read_matrix(d, p):
    br = BitReader(d, p)
    sx = sy = 65536; r0 = r1 = 0
    if br.ub(1):
        n = br.ub(5); sx, sy = br.sb(n), br.sb(n)
    if br.ub(1):
        n = br.ub(5); r0, r1 = br.sb(n), br.sb(n)
    n = br.ub(5); tx, ty = br.sb(n), br.sb(n)
    br.align()
    return (sx, sy, r0, r1, tx, ty), br.p


def write_matrix(sx, sy, tx=0, ty=0):
    """sx/sy in 16.16 fixed point, tx/ty in twips."""
    bw = BitWriter()
    bw.ub(1, 1); n = sbits(sx, sy); bw.ub(n, 5); bw.sb(sx, n); bw.sb(sy, n)
    bw.ub(0, 1)
    n = sbits(tx, ty) if (tx or ty) else 0
    bw.ub(n, 5)
    if n:
        bw.sb(tx, n); bw.sb(ty, n)
    return bw.bytes()


def write_cxform_alpha(alpha):
    """CXFORMWITHALPHA that only scales alpha (256 = opaque), as used by PlaceObject2."""
    bw = BitWriter()
    bw.ub(0, 1); bw.ub(1, 1)  # no add terms, mult terms
    n = sbits(256, alpha); bw.ub(n, 4)
    for v in (256, 256, 256, alpha):
        bw.sb(v, n)
    return bw.bytes()


def shape_bitmap_fill(body):
    """For a single-fill bitmap shape: locate the fill style and the bytes around it."""
    p = 2
    rect, p = read_rect(body, p)
    nfill = body[p]
    if nfill != 1:
        return None
    ftype = body[p + 1]
    if not 0x40 <= ftype <= 0x43:
        return None
    bid = struct.unpack_from("<H", body, p + 2)[0]
    mtx, end = read_matrix(body, p + 4)
    return {"bounds": rect, "fill_pos": p + 1, "fill_end": end, "bitmap_id": bid, "matrix": mtx, "type": ftype}


def clone_bitmap_shape(body, new_id, bitmap_id, sx, sy, fill_type, tx=0, ty=0):
    info = shape_bitmap_fill(body)
    head = struct.pack("<H", new_id) + body[2:info["fill_pos"]]
    fill = bytes([fill_type]) + struct.pack("<H", bitmap_id) + write_matrix(sx, sy, tx, ty)
    return head + fill + body[info["fill_end"]:]


def external_image2(cid, export, w, h, fmt=13):
    e, f = export.encode(), (export + ".tga").encode()
    return Tag(TAG_EXTERNAL_IMAGE2, struct.pack("<IHHH", cid, fmt, w, h) + bytes([len(e)]) + e + bytes([len(f)]) + f)


def place_new(depth, char_id):
    return Tag(TAG_PLACEOBJECT2, bytes([0x02]) + struct.pack("<HH", depth, char_id) + write_matrix(65536, 65536))


def place_replace(depth, char_id):
    return Tag(TAG_PLACEOBJECT2, bytes([0x03]) + struct.pack("<HH", depth, char_id))


def holds_for(n_frames, src_fps, timeline_fps=30):
    """Timeline frames per source frame so n frames at src_fps keep their real speed (24 -> 30 gives 1,1,1,2)."""
    total = round(n_frames * timeline_fps / src_fps)
    starts = [round(k * timeline_fps / src_fps) for k in range(n_frames)] + [total]
    return [b - a for a, b in zip(starts, starts[1:])]


def flipbook_sprite(sprite_id, shape_ids, hold=1):
    holds = hold if isinstance(hold, list) else [hold] * len(shape_ids)
    kids = []
    for k, (sid, h) in enumerate(zip(shape_ids, holds)):
        if h <= 0:
            continue
        kids.append(place_new(1, sid) if not kids else place_replace(1, sid))
        kids += [Tag(TAG_SHOWFRAME, b"") for _ in range(h)]
    kids.append(Tag(TAG_END, b""))
    return Tag(TAG_DEFINESPRITE, b"", kids, struct.pack("<HH", sprite_id, sum(holds)))
