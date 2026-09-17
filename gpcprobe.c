// SPDX-License-Identifier: GPL-2.0
/*
 * gpcprobe - read-only NVIDIA GPU BAR0 register probe.
 *
 * The proprietary nvidia driver owns the GPU BARs exclusively and zeroes the
 * kernel resource entries, so /sys/.../resource0 (EINVAL) and /dev/mem
 * (STRICT_DEVMEM) cannot reach the register window. This module reads the raw
 * BAR0 config register, ioremaps it, and exposes 32-bit reads via
 * /dev/gpcprobe (ioctl). Read-only: no writes to BAR0.
 */
#include <linux/module.h>
#include <linux/init.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/pci.h>
#include <linux/pci_regs.h>
#include <linux/uaccess.h>
#include <linux/string.h>

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Read-only NVIDIA BAR0 register probe");

#define GP_IOC_MAGIC 'G'
struct gp_reg {
	__u32 idx;	/* card index */
	__u32 off;	/* register offset within BAR0 */
	__u32 val;	/* result (out) */
};
struct gp_info {
	__u32 count;
	__u32 bdf[16];	/* (bus << 8) | devfn, 0 = unused */
};
#define GP_IOC_INFO   _IO(GP_IOC_MAGIC, 1)
#define GP_IOC_READ32 _IOWR(GP_IOC_MAGIC, 2, struct gp_reg)

#define BAR_ADDR_MASK	0xfffffff0u
#define BAR_MEM_TYPE_IO	0x01u	/* bit 0: 1 = I/O space */
#define BAR_MEM_TYPE_64	0x02u	/* bit 1: 64-bit BAR */

struct gp_card {
	struct pci_dev *pdev;
	volatile void __iomem *map;
	resource_size_t size;
};

static struct gp_card cards[16];
static int ncards;

static long gp_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	struct gp_reg r;
	struct gp_info info;
	int i;

	switch (cmd) {
	case GP_IOC_INFO:
		memset(&info, 0, sizeof(info));
		info.count = ncards;
		for (i = 0; i < ncards; i++)
			info.bdf[i] = ((u32)cards[i].pdev->bus->number << 8) |
				       cards[i].pdev->devfn;
		return copy_to_user((void __user *)arg, &info, sizeof(info)) ? -EFAULT : 0;
	case GP_IOC_READ32:
		if (copy_from_user(&r, (void __user *)arg, sizeof(r)))
			return -EFAULT;
		if (r.idx >= ncards)
			return -EINVAL;
		if (r.off + 4 > cards[r.idx].size)
			return -ERANGE;
		r.val = readl(cards[r.idx].map + r.off);
		if (copy_to_user((void __user *)arg, &r, sizeof(r)))
			return -EFAULT;
		return 0;
	}
	return -ENOTTY;
}

static int gp_open(struct inode *inode, struct file *file)
{
	return 0;
}

static const struct file_operations gp_fops = {
	.owner = THIS_MODULE,
	.open = gp_open,
	.unlocked_ioctl = gp_ioctl,
};

static struct miscdevice gp_dev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "gpcprobe",
	.fops = &gp_fops,
};

static int __init gp_init(void)
{
	struct pci_dev *pdev;
	u32 lo, hi;
	u64 start, len;
	int seen = 0;

	for_each_pci_dev(pdev) {
		seen++;
		if (pdev->vendor == 0x10de || seen <= 12)
			pr_info("gpcprobe: dev %s ven=%04x cls=%06x\n",
				pci_name(pdev), pdev->vendor, pdev->class);
		if (pdev->vendor != 0x10de)
			continue;
		if ((pdev->class >> 16) != 0x03)
			continue;
		if (ncards >= 16)
			break;
		if (pci_read_config_dword(pdev, PCI_BASE_ADDRESS_0, &lo)) {
			pr_err("gpcprobe: %s config read failed\n", pci_name(pdev));
			continue;
		}
		if (lo & BAR_MEM_TYPE_IO) {
			pr_err("gpcprobe: %s BAR0 is I/O space\n", pci_name(pdev));
			continue;
		}
		start = lo & BAR_ADDR_MASK;
		if (lo & BAR_MEM_TYPE_64) {
			if (pci_read_config_dword(pdev, PCI_BASE_ADDRESS_0 + 4, &hi)) {
				pr_err("gpcprobe: %s config read hi failed\n",
					pci_name(pdev));
				continue;
			}
			start |= (u64)hi << 32;
		}
		len = 0x1000000;	/* 16 MiB register window */
		cards[ncards].map = ioremap(start, len);
		if (!cards[ncards].map) {
			pr_err("gpcprobe: %s ioremap 0x%llx failed\n",
				pci_name(pdev), start);
			continue;
		}
		cards[ncards].pdev = pci_dev_get(pdev);
		cards[ncards].size = len;
		ncards++;
		pr_info("gpcprobe: %s BAR0 at 0x%llx (%llu bytes)\n",
			pci_name(pdev), start, len);
	}
	if (!ncards) {
		pr_err("gpcprobe: no NVIDIA GPU BAR0 found (scanned %d devs)\n",
			seen);
		return -ENODEV;
	}
	if (misc_register(&gp_dev))
		goto err;
	return 0;
err:
	while (ncards--) {
		iounmap(cards[ncards].map);
		pci_dev_put(cards[ncards].pdev);
	}
	return -ENODEV;
}

static void __exit gp_exit(void)
{
	misc_deregister(&gp_dev);
	while (ncards--) {
		iounmap(cards[ncards].map);
		pci_dev_put(cards[ncards].pdev);
	}
}

module_init(gp_init);
module_exit(gp_exit);
