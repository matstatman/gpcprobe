#!/usr/bin/env python3
"""Read-only GA100 floorsweep fuse probe: GPC + FBP + ROP/L2 + NVLink + PCIe + HBM.

Uses the gpcprobe kernel module (insmod gpcprobe.ko), which ioremaps each
NVIDIA GPU's BAR0 read-only because the proprietary nvidia driver owns the
BARs and blocks the usual sysfs resource0 / /dev/mem paths.

Prints a per-card detail block (fuse groups, I1500 HBM debug bridge, FBPA HBM
config, decoded HBM stack table, per-GPC TPC masks including
OPT_TPC_GPC[i]_DEFECTIVE, SM census from the TPC status arrays), then a
summary table (card/model/memory via nvidia-smi, fuses and SM total from
BAR0).

Register map (GA100 BAR0):
  GPC   OPT_GPC_DISABLE      0x00820350  one bit per GPC         (8)
  GPC   OPT_GPC_DEFECTIVE    0x008205c4  one bit per GPC
  FBP   OPT_FBP_DISABLE      0x00820364  one bit per FBP         (12)
  FBP   OPT_FBP_DEFECTIVE    0x008205cc  one bit per FBP
  FBP   OPT_FBPA_DISABLE     0x00820368  one bit per FBPA        (24)
  FBP   OPT_FBPA_DEFECTIVE   0x008205d0  one bit per FBPA
  FBP   OPT_FBIO_DISABLE     0x0082036c  one bit per FBIO        (24)
  FBP   OPT_FBIO_DEFECTIVE   0x008205d4  one bit per FBIO
  ROP   OPT_ROP_L2_DISABLE    0x008202c4  one bit per LTC (24), mirrors OPT_FBPA_DISABLE
  ROP   OPT_ROP_L2_DEFECTIVE  0x008205e8  one bit per LTC
  NVL   OPT_NVLINK_DISABLE    0x00820684  one bit per NVLink group (3)
  NVL   OPT_NVLINK_DISABLE_CP 0x00820688
  NVL   OPT_NVLINK_DEFECTIVE  0x0082068c
  PCIE  OPT_PCIE_LANE_DISABLE  0x00820394 one bit per lane (16)
  PCIE  OPT_GEN23               0x0082057c Gen2/3 boot disable
  PCIE  OPT_DISABLE_GEN3_SPEED  0x00820580
  MISC  OPT_SPARE_FS             0x00820398
  MISC  FUSE_FB_CONFIG           0x00820328
  MISC  FUSE_HALF_FBPA_EN        0x0082049c
  MISC  FUSE_ECC_EN              0x00820228
  MISC  OPT_SECURE_GSP_DEBUG_DIS 0x0082074c
  MISC  STATUS_OPT_DISPLAY       0x00820c04 (display-disabled flag)
  CTRL  CTRL_OPT_GPC 0x0082081c, CTRL_OPT_FBIO 0x00820814,
        CTRL_OPT_FBPA 0x00820818, CTRL_OPT_PERLINK 0x00820820,
        CTRL_OPT_PCIE_LANE 0x0082082c, CTRL_OPT_FBP 0x00820938,
        CTRL_OPT_NVLINK 0x008209b8
  TPC (one dword per GPC, i = 0..7; low byte, bit t = TPC t):
        OPT_TPC_GPC[i]_DEFECTIVE  scattered; a set bit is bad silicon
            0 0x008205c8  1 0x00820508  2 0x0082050c  3 0x00820514
            4 0x00820564  5 0x008205ac  6 0x00820b84  7 0x00820b80
        OPT_TPC_GPC[i]_DISABLE    0x00820760 + i*4   fuseless floorsweep
        OPT_TPC_GPC[i]_RECONFIG   0x00820788 + i*4 for GPC 0-5;
                                  GPC 6 0x00820b94, GPC 7 0x00820b90
        FUSE_CTRL_OPT_TPC_GPC     0x00820838 + i*4   SW override input
        RECONFIG_OVERRIDE         0x00820a40 + i*4   drops a reconfig bit
        FUSE_STATUS_OPT_TPC_GPC   0x00820c38 + i*4   effective mask
        With CTRL and RECONFIG_OVERRIDE at 0, STATUS = DISABLE | RECONFIG.
        STATUS also holds SKU-disable and reconfig bits; it is not the
        defective fuse.
  read-only status shadows:
        STATUS_OPT_GPC 0x00820c1c   STATUS_FBP 0x00820d38
        STATUS_FBPA    0x00820c18   STATUS_OPT_FBIO 0x00820c14
        STATUS_OPT_NVLINK 0x00820db8   STATUS_OPT_PCIE_LANE 0x00820c2c
        STATUS_SPARE_FS 0x00820c30     STATUS_FB_CONFIG 0x00820c34
        STATUS_HALF_FBPA 0x00820c00
  I1500 IEEE 1500 HBM debug bridge (raw readouts, not bitmasks):
        I1500_INSTR       0x009A3CB4  latched instruction (boot residue)
        I1500_MODE        0x009A3CB8  latched mode
        I1500_DATA        0x009A3CBC  latched data
        I1500_SHADOW_WIR  0x009A3CC0  RO, last WIR shifted in
        I1500_SHADOW_WDR  0x009A3CC4  RO, per-die WDR
        I1500_STATUS      0x009A3CC8  0 = idle
        The bridge is driven by the PKC-encrypted FB Falcon at boot (the GSP
        never touches it: no I1500 accesses in gsp_ga10x.bin), so live reads
        show latched boot residue, not a clean DEVICE_ID. A clean read needs
        WIR shift-in, i.e. register writes.
  FBPA HBM config (raw readouts, broadcast window):
        FBPA_NUM_ACTIVE       0x009A0164  active FBP count
        FBPA_CFG0_BROADCAST   0x009A0200
        FBPA_CFG1_BROADCAST   0x009A0204  0x02779000 = A100-80GB HBM2E config
        FBPA_MRS_0            0x009A0300
        FBPA_MRS_1            0x009A0304
        FBPA_MRS_8            0x009A0320  density MR
        FBPA_MRS_2            0x009A0334
        FBPA_MRS_WL_RL        0x009A0338
        FBPA_HBM_CFG0         0x009A038C
        FBPA_ECC_CTRL         0x009A0470  GPU-side ECC datapath (0 = off)
        FBPA_VEND_ID_C0       0x009A0838
        FBPA_VEND_ID_C1       0x009A083C
        FBPA_TRAINING_STATUS  0x009A0974  0 = not in training
  HBM stack topology (decoded by the script from the FBP/FBPA fuses):
        6 stacks; stack s = FBP {2s, 2s+1} = FBPA {4s..4s+3}
        1 FBPA = 1 HBM channel (256-bit) = 2 DRAM dies; full stack = 8 dies
  Offsets per JRex286's Ampere fuse probe (gist 0480d2b2), the
  Consensus-Protocol/cmp170hx register docs, and the NVIDIA RM source
  (nv_fusefuse_ga100.h / fbpa defs); validated on this hardware: every
  status shadow equals its fuse's value. Bit widths for a few misc fuses
  are nominal (they only affect the active count).
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
        ("OPT_GPC_DISABLE",     0x00820350, 8,  0x00820C1C),
        ("OPT_GPC_DEFECTIVE",   0x008205C4, 8,  None),
    ]),
    ("FBP  (memory partitions)", [
        ("OPT_FBP_DISABLE",     0x00820364, 12, 0x00820D38),
        ("OPT_FBP_DEFECTIVE",   0x008205CC, 12, None),
        ("OPT_FBPA_DISABLE",    0x00820368, 24, 0x00820C18),
        ("OPT_FBPA_DEFECTIVE",  0x008205D0, 24, None),
        ("OPT_FBIO_DISABLE",    0x0082036C, 24, 0x00820C14),
        ("OPT_FBIO_DEFECTIVE",  0x008205D4, 24, None),
    ]),
    ("ROP  (raster ops / L2 slices)", [
        ("OPT_ROP_L2_DISABLE",   0x008202C4, 24, None),
        ("OPT_ROP_L2_DEFECTIVE", 0x008205E8, 24, None),
    ]),
    ("NVLink", [
        ("OPT_NVLINK_DISABLE",    0x00820684, 3, 0x00820DB8),
        ("OPT_NVLINK_DISABLE_CP", 0x00820688, 3, None),
        ("OPT_NVLINK_DEFECTIVE",  0x0082068C, 3, None),
    ]),
    ("PCIe (lanes / link speed)", [
        ("OPT_PCIE_LANE_DISABLE",  0x00820394, 16, 0x00820C2C),
        ("OPT_GEN23",              0x0082057C, 1,  None),
        ("OPT_DISABLE_GEN3_SPEED", 0x00820580, 1,  None),
    ]),
    ("Misc fuses", [
        ("OPT_SPARE_FS",             0x00820398, 16, 0x00820C30),
        ("FUSE_FB_CONFIG",           0x00820328, 4,  0x00820C34),
        ("FUSE_HALF_FBPA_EN",        0x0082049C, 24, 0x00820C00),
        ("FUSE_ECC_EN",              0x00820228, 1,  None),
        ("OPT_SECURE_GSP_DEBUG_DIS", 0x0082074C, 1,  None),
        ("STATUS_OPT_DISPLAY",       0x00820C04, 1,  None),
    ]),
    ("CTRL / override readouts (0 = no live override)", [
        ("CTRL_OPT_GPC",       0x0082081C, 8,  None),
        ("CTRL_OPT_FBIO",      0x00820814, 24, None),
        ("CTRL_OPT_FBPA",      0x00820818, 24, None),
        ("CTRL_OPT_PERLINK",   0x00820820, 3,  None),
        ("CTRL_OPT_PCIE_LANE", 0x0082082C, 16, None),
        ("CTRL_OPT_FBP",       0x00820938, 12, None),
        ("CTRL_OPT_NVLINK",    0x008209B8, 3,  None),
    ]),
]

# (label, offset, note) — raw 32-bit readouts, not fuse bitmasks
RAW_GROUPS = [
    ("I1500 HBM debug bridge (latched boot residue)", [
        ("I1500_INSTR",      0x009A3CB4, "latched instruction"),
        ("I1500_MODE",       0x009A3CB8, "latched mode"),
        ("I1500_DATA",       0x009A3CBC, "latched data"),
        ("I1500_SHADOW_WIR", 0x009A3CC0, "RO, last WIR shifted in"),
        ("I1500_SHADOW_WDR", 0x009A3CC4, "RO, per-die WDR"),
        ("I1500_STATUS",     0x009A3CC8, "0 = idle"),
    ]),
    ("FBPA HBM config (broadcast window)", [
        ("FBPA_NUM_ACTIVE",      0x009A0164, "active FBP count"),
        ("FBPA_CFG0_BROADCAST",  0x009A0200, "HBM config"),
        ("FBPA_CFG1_BROADCAST",  0x009A0204, "0x02779000 = A100-80GB HBM2E config"),
        ("FBPA_MRS_0",           0x009A0300, None),
        ("FBPA_MRS_1",           0x009A0304, None),
        ("FBPA_MRS_8",           0x009A0320, "density MR (0x20 on all 15 ref cards)"),
        ("FBPA_MRS_2",           0x009A0334, None),
        ("FBPA_MRS_WL_RL",       0x009A0338, None),
        ("FBPA_HBM_CFG0",        0x009A038C, None),
        ("FBPA_ECC_CTRL",        0x009A0470, "GPU-side ECC datapath (0 = off)"),
        ("FBPA_VEND_ID_C0",      0x009A0838, "0 on all 15 ref cards"),
        ("FBPA_VEND_ID_C1",      0x009A083C, "0 on all 15 ref cards"),
        ("FBPA_TRAINING_STATUS", 0x009A0974, "0 = not in training"),
    ]),
]

# Per-GPC TPC shadows. DEFECTIVE is not a stride. DATA[7:0] of each fuse
# shadow is TPC 0..7; a set bit is that TPC.
# DEFECTIVE = bad silicon. DISABLE = fuseless floorsweep. RECONFIG is the
# reconfig fuse (GPC 6/7 leave the 0x00820788 stride). STATUS is the
# effective mask the chip consumes; with CTRL and RECONFIG_OVERRIDE at 0,
# STATUS = DISABLE | RECONFIG. RECONFIG_OVERRIDE drops a reconfig bit
# out of STATUS.
TPC_DEFECTIVE = (
    0x008205C8,  # GPC 0
    0x00820508,  # GPC 1
    0x0082050C,  # GPC 2
    0x00820514,  # GPC 3
    0x00820564,  # GPC 4
    0x008205AC,  # GPC 5
    0x00820B84,  # GPC 6
    0x00820B80,  # GPC 7
)
TPC_CTRL_BASE = 0x00820838
TPC_STATUS_BASE = 0x00820C38
TPC_OVERRIDE_BASE = 0x00820A40


def tpc_disable_off(g):
    return 0x00820760 + 4 * g


def tpc_reconfig_off(g):
    if g <= 5:
        return 0x00820788 + 4 * g
    return 0x00820B94 if g == 6 else 0x00820B90


# GPC structure for the SM census: each GPC has 8 TPC slot positions in the
# status byte; every live GPC on this die fuses one out (stock 0x01), leaving
# 7 TPC x 2 SM = 14 SM/GPC. STATUS_OPT_TPC is the effective state (it reflects
# the reconfig-override writes: 0x01 -> 0x00 there showed up as +2 SM and CUDA
# agreed).
TPC_SLOTS_PER_GPC = 8
SM_PER_TPC = 2

STATUS_NAMES = {
    0x00820C1C: "STATUS_OPT_GPC",
    0x00820D38: "STATUS_FBP",
    0x00820C18: "STATUS_FBPA",
    0x00820C14: "STATUS_OPT_FBIO",
    0x00820DB8: "STATUS_OPT_NVLINK",
    0x00820C2C: "STATUS_OPT_PCIE_LANE",
    0x00820C30: "STATUS_SPARE_FS",
    0x00820C34: "STATUS_FB_CONFIG",
    0x00820C00: "STATUS_HALF_FBPA",
}

# group title -> (disable label, defective label, width)
DELTA = {
    "GPC": ("OPT_GPC_DISABLE", "OPT_GPC_DEFECTIVE", 8),
    "FBP": ("OPT_FBP_DISABLE", "OPT_FBP_DEFECTIVE", 12),
    "ROP": ("OPT_ROP_L2_DISABLE", "OPT_ROP_L2_DEFECTIVE", 24),
    "NVLink": ("OPT_NVLINK_DISABLE", "OPT_NVLINK_DEFECTIVE", 3),
}

NUM_STACKS = 6  # GA100 HBM stack slots


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
                dom, rest = parts[0].lower().split(":", 1)
                meta[dom[-4:] + ":" + rest] = (parts[1], int(parts[2]))
            except (ValueError, AttributeError):
                pass
    return meta


def fmt_mem(mib):
    if mib is None:
        return "?"
    gib = mib / 1024
    return f"{gib:.0f} GiB" if gib == int(gib) else f"{gib:.1f} GiB"


def stack_report(vals, mib):
    """Decode the FBP/FBPA fuses into the 6 HBM stacks and print sizes.

    Stack s = FBP {2s, 2s+1} = FBPA {4s..4s+3}. One FBPA is one 256-bit HBM
    channel holding 2 DRAM dies, so a full stack is 4 FBPAs = 8 dies. The
    per-FBPA size is the nvidia-smi total divided by the active FBPA count
    (the CFG1/CSTATUS broadcast window), so the table reflects the config the
    card is currently running in (stock 512 MiB/FBPA vs 4 GiB/FBPA unlocked).
    """
    dis = set(bits_set(vals["OPT_FBPA_DISABLE"], 24))
    dfc = set(bits_set(vals["OPT_FBPA_DEFECTIVE"], 24))
    f_dis = set(bits_set(vals["OPT_FBP_DISABLE"], 12))
    f_dfc = set(bits_set(vals["OPT_FBP_DEFECTIVE"], 12))
    active = [k for k in range(24) if k not in dis and k not in dfc]
    per = (mib // len(active)
           if mib and active and mib % len(active) == 0 else None)

    print("  HBM stacks (stack s = FBP 2s,2s+1 = FBPA 4s..4s+3; 1 FBPA = 1 channel = 2 dies)")
    print(f"    {'stk':<4} {'FBP':<6} {'FBPA active':<15} {'dies':<5} {'state':<24} size")
    for s in range(NUM_STACKS):
        fbpas = range(4 * s, 4 * s + 4)
        act = [k for k in fbpas if k not in dis and k not in dfc]
        dies = 2 * len(act)
        state = {4: "full", 2: "half", 0: "dead"}.get(len(act), f"odd ({len(act)}/4)")
        if len(act) < 4:
            reasons = []
            for f in (2 * s, 2 * s + 1):
                if f in f_dfc:
                    reasons.append(f"FBP{f} def")
                elif f in f_dis:
                    reasons.append(f"FBP{f} dis")
            if reasons:
                state += " (" + ", ".join(reasons) + ")"
        size = fmt_mem(per * len(act)) if per else "?"
        print(f"    {s:<4} {2*s},{2*s+1:<3} {fmt(act):<15} {dies}/8  {state:<24} {size}")

    parts = [f"{len(active)}/24 FBPA = {256 * len(active)}-bit"]
    if per:
        parts.append(f"per-FBPA {fmt_mem(per)}")
        parts.append(f"die {fmt_mem(per // 2)}")
        if mib:
            parts.append(f"{fmt_mem(mib)} exposed")
            parts.append(f"{fmt_mem(NUM_STACKS * 8 * per // 2)} max (all 48 die slots)")
    print("    " + "  |  ".join(parts))


def _tpc_row(g, dfc, dis, rec, st, ct, ov, bad):
    return (f"    {g:<3}  {dfc:<10}  {dis:<8}  {rec:<9}  "
            f"{st:<7}  {ct:<6}  {ov:<4}  {bad}")


def tpc_report(rd):
    """Print the per-GPC TPC masks and return (ctrl[], status[]).

    OPT_TPC_GPC[i]_DEFECTIVE is the bad-silicon byte. STATUS is the
    effective floorsweep and also contains SKU-disable and reconfig, so
    it is the wrong register for "is this TPC defective". With CTRL at 0,
    STATUS = (DISABLE | RECONFIG) & ~RECONFIG_OVERRIDE.
    """
    print("  TPC per GPC (defective = bad silicon; status = effective mask)")
    print(_tpc_row("GPC", "defective", "disable", "reconfig",
                   "status", "ctrl", "ovr", "bad TPCs"))
    ctrl, status = [], []
    bad_parts = []
    for g in range(8):
        dfc = rd(TPC_DEFECTIVE[g]) & 0xFF
        dis = rd(tpc_disable_off(g)) & 0xFF
        rec = rd(tpc_reconfig_off(g)) & 0xFF
        st = rd(TPC_STATUS_BASE + 4 * g)
        ct = rd(TPC_CTRL_BASE + 4 * g)
        ov = rd(TPC_OVERRIDE_BASE + 4 * g)
        status.append(st)
        ctrl.append(ct)
        bits = bits_set(dfc, 8)
        if bits:
            bad_parts.append(f"GPC {g} TPC {fmt(bits)}")
        expect = (dis | rec) & ~(ov & 0xFF) & 0xFF
        flag = ""
        if (ct & 0xFF) == 0 and (st & 0xFF) != expect:
            flag = "   status != (disable|reconfig) & ~override"
        elif ((ov & 0xFF) and (ct & 0xFF) == 0 and (st & 0xFF) == expect
              and (rec & ov & 0xFF)):
            flag = "   reconfig dropped from status"
        if st & ~0xFF or ct & ~0xFF or ov & ~0xFF:
            flag += (f"   full st=0x{st:08x} ctrl=0x{ct:08x} ovr=0x{ov:08x}")
        print(_tpc_row(g, f"0x{dfc:02x}", f"0x{dis:02x}", f"0x{rec:02x}",
                       f"0x{st & 0xFF:02x}", f"0x{ct & 0xFF:02x}",
                       f"0x{ov & 0xFF:02x}", f"{fmt(bits)}{flag}"))
    print("    bad silicon: " + ("; ".join(bad_parts) if bad_parts else "none"))
    return ctrl, status


def sm_report(vals, fuse_tpc, stat_tpc):
    """Per-GPC SM census — how many SMs are active in each GPC.

    SMs = 2 per TPC (SM_PER_TPC); a GPC's live TPC count is 8 minus the
    popcount of its STATUS_OPT_TPC byte (TPC_SLOTS_PER_GPC). The STATUS
    array is the effective TPC-disable state — it includes the reconfig
    override writes, so a 0x01 -> 0x00 unlock reads as +2 SM here — which
    is what makes this the register-side witness for the SM count (CUDA
    confirmed 70 stock -> 74 after the GPC 4/5 reconfig unlock). GPCs that
    are fused off (OPT_GPC_DISABLE) or runtime-disabled (CTRL_OPT_GPC) or
    defective contribute 0 SM. A fuse-vs-status divergence per GPC flags
    a live reconfig override in place. Returns the card's total SM count.
    """
    gpc_dfc = set(bits_set(vals["OPT_GPC_DEFECTIVE"], 8))
    gpc_fus = set(bits_set(vals["OPT_GPC_DISABLE"], 8))
    gpc_ctl = set(bits_set(vals["CTRL_OPT_GPC"], 8))

    print("  SM census (2 SM/TPC, 8 TPC slots/GPC; STATUS = effective state)")
    print(f"    {'GPC':<5} {'state':<14} {'fuse TPC':<10} {'status TPC':<12} "
          f"{'TPC':<6} {'SM'}")
    n_gpc = n_tpc = n_sm = 0
    for g in range(8):
        if g in gpc_dfc:
            state = "defective"
        elif g in gpc_fus or g in gpc_ctl:
            state = ("disabled"
                     + (" (ctrl)" if g in gpc_ctl - gpc_fus else ""))
        else:
            state = "active"
        st = stat_tpc[g] & 0xFF
        tpc = TPC_SLOTS_PER_GPC - bin(st).count("1")
        if state == "active":
            n_gpc += 1
            n_tpc += tpc
            n_sm += tpc * SM_PER_TPC
        ovr = (f"   <- override (fuse 0x{fuse_tpc[g] & 0xFF:02x})"
               if fuse_tpc[g] != stat_tpc[g] else "")
        print(f"    {g:<5} {state:<14} 0x{fuse_tpc[g] & 0xFF:02x}     "
              f"0x{st:02x}       {tpc}/8    {tpc * SM_PER_TPC if state == 'active' else 0}{ovr}")
    print(f"    total: {n_gpc}/8 GPC active, {n_tpc} TPC, {n_sm} SM")
    return n_sm


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
                if title.startswith("CTRL"):
                    note = "(no override)" if vals[label] == 0 else "OVERRIDE PRESENT!"
                    print(f"    {label:<24} 0x{vals[label]:08x}  {note}")
                elif width == 1:
                    print(f"    {label:<24} 0x{vals[label]:08x}  "
                          f"bit0 {'set' if vals[label] & 1 else 'clear'}")
                else:
                    bits = bits_set(vals[label], width)
                    print(f"    {label:<24} 0x{vals[label]:08x}  "
                          f"bits {fmt(bits):<24} {width - len(bits)}/{width} active")
            if title.split()[0] in DELTA:
                dis_l, def_l, w = DELTA[title.split()[0]]
                not_def = sorted(set(bits_set(vals[dis_l], w)) -
                                 set(bits_set(vals[def_l], w)))
                print(f"    {'disabled not defective':<24} {'':4s} {fmt(not_def)}")

        for title, regs in RAW_GROUPS:
            print(f"  {title}")
            for label, off, note in regs:
                vals[label] = rd(i, off)
                suffix = f"  ({note})" if note else ""
                print(f"    {label:<24} 0x{vals[label]:08x}{suffix}")

        stack_report(vals, mib)

        ctrl_tpc, stat_tpc = tpc_report(lambda off: rd(i, off))
        n_sm = sm_report(vals, ctrl_tpc, stat_tpc)

        print("  status shadows (should equal fuses)")
        for status_off, sname in STATUS_NAMES.items():
            val = rd(i, status_off)
            for title, regs in GROUPS:
                for label, off, width, so in regs:
                    if so == status_off:
                        fuse_val = vals[label]
                        flag = "ok" if val == fuse_val else f"!= fuse 0x{fuse_val:08x}!"
                        print(f"    {sname:<24} 0x{val:08x}  [{flag}]")
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
            str(n_sm),
        ])

    headers = ["Card", "GPC healthy", "GPC disabled", "GPC defective",
               "FBP healthy", "FBP disabled", "FBP defective", "SM total"]
    table = [headers] + rows
    widths = [max(len(str(r[c])) for r in table) for c in range(len(headers))]
    print("Summary")
    for r in table:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())


main()
