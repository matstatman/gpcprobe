# gpcprobe — GA100 GPC floorsweep fuse probe

Reads the GA100 GPC floorsweep fuses straight out of BAR0:

| Register        | BAR0 offset | Meaning                                    |
|-----------------|-------------|--------------------------------------------|
| `OPT_GPC_DISABLE`   | `0x00820350` | one bit per GPC group — fused OFF          |
| `OPT_GPC_DEFECTIVE` | `0x008205c4` | one bit per GPC group — marked dead        |

Strictly **read-only**: the module only `ioremap`s BAR0 and issues `readl`.
It never writes to the BAR, and the nvidia driver keeps full ownership of
the device (no unbind, no reboot).

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
Card   Disabled clusters    Marked defective   Disabled but not defective
06     0, 1, 3              3                  0, 1
       (raw: OPT_GPC_DISABLE=0x0000000b OPT_GPC_DEFECTIVE=0x00000008)
05     0, 2, 7              7                  0, 2
       (raw: OPT_GPC_DISABLE=0x00000085 OPT_GPC_DEFECTIVE=0x00000080)
```

- **Card** — PCI slot (bus number) of the GPU.
- **Disabled clusters** — GPC groups masked off in `OPT_GPC_DISABLE`
  (bit N = GPC N). GA100 die: 8 GPCs x 14 SMs (7 TPCs x 2 SMs) = 112 SMs full die; these cards run 5 GPCs = 70 SMs = 4480 CUDA cores.
- **Marked defective** — GPCs the fuser marked dead.
- **Disabled but not defective** — GPCs trimmed by the floorsweep but *not*
  fused as defective, i.e. intentional cuts rather than die repairs.

## Notes

- Card indices are PCI enumeration order; the BDF of each card is logged by
  the module at `insmod` time and is returned by the `GP_IOC_INFO` ioctl.
- Only the first 16 MiB of BAR0 (the register window) is mapped; out-of-range
  offsets are rejected with `ERANGE`.
- If a GPU is mid-reset or its BAR is temporarily unavailable the module
  just skips it (visible in `dmesg`); rerun `insmod` later.
- Nothing here touches GPU state: the nvidia driver, HBM contents, and any
  running CUDA work are unaffected by loading/unloading the module.
