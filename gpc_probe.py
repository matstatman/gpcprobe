#!/usr/bin/env python3
"""Read-only GA100 GPC floorsweep fuse probe (OPT_GPC_DISABLE / OPT_GPC_DEFECTIVE).

Uses the gpcprobe kernel module (insmod gpcprobe.ko), which ioremaps each
NVIDIA GPU's BAR0 read-only because the proprietary nvidia driver owns the
BARs and blocks the usual sysfs resource0 / /dev/mem paths.
"""
import fcntl
import os
import struct
import sys

REG_GPC_DISABLE = 0x00820350
REG_GPC_DEFECTIVE = 0x008205c4

GP_IOC_MAGIC = 0x47  # 'G'
GP_IOC_INFO = (0x00 << 30) | (0 << 16) | (GP_IOC_MAGIC << 8) | 1   # _IO('G', 1)
GP_IOC_READ32 = (3 << 30) | (12 << 16) | (GP_IOC_MAGIC << 8) | 2   # _IOWR('G', 2, 12B)


def bits_set(mask, width=8):
    return [i for i in range(width) if mask & (1 << i)]


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

    print(f"{'Card':<6} {'Disabled clusters':<20} {'Marked defective':<18} {'Disabled but not defective'}")
    for i in range(count):
        bdf = struct.unpack_from("<I", info, 4 + 4 * i)[0]
        slot = f"{(bdf >> 8) & 0xFF:02x}"
        try:
            disable = rd(i, REG_GPC_DISABLE)
            defective = rd(i, REG_GPC_DEFECTIVE)
        except OSError as e:
            print(f"{slot:<6} ERROR reading card {i}: {e}")
            continue

        disabled_bits = set(bits_set(disable))
        defective_bits = set(bits_set(defective))
        not_defective_bits = disabled_bits - defective_bits

        def fmt(bits):
            return ", ".join(str(b) for b in sorted(bits)) if bits else "none"

        print(f"{slot:<6} {fmt(disabled_bits):<20} {fmt(defective_bits):<18} {fmt(not_defective_bits)}")
        print(f"       (raw: OPT_GPC_DISABLE=0x{disable:08x} OPT_GPC_DEFECTIVE=0x{defective:08x})")


main()
