#!/usr/bin/env python3
"""Read-only GA100 floorsweep fuse probe: GPC (compute) + FBP (memory).

Uses the gpcprobe kernel module (insmod gpcprobe.ko), which ioremaps each
NVIDIA GPU's BAR0 read-only because the proprietary nvidia driver owns the
BARs and blocks the usual sysfs resource0 / /dev/mem paths.

Prints a per-card detail block, then a summary table (card/model/memory via
nvidia-smi, fuse results from BAR0).

Register map (GA100 BAR0):
  GPC  OPT_GPC_DISABLE    0x00820350  one bit per GPC
  GPC  OPT_GPC_DEFECTIVE  0x008205c4  one bit per GPC
  FBP  OPT_FBP_DISABLE    0x00820364  one bit per FBP   (12 FBPs)
  FBP  OPT_FBP_DEFECTIVE  0x008205CC  one bit per FBP (physically dead)
  FBP  OPT_FBPA_DEFECTIVE 0x008205D0  one bit per FBPA (physically dead)
  FBP  OPT_FBIO_DEFECTIVE 0x008205D4  one bit per FBIO (physically dead)

  ROP  OPT_ROP_L2_DISABLE  0x008202C4  one bit per L2 slice (fused OFF;
                                       mirrors OPT_FBPA_DISABLE)
  ROP  OPT_ROP_L2_DEFECTIVE 0x008205E8  one bit per L2 slice (physically dead)
  FBP  OPT_FBPA_DISABLE   0x00820368  one bit per FBPA  (24)
  FBP  OPT_FBIO_DISABLE   0x0082036C  one bit per FBIO  (24)
  read-only status shadows:
        STATUS_OPT_GPC 0x00820C1C   STATUS_FBP 0x00820D38
        STATUS_FBPA    0x00820C18   STATUS_OPT_FBIO 0x00820C14
  Offsets per JRex286's Ampere fuse probe (gist 0480d2b2) and the
  Consensus-Protocol/cmp170hx register docs; validated on this
  hardware: every status shadow equals its fuse's value.
"""
import fcntl
import os
import shutil
import struct
import subprocess
import sys

GP_IOC_MAGIC = 0x47  # 'G'
GP_IOC_INFO = (0x00 << 30) | (0 << 16) | (GP_IOC_MAGIC << 8) | 1   # _IO('G', 1)
GP_IOC_READ32 = (3 << 30) | (12 << 16) | (GP_IOC_MAGIC << 8) | 2   # _IOWR('G', 2, 12B)

# (label, fuse offset, bit width, status shadow offset or None)
GROUPS = [
    ("GPC  (compute clusters)", [
        ("OPT_GPC_DISABLE",   0x00820350, 8,  0x00820C1C),
        ("OPT_GPC_DEFECTIVE", 0x008205C4, 8,  None),
    ]),
    ("FBP  (memory partitions)", [
        ("OPT_FBP_DISABLE",   0x00820364, 12, 0x00820D38),
        ("OPT_FBP_DEFECTIVE", 0x008205CC, 12, None),
        ("OPT_FBPA_DISABLE",  0x00820368, 24, 0x00820C18),
        ("OPT_FBPA_DEFECTIVE", 0x008205D0, 24, None),
        ("OPT_FBIO_DISABLE",  0x0082036C, 24, 0x00820C14),
        ("OPT_FBIO_DEFECTIVE", 0x008205D4, 24, None),
    ]),
    ("ROP  (raster ops / L2 slices)", [
        ("OPT_ROP_L2_DISABLE", 0x008202C4, 24, None),
        ("OPT_ROP_L2_DEFECTIVE", 0x008205E8, 24, None),
    ]),
]
STATUS_NAMES = {
    0x00820C1C: "STATUS_OPT_GPC",
    0x00820D38: "STATUS_FBP",
    0x00820C18: "STATUS_FBPA",
    0x00820C14: "STATUS_OPT_FBIO",
}


def bits_set(mask, width):
    return [i for i in range(width) if mask & (1 << i)]


def fmt(bits):
    return ", ".join(str(b) for b in sorted(bits)) if bits else "none"


def gpu_meta():
    """bdf -> (model name, total memory MiB) via nvidia-smi; {} if unavailable."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "--query-gpu=pci.bus_id,name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    meta = {}
    for line in out.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                # nvidia-smi pads the domain to 8 hex digits; normalize to 4
                dom, rest = parts[0].lower().split(':', 1)
                meta[dom[-4:] + ':' + rest] = (parts[1], int(parts[2]))
            except (ValueError, AttributeError):
                pass
    return meta


def fmt_mem(mib):
    if mib is None:
        return "?"
    gib = mib / 1024
    return f"{gib:.0f} GiB" if gib == int(gib) else f"{gib:.1f} GiB"


def main():
    try:
        fd = os.open("/dev/gpcprobe", os.O_RDWR)
    except OSError as e:
        sys.exit(f"cannot open /dev/gpcprobe ({e}); run: insmod gpcprobe.ko")

    info = bytearray(4 + 16 * 4)
    fcntl.ioctl(fd, GP_IOC_INFO, info, True)
    count = struct.unpack_from("<I", info)[0]
    if not count:
        sys.exit("no NVIDIA GPUs seen by gpcprobe")

    def rd(idx, off):
        buf = bytearray(struct.pack("<III", idx, off, 0))
        fcntl.ioctl(fd, GP_IOC_READ32, buf)
        return struct.unpack_from("<I", buf, 8)[0]

    meta = gpu_meta()
    rows = []

    for i in range(count):
        bdf = struct.unpack_from("<I", info, 4 + 4 * i)[0]
        bus, devfn = (bdf >> 8) & 0xFF, bdf & 0xFF
        bdf_str = f"0000:{bus:02x}:{devfn >> 3:02x}.{devfn & 7}"
        name, mib = meta.get(bdf_str.lower(), ("?", None))

        vals = {}
        print(f"== Card {bus:02x}  ({bdf_str}) ==")
        for title, regs in GROUPS:
            print(f"  {title}")
            for label, off, width, status_off in regs:
                vals[label] = rd(i, off)
                bits = bits_set(vals[label], width)
                print(f"    {label:<18} 0x{vals[label]:08x}  "
                      f"{label.split('_')[1]}s {fmt(bits):<22} "
                      f"{width - len(bits)}/{width} active")
            delta = {"GPC": ("OPT_GPC_DISABLE", "OPT_GPC_DEFECTIVE", 8),
                     "FBP": ("OPT_FBP_DISABLE", "OPT_FBP_DEFECTIVE", 12),
                     "ROP": ("OPT_ROP_L2_DISABLE", "OPT_ROP_L2_DEFECTIVE", 24)}
            if title.split()[0] in delta:
                dis_l, def_l, w = delta[title.split()[0]]
                not_def = sorted(set(bits_set(vals[dis_l], w)) -
                                 set(bits_set(vals[def_l], w)))
                print(f"    {'disabled not defective':<18} {'':1s}  {fmt(not_def)}")
        print("  status shadows (should equal fuses)")
        for status_off, sname in STATUS_NAMES.items():
            val = rd(i, status_off)
            for title, regs in GROUPS:
                for label, off, width, so in regs:
                    if so == status_off:
                        fuse_val = vals[label]
                        flag = "ok" if val == fuse_val else f"!= fuse 0x{fuse_val:08x}!"
                        print(f"    {sname:<18} 0x{val:08x}  [{flag}]")
        print()

        gpc_d, gpc_f = vals["OPT_GPC_DISABLE"], vals["OPT_GPC_DEFECTIVE"]
        fb_d, fb_f = vals["OPT_FBP_DISABLE"], vals["OPT_FBP_DEFECTIVE"]
        rows.append([
            f"{bdf_str}  {name}  {fmt_mem(mib)}",
            str(8 - len(bits_set(gpc_d, 8))),
            str(len(bits_set(gpc_d, 8))),
            str(len(bits_set(gpc_f, 8))),
            str(12 - len(bits_set(fb_d, 12))),
            str(len(bits_set(fb_d, 12))),
            str(len(bits_set(fb_f, 12))),
        ])

    headers = ["Card", "GPC healthy", "GPC disabled", "GPC defective",
               "FBP healthy", "FBP disabled", "FBP defective"]
    table = [headers] + rows
    widths = [max(len(str(r[c])) for r in table) for c in range(len(headers))]
    print("Summary")
    for r in table:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())


main()
