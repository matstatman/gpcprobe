# gpcprobe — GA100 floorsweep fuse probe (GPC / FBP / ROP / NVLink / PCIe / HBM)

Reads the GA100 floorsweep/restriction fuses straight out of BAR0, plus the
HBM controller readouts (I1500 debug bridge + FBPA config), and decodes the
fuses into the physical HBM stack layout.

## Register table

| Register          | BAR0 offset | Meaning                                        |
|-------------------|-------------|-------------------------------------------------|
| `OPT_GPC_DISABLE`   | `0x00820350` | one bit per GPC (8 groups) — fused OFF           |
| `OPT_GPC_DEFECTIVE` | `0x008205C4` | one bit per GPC — marked dead                    |
| `OPT_FBP_DISABLE`   | `0x00820364` | one bit per FBP (12 memory partitions) — OFF     |
| `OPT_FBP_DEFECTIVE`  | `0x008205CC` | one bit per FBP — physically dead (not just floorswept) |
| `OPT_FBPA_DISABLE`  | `0x00820368` | one bit per FBPA (24, two per FBP) — OFF         |
| `OPT_FBPA_DEFECTIVE` | `0x008205D0` | one bit per FBPA — physically dead             |
| `OPT_FBIO_DISABLE`  | `0x0082036C` | one bit per FBIO (24) — OFF                      |
| `OPT_FBIO_DEFECTIVE` | `0x008205D4` | one bit per FBIO — physically dead             |
| `OPT_ROP_L2_DISABLE`   | `0x008202C4` | one bit per ROP/L2 slice (24) — fused OFF (mirrors `OPT_FBPA_DISABLE`) |
| `OPT_ROP_L2_DEFECTIVE` | `0x008205E8` | one bit per ROP/L2 slice — physically dead      |
| `OPT_NVLINK_DISABLE`   | `0x00820684` | one bit per NVLink group (3) — fused OFF           |
| `OPT_NVLINK_DISABLE_CP`| `0x00820688` | NVLink CP disable mask                             |
| `OPT_NVLINK_DEFECTIVE` | `0x0082068C` | one bit per NVLink group — physically dead         |
| `OPT_PCIE_LANE_DISABLE`| `0x00820394` | one bit per PCIe lane (16) — fused OFF             |
| `OPT_GEN23`            | `0x0082057C` | Gen2/3 boot disable                                |
| `OPT_DISABLE_GEN3_SPEED`| `0x00820580` | Gen3 speed disable                                 |
| `OPT_SPARE_FS`         | `0x00820398` | spare floorsweep mask                              |
| `FUSE_FB_CONFIG`       | `0x00820328` | framebuffer config fuse (0 = no override of the RAMCFG board strap) |
| `FUSE_HALF_FBPA_EN`    | `0x0082049C` | per-FBPA half-capacity enable                      |
| `FUSE_ECC_EN`          | `0x00820228` | ECC enable fuse (controller-side DRAM ECC datapath) |
| `OPT_SECURE_GSP_DEBUG_DIS` | `0x0082074C` | secure GSP debug disabled (production part)       |
| `STATUS_OPT_DISPLAY`   | `0x00820C04` | display-disabled flag                              |
| `CTRL_OPT_GPC`         | `0x0082081C` | live GPC override (0 = none)                       |
| `CTRL_OPT_FBIO`        | `0x00820814` | live FBIO override (0 = none)                      |
| `CTRL_OPT_FBPA`        | `0x00820818` | live FBPA override (0 = none)                      |
| `CTRL_OPT_PERLINK`     | `0x00820820` | live per-link override (0 = none)                  |
| `CTRL_OPT_PCIE_LANE`   | `0x0082082C` | live PCIe-lane override (0 = none)                 |
| `CTRL_OPT_FBP`         | `0x00820938` | live FBP override (0 = none)                       |
| `CTRL_OPT_NVLINK`      | `0x008209B8` | live NVLink override (0 = none)                    |
| `FUSE_CTRL_OPT_TPC_GPC`| `0x00820838+i*4` | per-GPC TPC override, one dword per GPC 0..7 (remove-only) |
| `FUSE_STATUS_OPT_TPC_GPC` | `0x00820C38+i*4` | per-GPC TPC status, one dword per GPC 0..7     |
| `STATUS_OPT_GPC`    | `0x00820C1C` | read-only shadow of `OPT_GPC_DISABLE`            |
| `STATUS_FBP`        | `0x00820D38` | read-only shadow of `OPT_FBP_DISABLE`            |
| `STATUS_FBPA`       | `0x00820C18` | read-only shadow of `OPT_FBPA_DISABLE`           |
| `STATUS_OPT_FBIO`   | `0x00820C14` | read-only shadow of `OPT_FBIO_DISABLE`           |
| `STATUS_OPT_NVLINK`    | `0x00820DB8` | read-only shadow of `OPT_NVLINK_DISABLE`           |
| `STATUS_OPT_PCIE_LANE` | `0x00820C2C` | read-only shadow of `OPT_PCIE_LANE_DISABLE`        |
| `STATUS_SPARE_FS`      | `0x00820C30` | read-only shadow of `OPT_SPARE_FS`                 |
| `STATUS_FB_CONFIG`     | `0x00820C34` | read-only shadow of `FUSE_FB_CONFIG`               |
| `STATUS_HALF_FBPA`     | `0x00820C00` | read-only shadow of `FUSE_HALF_FBPA_EN`            |

### HBM controller readouts (raw values, not fuse bitmasks)

| Register          | BAR0 offset | Meaning                                        |
|-------------------|-------------|-------------------------------------------------|
| `I1500_INSTR`       | `0x009A3CB4` | IEEE 1500 HBM debug bridge — latched instruction (boot residue) |
| `I1500_MODE`        | `0x009A3CB8` | latched mode                                     |
| `I1500_DATA`        | `0x009A3CBC` | latched data                                     |
| `I1500_SHADOW_WIR`  | `0x009A3CC0` | RO — last WIR shifted in                         |
| `I1500_SHADOW_WDR`  | `0x009A3CC4` | RO — per-die WDR (e.g. `0x8000f000` on the 8 GB card family) |
| `I1500_STATUS`      | `0x009A3CC8` | 0 = idle                                         |
| `FBPA_NUM_ACTIVE`   | `0x009A0164` | active FBP count (8 on this SKU)                 |
| `FBPA_CFG0_BROADCAST`| `0x009A0200` | HBM config broadcast                             |
| `FBPA_CFG1_BROADCAST`| `0x009A0204` | HBM config broadcast — `0x02779000` is the A100-80 GB (HBM2E) value |
| `FBPA_MRS_0` / `_1` | `0x009A0300` / `0x009A0304` | memory-register state              |
| `FBPA_MRS_8`        | `0x009A0320` | density MR — `0x20` on all 15 reference cards (8/10 GB CMP, 40/80 GB A100) |
| `FBPA_MRS_2` / `_WL_RL` | `0x009A0334` / `0x009A0338` | memory-register state / write-level, read-level |
| `FBPA_HBM_CFG0`     | `0x009A038C` | HBM config word 0                                |
| `FBPA_ECC_CTRL`     | `0x009A0470` | GPU-side ECC datapath control (0 = off)          |
| `FBPA_VEND_ID_C0/C1`| `0x009A0838` / `0x009A083C` | DRAM vendor ID — 0 on all 15 reference cards |
| `FBPA_TRAINING_STATUS` | `0x009A0974` | 0 = not in training                           |

The I1500 bridge is the IEEE 1500 test port to the HBM dies. It is driven
only by the PKC-encrypted FB Falcon at boot — the GSP never touches it
(no I1500 register accesses in `gsp_ga10x.bin`) — so the values above are
latched boot residue, not a clean `DEVICE_ID`. A clean die-ID readout needs
WIR shift-in, i.e. register **writes**, plus the undocumented bridge
clocking protocol and the JESD235/238 WIR opcode.

Strictly **read-only**: the module only `ioremap`s BAR0 and issues `readl`.
It never writes to the BAR, and the nvidia driver keeps full ownership of
the device (no unbind, no reboot).

Offset provenance: JRex286's Ampere fuse probe, the
Consensus-Protocol/cmp170hx register docs, and the NVIDIA RM source
(`nv_fusefuse_ga100.h` / FBPA defs, 610.x branch)
([gist](https://gist.github.com/JRex286/0480d2b2b35ad594e57b6543952be307),
covers the full Ampere line including the CMP 170HX). Validated on this
hardware: on each card every `STATUS_*` shadow equals its fuse's value, so
a mis-assigned offset would have shown up as a mismatch.

## HBM stack layout

GA100 memory hierarchy: **6 HBM stacks** → 12 FBP (2 per stack) →
24 FBPA (2 per FBP, 4 per stack) → each FBPA is one 256-bit HBM channel
holding **2 DRAM dies** (full stack = 8 dies, 1024-bit).

Stack *s* = FBP {2*s*, 2*s*+1} = FBPA {4*s* … 4*s*+3}. The probe decodes
`OPT_FBPA_DISABLE`/`OPT_FBPA_DEFECTIVE` and `OPT_FBP_DISABLE`/
`OPT_FBP_DEFECTIVE` into this layout and prints a per-stack table. Per-FBPA
size is derived as `nvidia-smi total ÷ active FBPA count` (the
`FBPA_CFG1_BROADCAST`/CSTATUS window), so the table reflects the config the
card currently runs in:

| Config | per-FBPA | full stack | half stack | exposed |
|--------|----------|------------|------------|---------|
| stock 8 GiB SKU | 512 MiB | 2 GiB | 1 GiB | 8 GiB (16 × 512 MiB) |
| 64 GiB unlock (CFG1 = A100-80 GB value) | 4 GiB | 16 GiB | 8 GiB | 64 GiB (16 × 4 GiB) |

This SKU (both cards, identically fused): **2 full + 4 half stacks**,
16/24 FBPA = 4096-bit. FBP 1, 4 floorswept; FBP 6, 11 defective — one half
taken out of four different stacks. Dies are 2 GiB (16 Gb) class
(4 GiB/FBPA ÷ 2 dies), so the board carries up to 96 GiB of DRAM in its
48 die slots.

The 10 GB sibling (device id 0x2082) is 20/24 FBPA = 5120-bit and unlocks
to 80 GiB (20 × 4 GiB) — same die size, more FBP fuses intact. The stock
8 vs 10 GB difference is **FBP fusing**, not die size.

### HBM generation / ECC

`FBPA_CFG1_BROADCAST = 0x02779000` (the A100-80 GB HBM2E config) bringing up
64 GiB, plus the identical density MR (`FBPA_MRS_8 = 0x20`) across all 15
reference cards (8/10 GB CMP *and* 40/80 GB A100), points to HBM2E 16 Gb
dies — which have on-die ECC silicon (plain HBM2 does not). The RAMCFG board
strap (A100 datasheet: `00100` = Samsung HBM2 8 Gb, `01111` = Hynix HBM2E
16 Gb, `01010` = Micron HBM2E 16 Gb) is sampled by the FB Falcon, not the
floorsweep (`FUSE_FB_CONFIG = 0` on both cards), so the exact vendor is not
pinned down from BAR0; `FBPA_VEND_ID_C0/C1` read 0 (never populated on any
of the 15 reference cards). What *is* pinned: the GPU-side ECC datapath
(`FUSE_ECC_EN`, `FEATURE_READOUT.ECC_DRAM`, `FBPA_ECC_CTRL`) is fused off,
and any die-level ECC enable lives in the HBM MR space programmed only by
the boot-time FB Falcon (runtime MRS writes are dropped on source identity).

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

The script prints a per-card detail block (fuse groups, I1500 bridge, FBPA
HBM config, decoded HBM stack table), then a **Summary** table (one row per
card). The Card column carries the BDF, model name and total memory
(queried from nvidia-smi, matched by PCI bus id; shows a question mark if
nvidia-smi is unavailable); the remaining columns carry the healthy /
disabled / defective counts for GPCs and FBPs (the bit indices and the
per-stack breakdown are in the per-card detail).

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
== Card 05  (0000:05:00.0) ==
  GPC  (compute clusters)
    OPT_GPC_DISABLE          0x00000085  bits 0, 2, 7                  5/8 active
    OPT_GPC_DEFECTIVE        0x00000080  bits 7                        7/8 active
    disabled not defective        0, 2
  FBP  (memory partitions)
    OPT_FBP_DISABLE          0x00000852  bits 1, 4, 6, 11              8/12 active
    OPT_FBP_DEFECTIVE        0x00000840  bits 6, 11                    10/12 active
    OPT_FBPA_DISABLE         0x00c0330c  bits 2, 3, 8, 9, 12, 13, 22, 23 16/24 active
    OPT_FBPA_DEFECTIVE       0x00c03000  bits 12, 13, 22, 23           20/24 active
    OPT_FBIO_DISABLE         0x00c0330c  bits 2, 3, 8, 9, 12, 13, 22, 23 16/24 active
    OPT_FBIO_DEFECTIVE       0x00c03000  bits 12, 13, 22, 23           20/24 active
    disabled not defective        1, 4
  ROP  (raster ops / L2 slices)
    OPT_ROP_L2_DISABLE       0x00c0330c  bits 2, 3, 8, 9, 12, 13, 22, 23 16/24 active
    OPT_ROP_L2_DEFECTIVE     0x00c03000  bits 12, 13, 22, 23           20/24 active
    disabled not defective        2, 3, 8, 9
  NVLink
    OPT_NVLINK_DISABLE       0x00000007  bits 0, 1, 2                  0/3 active
    OPT_NVLINK_DISABLE_CP    0x00000000  bits none                     3/3 active
    OPT_NVLINK_DEFECTIVE     0x00000000  bits none                     3/3 active
    disabled not defective        0, 1, 2
  PCIe (lanes / link speed)
    OPT_PCIE_LANE_DISABLE    0x00000000  bits none                     16/16 active
    OPT_GEN23                0x00000001  bit0 set
    OPT_DISABLE_GEN3_SPEED   0x00000001  bit0 set
  Misc fuses
    OPT_SPARE_FS             0x00000000  bits none                     16/16 active
    FUSE_FB_CONFIG           0x00000000  bits none                     4/4 active
    FUSE_HALF_FBPA_EN        0x00000000  bits none                     24/24 active
    FUSE_ECC_EN              0x00000000  bit0 clear
    OPT_SECURE_GSP_DEBUG_DIS 0x00000001  bit0 set
    STATUS_OPT_DISPLAY       0x00000001  bit0 set
  CTRL / override readouts (0 = no live override)
    CTRL_OPT_GPC             0x00000000  (no override)
    CTRL_OPT_FBIO            0x00000000  (no override)
    CTRL_OPT_FBPA            0x00000000  (no override)
    CTRL_OPT_PERLINK         0x00000000  (no override)
    CTRL_OPT_PCIE_LANE       0x00000000  (no override)
    CTRL_OPT_FBP             0x00000000  (no override)
    CTRL_OPT_NVLINK          0x00000000  (no override)
  I1500 HBM debug bridge (latched boot residue)
    I1500_INSTR              0x0000000f  (latched instruction)
    I1500_MODE               0x00000008  (latched mode)
    I1500_DATA               0x32000000  (latched data)
    I1500_SHADOW_WIR         0x000000f0  (RO, last WIR shifted in)
    I1500_SHADOW_WDR         0x6400f000  (RO, per-die WDR)
    I1500_STATUS             0x00000000  (0 = idle)
  FBPA HBM config (broadcast window)
    FBPA_NUM_ACTIVE          0x00000008  (active FBP count)
    FBPA_CFG0_BROADCAST      0x07981800  (HBM config)
    FBPA_CFG1_BROADCAST      0x02779000  (0x02779000 = A100-80GB HBM2E config)
    FBPA_MRS_0               0x00000003
    FBPA_MRS_1               0x00100000
    FBPA_MRS_8               0x00200000  (density MR (0x20 on all 15 ref cards))
    FBPA_MRS_2               0x00200019
    FBPA_MRS_WL_RL           0x003000eb
    FBPA_HBM_CFG0            0x000000a7
    FBPA_ECC_CTRL            0x00000000  (GPU-side ECC datapath (0 = off))
    FBPA_VEND_ID_C0          0x00000000  (0 on all 15 ref cards)
    FBPA_VEND_ID_C1          0x00000000  (0 on all 15 ref cards)
    FBPA_TRAINING_STATUS     0x00000000  (0 = not in training)
  HBM stacks (stack s = FBP 2s,2s+1 = FBPA 4s..4s+3; 1 FBPA = 1 channel = 2 dies)
    stk  FBP    FBPA active     dies  state                    size
    0    0,1   0, 1            4/8  half (FBP1 dis)          8 GiB
    1    2,3   4, 5, 6, 7      8/8  full                     16 GiB
    2    4,5   10, 11          4/8  half (FBP4 dis)          8 GiB
    3    6,7   14, 15          4/8  half (FBP6 def)          8 GiB
    4    8,9   16, 17, 18, 19  8/8  full                     16 GiB
    5    10,11  20, 21          4/8  half (FBP11 def)         8 GiB
    16/24 FBPA = 4096-bit  |  per-FBPA 4 GiB  |  die 2 GiB  |  64 GiB exposed  |  96 GiB max (all 48 die slots)
  TPC/GPC arrays (one dword per GPC)
    FUSE_CTRL_OPT_TPC_GPC    0:0x00  1:0x00  2:0x00  3:0x00  4:0x00  5:0x00  6:0x00  7:0x00
    FUSE_STATUS_OPT_TPC_GPC  0:0xff  1:0x01  2:0xff  3:0x01  4:0x01  5:0x01  6:0x01  7:0xff
  status shadows (should equal fuses)
    STATUS_OPT_GPC           0x00000085  [ok]
    STATUS_FBP               0x00000852  [ok]
    STATUS_FBPA              0x00c0330c  [ok]
    STATUS_OPT_FBIO          0x00c0330c  [ok]
    STATUS_OPT_NVLINK        0x00000007  [ok]
    STATUS_OPT_PCIE_LANE     0x00000000  [ok]
    STATUS_SPARE_FS          0x00000000  [ok]
    STATUS_FB_CONFIG         0x00000000  [ok]
    STATUS_HALF_FBPA         0x00000000  [ok]
```

- **GPC (8 on GA100, 14 SMs each, 7 TPCs × 2 SMs = 112 SMs full die):**
  compute clusters. `disabled not defective` = trimmed by the floorsweep but
  not fused dead — intentional cuts rather than die repairs. GPC fuses are
  **per-die** (each card differs).
- **FBP / FBPA / FBIO:** the memory side — see the HBM stack layout section
  above. 16/24 FBPA (8 FBP) active on this SKU, identical on both cards.
  The 64 GiB the card reports is the same 16 FBPAs with a 4 GiB window each
  (CFG1 broadcast = the A100-80 GB value); stock config is the same 16
  FBPAs at 512 MiB each.
- **I1500 bridge:** latched residue from the FB Falcon's boot-time HBM
  bring-up. `SHADOW_WDR` family: `0x8000f000` (8 GB cards), `0x8273ff83`
  (10 GB card), `0xNN00f000` (A100s) — the high byte differs per card and
  nobody has decoded it into vendor/density yet.
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
