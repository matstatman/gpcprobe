# gpcprobe — GA100 floorsweep fuse probe (GPC + FBP)

Reads the GA100 floorsweep/restriction fuses straight out of BAR0:

| Register          | BAR0 offset | Meaning                                        |
|-------------------|-------------|-------------------------------------------------|
| `OPT_GPC_DISABLE`   | `0x00820350` | one bit per GPC (8 groups) — fused OFF           |
| `OPT_GPC_DEFECTIVE` | `0x008205C4` | one bit per GPC — marked dead                    |
| `OPT_FBP_DISABLE`   | `0x00820364` | one bit per FBP (12 memory partitions) — OFF     |
| `OPT_FBP_DEFECTIVE`  | `0x008205CC` | one bit per FBP — physically dead (not just floorswept) |
| `OPT_FBPA_DEFECTIVE` | `0x008205D0` | one bit per FBPA — physically dead             |
| `OPT_FBIO_DEFECTIVE` | `0x008205D4` | one bit per FBIO — physically dead             |
| `OPT_ROP_L2_DISABLE`   | `0x008202C4` | one bit per ROP/L2 slice — fused OFF (mirrors `OPT_FBPA_DISABLE`) |
| `OPT_ROP_L2_DEFECTIVE` | `0x008205E8` | one bit per ROP/L2 slice — physically dead      |
| `OPT_FBPA_DISABLE`  | `0x00820368` | one bit per FBPA (24, two per FBP) — OFF         |
| `OPT_FBIO_DISABLE`  | `0x0082036C` | one bit per FBIO (24) — OFF                      |
| `STATUS_OPT_GPC`    | `0x00820C1C` | read-only shadow of `OPT_GPC_DISABLE`            |
| `STATUS_FBP`        | `0x00820D38` | read-only shadow of `OPT_FBP_DISABLE`            |
| `STATUS_FBPA`       | `0x00820C18` | read-only shadow of `OPT_FBPA_DISABLE`           |
| `STATUS_OPT_FBIO`   | `0x00820C14` | read-only shadow of `OPT_FBIO_DISABLE`           |

Strictly **read-only**: the module only `ioremap`s BAR0 and issues `readl`.
It never writes to the BAR, and the nvidia driver keeps full ownership of
the device (no unbind, no reboot).

Offset provenance: JRex286's Ampere fuse probe and the
Consensus-Protocol/cmp170hx register docs
([gist](https://gist.github.com/JRex286/0480d2b2b35ad594e57b6543952be307),
covers the full Ampere line including the CMP 170HX). Validated on this
hardware: on each card every `STATUS_*` shadow equals its fuse's value, so
a mis-assigned offset would have shown up as a mismatch.

## Why a kernel module at all

On this box the usual tricks do not work:

- **`/sys/bus/pci/devices/<bdf>/resource0`** — the proprietary nvidia driver
  owns the GPU BARs exclusively and zeroes the kernel's resource entries
  (the BARs show as `00000000-00000000` in `/proc/iomem`). `mmap()` on the
  sysfs resource fails with `EINVAL`, `read()` with `EIO`, for any offset or
  flag combination.
- **`/dev/mem`** — blocked by `CONFIG_STRICT_DEVMEM=y`, and the kernel has no
  record of the ranges anyway. The real addresses (`0xfa000000` /
  `0xf9000000`, 16 MiB each) exist only in the raw PCI config space, which
  RM programs itself.

`gpcprobe.ko` solves this by reading the raw BAR0 config register of every
NVIDIA GPU (`vendor 0x10de`, class `0x03xx`), `ioremap`ing 16 MiB of it, and
exposing 32-bit reads through `/dev/gpcprobe` via ioctl.

## Files

| File          | What it is                                  |
|---------------|---------------------------------------------|
| `gpcprobe.c`  | the kernel module                           |
| `Makefile`    | out-of-tree build against the running kernel|
| `gpc_probe.py`| Python frontend — auto-discovers all cards, no hardcoded BDF list |

## Build

Needs headers matching the **running** kernel:

```sh
sudo apt install -y linux-headers-$(uname -r)
make
```

Rebuild after every kernel upgrade (the module is version-pinned to
`/lib/modules/$(uname -r)/build`).

The script prints a per-card detail block, then a **Summary** table (one row per card). The Card column carries the BDF, model name and total memory (queried from nvidia-smi, matched by PCI bus id; shows a question mark if nvidia-smi is unavailable); the remaining columns carry the healthy / disabled / defective counts for GPCs and FBPs (the bit indices are in the per-card detail). FBPA/FBIO detail stays in the per-card block.

## Usage

```sh
# 1. load (prints one line per mapped BAR0)
sudo insmod gpcprobe.ko
sudo dmesg | grep gpcprobe

# 2. probe (needs root for /dev/gpcprobe)
sudo python3 gpc_probe.py

# 3. unload when done
sudo rmmod gpcprobe
```

## Output

```
== Card 06  (0000:06:00.0) ==
  GPC  (compute clusters)
    OPT_GPC_DISABLE    0x0000000b  GPCs 0, 1, 3        5/8 active
    OPT_GPC_DEFECTIVE  0x00000008  GPCs 3              7/8 active
    disabled not defective          0, 1
  FBP  (memory partitions)
    OPT_FBP_DISABLE    0x00000852  FBPs 1, 4, 6, 11    8/12 active
    OPT_FBPA_DISABLE   0x00c0330c  FBPAs 2,3,8,9,12,13,22,23  16/24 active
    OPT_FBIO_DISABLE   0x00c0330c  FBIOs 2,3,8,9,12,13,22,23  16/24 active
  status shadows (should equal fuses)
    STATUS_OPT_GPC     0x0000000b  [ok]
    STATUS_FBP         0x00000852  [ok]
    STATUS_FBPA        0x00c0330c  [ok]
    STATUS_OPT_FBIO    0x00c0330c  [ok]
```

- **GPC (8 on GA100, 14 SMs each, 7 TPCs × 2 SMs = 112 SMs full die):**
  compute clusters. `disabled not defective` = trimmed by the floorsweep but
  not fused dead — intentional cuts rather than die repairs. GPC fuses are
  **per-die** (each card differs).
- **FBP / FBPA / FBIO:** the memory side. 12 FBPs (8 active here × 640 bits =
  the full 5120-bit HBM interface), each FBP split into 2 FBPAs, plus 24
  FBIO interface partitions. These are **SKU-identical** (product-line
  restriction, same on both cards) — on this SKU the capacity difference
  versus other parts comes from the HBM die size (0x20c2 = 8 × 8 GB = 64 GB),
  not from FBP fusing.
- **Status shadows:** read-only copies the firmware consumes. A `[!= fuse]`
  flag means a live override has diverged from the fused value.

## Notes

- Card indices are PCI enumeration order; the BDF of each card is logged by
  the module at `insmod` time and is returned by the `GP_IOC_INFO` ioctl.
- Only the first 16 MiB of BAR0 (the register window) is mapped; out-of-range
  offsets are rejected with `ERANGE`.
- If a GPU is mid-reset or its BAR is temporarily unavailable the module
  just skips it (visible in `dmesg`); rerun `insmod` later.
- Nothing here touches GPU state: the nvidia driver, HBM contents, and any
  running CUDA work are unaffected by loading/unloading the module.
