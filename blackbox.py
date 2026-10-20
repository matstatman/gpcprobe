#!/usr/bin/env python3
"""blackbox.py — human-readable dump of the inforom journal & settings
objects: BBX (board boot/POH journal), DEM (persistent event log), OMS
(operational-mode settings).

Sources:
  live         every visible card via /dev/gpcprobe (--bdf narrows to one)
  <file.rom>   a flash capture (1 MiB aperture or 0xFF000 nvflash pull)

By default a command runs on EVERY card behind /dev/gpcprobe (or the given
capture); --bdf narrows live mode to a single card.  --full additionally
shows all-zero BBX blocks and raw hex of every decoded structure.

Examples:
  blackbox.py live                     # all 3 objects, all cards
  blackbox.py live --bdf 06 --only dem # one card, event log only
  blackbox.py 170hx_05..._nvflash.rom --only bbx
  blackbox.py cap09.rom --only oms --full

Read-only by construction (zero write ioctls exist here; the reader core
issues only GP_IOC_INFO + GP_IOC_READ32 against /dev/gpcprobe).
"""
import argparse
import hashlib
import os
import re
import struct
import sys
import time
import zlib

FLASH_TOP = 0x100000
SLOT_LO   = 0xC1080
SLOT_END  = 0xF2000
SLOT_SZ   = 0x80
N_SLOTS   = (SLOT_END - SLOT_LO) // SLOT_SZ      # 1567
BI_MAGIC  = b"\x00" * 8 + b"BI"
BI_SEARCH = (0xF0000, 0xFC000)
BI_PREF   = 0xF2000

GP_IOC_MAGIC  = 0x47                             # /dev/gpcprobe ABI
GP_IOC_INFO   = (0 << 30) | (0 << 16) | (GP_IOC_MAGIC << 8) | 1
GP_IOC_READ32 = (3 << 30) | (12 << 16) | (GP_IOC_MAGIC << 8) | 2
AP_BASE = 0x300000          # NV_PROM aperture = BAR0 + 0x300000


def sha12(b):
    return hashlib.sha1(b).hexdigest()[:12]


def ascii_runs(b, n=4):
    return [s.decode("latin1") for s in
            re.findall(rb"[\x20-\x7e]{4,}", b) if len(s) >= n]


def fsz(x):
    return "0x{:X} ({} B)".format(x, x) if x is not None else "-"


def parse_bdf(s):
    if s is None:
        return None
    s = s.strip().lower()
    for pre in ("00000000:", "0000:"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    if ":" in s:
        bus, rest = s.split(":", 1)
        dev, fn = (rest.split(".") + ["0"])[:2]
        return (int(bus, 16) << 8) | (int(dev, 16) << 3) | int(fn, 16)
    v = int(s, 16)
    return v if v > 0xFF else v << 8


class FileSrc(object):
    kind = "file"

    def __init__(self, path, base=0):
        with open(path, "rb") as f:
            self.data = f.read()
        self.base = base
        self.limit = base + len(self.data)
        self.label = "file {} ({:,d} B)".format(path, len(self.data))
        if len(self.data) not in (0x100000, 0xFF000) and base == 0:
            raise SystemExit(
                "blackbox.py: {}: length {:#x} is neither an aperture "
                "(0x100000) nor nvflash (0xFF000) capture - say where it "
                "starts with --base".format(path, len(self.data)))

    def read(self, off, n):
        if off < self.base or off + n > self.limit:
            raise IndexError("read {:#x}+{:#x} outside capture [{:#x},{:#x})"
                             .format(off, n, self.base, self.limit))
        o = off - self.base
        return self.data[o:o + n]


class LiveSrc(object):
    kind = "live"

    def __init__(self, bdf=None):
        try:
            import fcntl
        except ImportError:
            raise SystemExit("blackbox.py: live mode needs Linux + "
                             "/dev/gpcprobe (build: gpcprobe.c); on a "
                             "workstation use a .rom capture")
        self.fcntl = fcntl
        if not os.path.exists("/dev/gpcprobe"):
            raise SystemExit("blackbox.py: /dev/gpcprobe missing ")
        self.fd = os.open("/dev/gpcprobe", os.O_RDWR)
        info = bytearray(4 + 16 * 4)
        fcntl.ioctl(self.fd, GP_IOC_INFO, info)
        count, *bdfs = struct.unpack("<17I", info)
        self.bdf_set = [b for b in bdfs[:count] if b]
        if bdf is None:
            self.idx, self.bdf = None, None
            self.label = "live /dev/gpcprobe ({} cards: {})".format(
                len(self.bdf_set),
                ",".join("{:04x}".format(b) for b in self.bdf_set))
        else:
            if bdf not in self.bdf_set:
                raise SystemExit(
                    "blackbox.py: bdf {:04x} not in visible set {}".format(
                        bdf, ",".join("{:04x}".format(b)
                                      for b in self.bdf_set)))
            self.idx = self.bdf_set.index(bdf)
            self.bdf = bdf
            self.label = "live /dev/gpcprobe idx {} bdf {:04x}".format(
                self.idx, bdf)
        self.limit = FLASH_TOP

    def r32(self, off):
        if self.idx is None:
            raise IndexError("LiveSrc probe handle has no card selected")
        buf = bytearray(struct.pack("<III", self.idx, AP_BASE + off, 0))
        self.fcntl.ioctl(self.fd, GP_IOC_READ32, buf)
        return struct.unpack_from("<I", buf, 8)[0]

    def read(self, off, n):
        if off < 0 or off + n > FLASH_TOP:
            raise IndexError("read {:#x}+{:#x} outside aperture".format(
                off, n))
        a, end = off & ~3, (off + n + 3) & ~3
        out = bytearray()
        for x in range(a, end, 4):
            out += struct.pack("<I", self.r32(x))
        return bytes(out[off - a:off - a + n])


class Rom(object):
    """Decodes one inforom image from a FileSrc or LiveSrc (read-only)."""

    def __init__(self, src):
        self.src = src
        self._nodes = None
        self._bi = None

    def raw(self, off, n):
        return self.src.read(off, n)

    def note(self, s):
        print(s, file=sys.stderr)

    # ---------- directory copy B (BI zlib container) ----------
    def bi(self):
        if self._bi is not None:
            return self._bi
        d = dict(off=None, alloc=0, container=None, trailer=b"", err=None)
        off = hdr = None
        try:
            try:
                h = self.raw(BI_PREF, 16)
                if h[:10] == BI_MAGIC:
                    off, hdr = BI_PREF, h
            except IndexError:
                pass
            if off is None:
                if self.src.kind == "live":
                    self.note("(searching for the BI blob, slow path...)")
                lo, hi = BI_SEARCH
                w = lo
                while w < min(hi, self.src.limit):
                    try:
                        chunk = self.raw(w, min(0x408, self.src.limit - w))
                    except IndexError:
                        break
                    j = chunk.find(BI_MAGIC)
                    if j >= 0:
                        off, hdr = w + j, chunk[j:j + 16]
                        break
                    w += 0x400
            if off is None:
                d["err"] = "BI blob (8x00+'BI') not found"
            elif not (0x800 <= struct.unpack_from("<I", hdr, 10)[0] <= 0x8000):
                d["off"] = off
                d["err"] = "alloc field @+10 = {:#x} outside 0x800..0x8000" \
                           .format(struct.unpack_from("<I", hdr, 10)[0])
            else:
                d["off"] = off
                d["alloc"] = struct.unpack_from("<I", hdr, 10)[0]  # slot
                #                        window: 8x00+'BI' + u32 alloc @+10
                avail = max(0, self.src.limit - (off + 16))
                stream = self.raw(off + 16, min(d["alloc"] + 0x400, avail))
                if self.src.kind == "live":
                    self.note("(reading BI slot: {:,d} B through the "
                              "aperture...)".format(len(stream)))
                z = zlib.decompressobj()
                d["container"] = z.decompress(stream) + z.flush()
                d["stream_len"] = len(stream) - len(z.unused_data)
                # static factory 16-B trailer at the alloc-slot end:
                # 00x8 + 'LE' + u32 0x0000B020 + 00 01 (static across all
                # cards)
                try:
                    d["trailer"] = self.raw(off + 16 + d["alloc"] - 16, 16)
                except IndexError:
                    d["trailer"] = b""
        except IndexError as e:
            d["err"] = "beyond capture: {}".format(e)
        except zlib.error as e:
            d["err"] = "zlib: {}".format(e)
        self._bi = d
        return d

    def bi_dir(self):
        d = self.bi()
        c = d.get("container")
        if c is None or len(c) < 112:
            return None
        ents = []
        for i in range(15):
            o = 14 + 5 * i
            ents.append((i, c[o:o + 3].decode("latin1"),
                         struct.unpack_from("<H", c, o + 3)[0]))
        f16 = c[89:94]
        if any(f16) and 32 <= f16[0] < 127:
            ents.append((15, f16[:3].decode("latin1"),
                         struct.unpack_from("<H", f16, 3)[0]))
        return dict(hdr=c[:14], entries=ents, free16=c[89:94],
                    tail=c[94:112], container=c)

    def bi_record(self, name):
        """Full OBM record bytes for an object from the BI container copy."""
        bd = self.bi_dir()
        if not bd:
            return None
        for _i, n, o in bd["entries"]:
            if n == name:
                c = bd["container"]
                if o + 8 > len(c):
                    return None
                size = c[o + 5] | (c[o + 6] << 8)
                if size < 8 or o + size > len(c):
                    return None
                return c[o:o + size]
        return None

    # ---------- JFFS slot table ----------
    def nodes(self):
        if self._nodes is not None:
            return self._nodes
        if self.src.kind == "live":
            self.note("(scanning {} slot headers...)".format(N_SLOTS))
        nodes, free0, freeF = [], 0, 0
        for i in range(N_SLOTS):
            base = SLOT_LO + i * SLOT_SZ
            try:
                b = self.raw(base, 12)
            except IndexError:
                break
            d0, d1, d2 = struct.unpack("<III", b)
            if d0 == 0:
                free0 += 1
                continue
            if d0 == 0xFFFFFFFF and d1 == 0xFFFFFFFF:
                freeF += 1
                continue
            nodes.append(dict(i=i, off=SLOT_LO + i * SLOT_SZ, type=d0 & 0xFF,
                              name=struct.pack("<I", d0)[1:4].decode("latin1"),
                              u16=d1 & 0xFFFF, hdrsize=(d1 >> 16) & 0xFFFF,
                              state=d2 & 0xFF))
        self._nodes = dict(nodes=nodes, free0=free0, freeF=freeF)
        return self._nodes

    def jffs_one(self, node):
        """First-slot OBM capture for one node (<=112 B).  Wide objects are
        physically 'named header slot + following raw fragment slots' - a
        node whose content carries no OBM self-name is flagged as a
        fragment (named=False) and captured as b"". (run-selection of GC
        runs is not yet fully modeled)"""
        c = self.raw(node["off"] + 0x10, 0x70)
        name_ok = c[:3].decode("latin1") == node["name"]
        size = c[5] | (c[6] << 8)
        named = name_ok and 8 <= size <= 0x10000
        return dict(node=node, named=named, hdr=c[:8],
                    size=size if named else None,
                    bytes=c[:min(size, 0x70)] if named else b"",
                    wide=node["hdrsize"] > SLOT_SZ)


BBX_SZ, DEM_SZ = 0x5940, 0x1030          # OBM record sizes (OMS = 0x70)
CH = 0x70                                    # JFFS chunk size
TLO = int(time.mktime((2021, 1, 1, 0, 0, 0, 0, 0, 0)))
THI = int(time.mktime((2029, 1, 1, 0, 0, 0, 0, 0, 0)))
DEM_FIFO_OFF, DEM_FIFO_SZ = 0x10, 0x1000     
DEM_FLAG_OVFL, DEM_FLAG_TRUNC = 0x01, 0x02   


def ts(v):
    """unix-seconds -> 'YYYY-mm-dd HH:MM:SS' if plausibly a timestamp."""
    if TLO <= v <= THI:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(v))
    return None


def dur(v):
    """seconds -> 'Nd Nh' style duration."""
    return "{}d {:02d}h {:02d}m".format(v // 86400, v % 86400 // 3600,
                                        v % 3600 // 60)


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def rows(b, base=0, cap=None):
    """hexdump rows with per-16B inline ascii (cap = max rows)."""
    out = []
    n = len(b) if cap is None else min(len(b), cap * 16)
    for i in range(0, n, 16):
        row = b[i:i + 16]
        out.append((base + i, row, "".join(
            chr(c) if 32 <= c < 127 else "." for c in row)))
    return out


def hexdump_ann(b, base=0, cap=None):
    """hexdump; tag every u32 in the unix-ts window (heuristic only)."""
    out = []
    for off, row, ascii_ in rows(b, base, cap):
        line = "    {:06x}  {:<47s} |{}|".format(
            off, " ".join("{:02x}".format(c) for c in row), ascii_)
        ann = []
        for k in range(0, len(row) - 3, 4):
            t = ts(struct.unpack_from("<I", row, k)[0])
            if t:
                ann.append("+0x{:x} {}".format(k, t))
        if ann:
            line += "   ts?: " + ", ".join(ann)
        out.append(line)
    return out


def nz_x(b, skips=(0x00, 0xFF)):
    return sum(1 for x in b if x not in skips)


# --------------------------------------------------------------------------
# JFFS live-record assembly (newest-chunk-wins; objscan/bbx_events model)
# --------------------------------------------------------------------------

def _newer(a, b):
    return 0 < (a - b) & 0xFF < 0x80


def assemble_live(rom, name, rec_size):
    """Offset-tagged JFFS reassembly: header slots cover record offset 0,
    fragment slots carry their record byte-offset in node d1.u16.  On
    collisions the chunk with the newer state byte (mod-256 serial) wins.
    Holes render as 0xFF.  Returns (bytes, covered, n_chunks_seen)."""
    best = {}
    seen = 0
    for n in rom.nodes()["nodes"]:
        if n["name"] != name:
            continue
        v = rom.jffs_one(n)
        o = 0 if v["named"] else n["u16"]
        if o + CH > rec_size:
            continue
        seen += 1
        cur = best.get(o)
        if cur is None or (not v["named"] and _newer(n["state"], cur[0])) or \
                (v["named"] and cur[1] and not _newer(cur[0], n["state"])):
            best[o] = (n["state"], rom.raw(n["off"] + 0x10, CH), v["named"])
    rec = bytearray(b"\xff" * rec_size)
    for o, (st, c, named) in best.items():
        rec[o:o + CH] = c
    return bytes(rec), min(len(best) * CH, rec_size), seen


def generations(rom, name):
    """Every JFFS slot of an object that carries a real OBM self-name, with
    its BI-agreement grade.  (Retired GC generations included on purpose:
    the live/dead discriminator is [OPEN], see inforom-objects.md §7.1.)"""
    bi = rom.bi_record(name)
    bi_pre = bi[:min(len(bi), CH)] if bi else None
    out = []
    for n in rom.nodes()["nodes"]:
        if n["name"] != name:
            continue
        v = rom.jffs_one(n)
        if not v["named"]:
            continue
        grade = "?"
        if bi_pre is not None:
            m = min(len(v["bytes"]), len(bi_pre))
            grade = "=BI" if v["bytes"][:m] == bi_pre[:m] else "!=BI"
        out.append(dict(node=n, size=v["size"], grade=grade))
    return out


def obm_line(tag, rec):
    if rec is None:
        return "{}: absent".format(tag)
    return ("{}: ver {} sub {:#x} size {} ck {:#04x} body {:,d} B nz {} "
            "sha {}".format(tag, rec[3], rec[4], fsz(rec[5] | (rec[6] << 8)),
                            rec[7], len(rec) - 8, nz_x(rec[8:]),
                            sha12(rec[8:])))


def blank_note(b):
    all0 = not any(b)
    allF = all(x == 0xFF for x in b)
    if all0:
        return "  [all-zero -> factory-blank]"
    if allF:
        return "  [all-FF -> never written (erase state)]"
    return ""


# --------------------------------------------------------------------------
# BBX
# --------------------------------------------------------------------------

def dump_bbx(rom, full=False):
    print("=" * 72)
    print("BBX — board boot / power-on-hours journal   (churns every boot)")
    print("=" * 72)
    bi = rom.bi_record("BBX")
    if bi:
        print("BI copy  : " + obm_line("factory", bi) + blank_note(bi[8:]))
    gens = generations(rom, "BBX")
    tcount = dict()
    for n in rom.nodes()["nodes"]:
        if n["name"] == "BBX":
            tcount[n["type"]] = tcount.get(n["type"], 0) + 1
    print("JFFS     : {} node(s) [types {}] = {} generation header slot(s) + "
          "fragments".format(sum(1 for n in rom.nodes()["nodes"]
                                 if n["name"] == "BBX"),
                             " ".join("{:#x}:{}".format(k, v)
                                      for k, v in sorted(tcount.items())),
                             len(gens)))
    for g in gens:
        n = g["node"]
        print("  gen: slot {:4d} @0x{:X} type {:#04x} state {:#02x} "
              "hdr-size {:#x} rec {:#x} {}".format(
                  n["i"], n["off"], n["type"], n["state"], n["hdrsize"],
                  g["size"], g["grade"]))

    rec, cov, chunks = assemble_live(rom, "BBX", BBX_SZ)
    holes = BBX_SZ - cov
    print("assembled: {:,d} of {:,d} B covered ({} chunks, newest-chunk-"
          "wins{}) sha {}".format(cov, BBX_SZ, chunks,
                                  ", {:,d} B holes -> FF".format(holes) if holes
                                  else ", no holes", sha12(rec)))

    print("\n-- header block [0x00:0x40]  (unix ts / uptime fields [MEAS;inf]) "
          "--")
    for o, label in ((0x08, "install ts      [inf]"),
                     (0x0c, "last-boot ts    [inf]"),
                     (0x10, "uptime s        [inf POH-like]"),
                     (0x14, "ts #2           [inf]"),
                     (0x18, "ts #3           [inf]"),
                     (0x1c, "uptime #2       [inf]"),
                     (0x20, "opaque          [OPEN]"),
                     (0x24, "opaque          [OPEN]"),
                     (0x28, "counter         [inf]"),
                     (0x2c, "ts #4           [inf]"),
                     (0x30, "uptime-like     [OPEN]"),
                     (0x34, "opaque pair lo  [OPEN]"),
                     (0x38, "opaque pair hi  [OPEN]")):
        v = u32(rec, o)
        t = ts(v)
        extra = "   = {} UTC".format(t) if t else (
            "   = {}{}".format(dur(v), " [uptime-like]")
            if 3600 <= v < THI - TLO and o in (0x10, 0x1c, 0x30) else "")
        print("  +0x{:02x} u32 {:10d} 0x{:08x}  {:<24s}{}".format(
            o, v, v, label, extra))

    print("\n-- boot-count block [0x80:0xa0]  ([MEAS] boot-count @+0x84) --")
    for o in range(0x80, 0xa8, 4):
        v = u32(rec, o)
        t = ts(v)
        if o == 0x84:
            print("  +0x84 boot count    {:10d}        [inf]".format(v))
        elif t:
            print("  +0x{:02x} ts            {} UTC".format(o, t))
        elif v:
            print("  +0x{:02x} u32           {:10d} 0x{:08x}  [OPEN]".format(
                o, v, v))

    print("\n-- power-cap history [0xd4:0x134]  {value W*1000, ts} pairs "
          "[inf]; stride ~12 B [OPEN] --")
    caps = 0
    for o in range(0xd4, 0x134 - 4):
        v, nxt = u32(rec, o), u32(rec, o + 4)
        t = ts(nxt)
        if v and v % 1000 == 0 and 10000 <= v <= 1000000 and t:
            print("  +0x{:03x}  {:>7d} W = {:>6.1f} W  set {} UTC".format(
                o, v, v / 1000.0, t))
            caps += 1
    if not caps:
        print("  (no {W*1000, ts} pairs found in the window)")

    print("\n-- 128-B block grid: 178 blocks [0x000:0x5900) + 64 B tail --")
    print("   region tags follow object-content.md §BBX (schemas [OPEN];")
    print("   u32s in the 2021..2029 unix window are tagged inline as ts?)")
    pop = 0
    for i in range(178):
        off = i * 0x80
        blk = rec[off:off + 0x80]
        p = nz_x(blk)
        if off >= 0x580:
            tag = "boot-journal ring"
        elif off >= 0x160:
            tag = "opaque table"
        elif off >= 0x80:
            tag = "header fields ^"
        else:
            tag = "header block"
        if not p:
            if full:
                print("  blk {:3d} @0x{:04x}  empty   ({})".format(i, off, tag))
            continue
        pop += 1
        print("  blk {:3d} @0x{:04x}  populated {:3d} B  ({})".format(
            i, off, p, tag))
        if i < 3 and not full:
            print("        (header-field block — decoded above)")
            continue
        print("\n".join(hexdump_ann(blk, off)))
    tail = rec[0x5900:]
    if nz_x(tail):
        print("  tail  @0x5900  populated {} B:".format(nz_x(tail)))
        print("\n".join(hexdump_ann(tail, 0x5900)))
    print("\nsummary: {} of 178 blocks populated; {} cap entries; "
          "ring inner schema is [OPEN] — ~20/178 blocks rewrite per 2 days "
          "on a live card".format(pop, caps))


# --------------------------------------------------------------------------
# DEM
# --------------------------------------------------------------------------

def _dem_text(rec, off, end):
    s = rec[off:end].split(b"\x00")[0]
    return s.decode("latin1", "replace")


def dem_walk(rec):
    """Follow the record chain through the FIFO by size-in-words, validated
    on seq continuity.  Returns (records, stop_reason, bytes_consumed)."""
    recs, off, prev_seq, consumed = [], DEM_FIFO_OFF, None, 0
    lo, hi = DEM_FIFO_OFF, DEM_FIFO_OFF + DEM_FIFO_SZ
    for _ in range(4096):                      # hard cap
        if off + 12 > hi:
            return recs, "reached end of FIFO", consumed
        rs, xid, flags, ext = rec[off], rec[off + 1], rec[off + 2], rec[off + 3]
        seq, t = struct.unpack_from("<II", rec, off + 4)
        if rs == 0:
            return recs, "hole (recordSize 0) @fifo+0x{:x}".format(
                off - lo), consumed
        if rs > DEM_FIFO_SZ // 4 or off + rs * 4 > hi:
            return recs, "implausible/overrun recordSize {:#x} " \
                         "@fifo+0x{:x}".format(rs, off - lo), consumed
        if prev_seq is not None and seq != prev_seq + 1:
            return recs, "seq break ({} -> {}) @fifo+0x{:x} — stop (stale or " \
                         "unframed bytes)".format(prev_seq, seq,
                                                  off - lo), consumed
        text = _dem_text(rec, off + 12, min(off + rs * 4, hi))
        recs.append(dict(off=off - lo, size=rs * 4, xid=xid, flags=flags,
                         ext=ext, seq=seq, ts=t, text=text))
        prev_seq = seq
        consumed += rs * 4
        off += rs * 4
        if off >= hi:                          # circular FIFO wrap
            off = lo
        if recs and off == recs[0]["off"] + lo:
            return recs, "wrapped to first record", consumed
    return recs, "record cap", consumed


def dump_dem(rom, full=False):
    print("=" * 72)
    print("DEM — persistent event log (Xid-style reliability history)")
    print("=" * 72)
    bi = rom.bi_record("DEM")
    if bi:
        print("BI copy  : " + obm_line("factory", bi) + blank_note(bi[8:]))
    gens = generations(rom, "DEM")
    tot = sum(1 for n in rom.nodes()["nodes"] if n["name"] == "DEM")
    print("JFFS     : {} node(s), {} generation header slot(s) [GC "
          "retention pool]".format(tot, len(gens)))
    for g in gens:
        n = g["node"]
        print("  gen: slot {:4d} @0x{:X} type {:#04x} state {:#02x} "
              "hdr-size {:#x} {}".format(n["i"], n["off"], n["type"],
                                         n["state"], n["hdrsize"],
                                         g["grade"]))

    rec, cov, chunks = assemble_live(rom, "DEM", DEM_SZ)
    print("assembled: {} ({} chunks){} sha {}".format(
        fsz(cov), chunks,
        "" if cov >= DEM_SZ else " — {} B holes".format(DEM_SZ - cov),
        sha12(rec)))

    seq0, wo, ro = struct.unpack_from("<IHH", rec, 8)
    print("\n-- FIFO framing [PUB ifrdem.h] --")
    print("  seqNumber @0x08  : {:<10d} (unused by this FW — per-record seq "
          "is authoritative)".format(seq0))
    print("  writeOffset @0xc : {:<10d} readOffset @0xe : {}   (0 = walk "
          "whole FIFO from +0x10)".format(wo, ro))
    print("  record framing [PUB NV_MSGBOX_DEM_RECORD]: {{u8 size-in-words, "
          "u8 xid, u8 flags, u8 xidExt,")
    print("      u32 seq, u32 unix-ts, NUL text}}  flags: 01=OVFL 02=TRUNC")

    recs, why, used = dem_walk(rec)
    print("\n-- event records ({} found; walk stopped: {}) --".format(
        len(recs), why))
    if not recs:
        print("  (no framed records — object blank/unwritten)")
    for i, r in enumerate(recs):
        fl = "".join(x for x in (
            "OVFL," if r["flags"] & DEM_FLAG_OVFL else "",
            "TRUNC," if r["flags"] & DEM_FLAG_TRUNC else "")).rstrip(",") or "-"
        print("  {:>3d} seq {:<6d} {} UTC  xid {:#04x} ({:>3d})  fl {:<5s} "
              "{:>3d} B @fifo+0x{:03x}".format(i, r["seq"], ts(r["ts"])
                                               or "{!r}".format(r["ts"]),
                                               r["xid"], r["xid"], fl,
                                               r["size"], r["off"]))
        print("       {}".format(r["text"]))
        if r["flags"] & DEM_FLAG_TRUNC:
            nxt = rec[r["off"] + DEM_FIFO_OFF + r["size"]:
                      r["off"] + DEM_FIFO_OFF + r["size"] + 96]
            cont = nxt.split(b"\x00")[0].decode("latin1", "replace") \
                if nxt and 32 <= nxt[0] < 127 else ""
            if cont.strip():
                print("       +overflow-past-record: {}".format(cont))
    tail = rec[DEM_FIFO_OFF + used:DEM_FIFO_OFF + DEM_FIFO_SZ] \
        if used < DEM_FIFO_SZ else b""
    runs = ascii_runs(tail, 6)
    if runs:
        print("\n-- unframed residual FIFO bytes: {} printable runs past the "
              "last record --".format(len(runs)))
        for s in runs:
            print("  {!r}".format(s))
    if recs:
        print("\nsummary: {} records, fifo used {:,d}/4096 B, span {} .. {}"
              .format(len(recs), used, ts(recs[0]["ts"]) or "?",
                      ts(recs[-1]["ts"]) or "?"))
    rsv = rec[DEM_SZ - 32:DEM_SZ] if full else b""
    if full:
        print("reserved tail (8×u32 before object end): {}".format(
            " ".join(str(u32(rsv, o)) for o in range(0, 32, 4))))


# --------------------------------------------------------------------------
# OMS
# --------------------------------------------------------------------------

def oms_entry_ck_ok(data):
    return (data + (data >> 8) + (data >> 16) + (data >> 24)) & 0xFF == 0


def decode_oms(tag, rec, full=False):
    print("\n  ---- {} ----".format(tag))
    print("  " + obm_line("OBM", rec))
    body = rec[8:]
    cnt = u32(body, 0)
    print("  +0x08 u32 = {:<12d} lifetimeRefreshCount [PUB V1G] / "
          "'count' [research fmt view]".format(cnt))
    unset = all(x == 0xFF for x in body[4:0x64]) and cnt == 0
    any_entry = False
    for i in range(8):                          # [PUB V1G] 8 entries ×12 B
        e = body[0x4 + 12 * i:0x10 + 12 * i]
        data, lmin, lmax = struct.unpack_from("<III", e, 0)
        off = 0x0c + 12 * i
        if e == b"\xff" * 12:
            print("  entry {} @+0x{:02x}: FF FF FF ...  unset (never written)"
                  .format(i, off))
            continue
        any_entry = True
        flags = []
        if data & 1:
            flags.append("entry-available")
        if data & 2:
            flags.append("force-device-disable")
        print("  entry {} @+0x{:02x}: data {:#010x} ({}) clockLimitMin {:<12d}"
              " clockLimitMax {:<12d} ck {}".format(
                  i, off, data, ",".join(flags) or "no flags", lmin, lmax,
                  "OK" if oms_entry_ck_ok(data) else "BAD"))
        if full:
            print("         raw: {}".format(e.hex(" ")))
    rsv = u32(body, 0x64)
    print("  +0x6c reserved u32 = {:#x}".format(rsv))
    if unset and not any_entry:
        print("  verdict: blank ring (count 0 + FF sentinel entries) — "
              "no settings provisioned")


def dump_oms(rom, full=False):
    print("=" * 72)
    print("OMS — operational-mode settings  (ver 1 sub 0x10, 112 B)")
    print("=" * 72)
    bi = rom.bi_record("OMS")
    if bi:
        decode_oms("BI container copy (factory)", bi, full)
    else:
        print("BI copy  : absent")
    gens = generations(rom, "OMS")
    if not gens:
        print("\nJFFS copy: no named OMS node — object absent in JFFS")
        return
    print("\nJFFS     : {} named generation slot(s) — every one decoded "
          "(live/dead discriminator is [OPEN])".format(len(gens)))
    for g in gens:
        n = g["node"]
        rec = rom.raw(n["off"] + 0x10, min(g["size"], 0x70))
        decode_oms("JFFS slot {} @0x{:X} type {:#x} state {:#02x} {}"
                   .format(n["i"], n["off"], n["type"], n["state"],
                           g["grade"]), rec, full)
        if any(b not in (0x00, 0xFF) for b in rec[0x1c:]):
            print("  populated body — raw:")
            print("\n".join(hexdump_ann(rec, 0)))


# --------------------------------------------------------------------------

CMDS = dict(bbx=dump_bbx, dem=dump_dem, oms=dump_oms)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="blackbox.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="human-readable dump of BBX / DEM / OMS (read-only)",
        epilog=__doc__)
    ap.add_argument("src", help="'live' (via /dev/gpcprobe) or a .rom capture")
    ap.add_argument("--bdf", help="narrow live mode to one card: "
                                  "06 | 0600 | 0000:06:00.0")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0,
                    help="partial capture: flash offset where the file starts")
    ap.add_argument("--full", action="store_true",
                    help="also show empty BBX blocks and raw hex")
    ap.add_argument("--only", choices=["bbx", "dem", "oms"], default=None,
                    help="dump a single §4.4 object instead of all three")
    a = ap.parse_args(argv)

    if a.src == "live":
        bdf0 = parse_bdf(a.bdf)
        probe = LiveSrc(bdf0)
        bdfs = [bdf0] if bdf0 is not None else sorted(probe.bdf_set)
        os.close(probe.fd)
        srcs = [LiveSrc(b) for b in bdfs]
    else:
        srcs = [FileSrc(a.src, base=a.base)]

    todo = [a.only] if a.only else ["bbx", "dem", "oms"]
    for k, src in enumerate(srcs):
        if len(srcs) > 1:
            print("===== {} =====\n".format(src.label))
        rom = Rom(src)
        for name in todo:
            CMDS[name](rom, full=a.full)
            if name != todo[-1]:
                print()
        if k + 1 < len(srcs):
            print()


if __name__ == "__main__":
    main()
