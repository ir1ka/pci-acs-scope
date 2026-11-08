// SPDX-License-Identifier: GPL-2.0-only
/* Guest-only ACS setup/verification. Never package as a host module. */
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/dmi.h>
#include <linux/slab.h>

struct entry { struct pci_dev *d; int pos; u16 seeded, original; };
static struct entry *entries;
static unsigned int count;
static char *native_kind;
module_param(native_kind, charp, 0400);
static bool block;
static bool regular;
module_param(regular, bool, 0644);
static u16 replacement_ctrl;
static char *check;
static unsigned int seed_count;
module_param(seed_count, uint, 0444);

static const struct pci_device_id ids[] = { { PCI_DEVICE(0x1b36, 0x0005) }, { } };
static int fixture_probe(struct pci_dev *d, const struct pci_device_id *id) { return 0; }
static struct pci_driver blocker = { .name = "vfio-acs-test", .id_table = ids, .probe = fixture_probe };

static int set_block(const char *s, const struct kernel_param *kp)
{
	bool b;
	int rc;
	if (!entries || kstrtobool(s, &b))
		return -EINVAL;
	if (b == block)
		return 0;
	if (b) {
        blocker.name = regular ? "acs-active-test" : "vfio-acs-test";
		rc = pci_register_driver(&blocker);
		if (rc)
			return rc;
	} else {
		pci_unregister_driver(&blocker);
	}
	block = b;
	return 0;
}
static const struct kernel_param_ops block_ops = { .set = set_block };
module_param_cb(block, &block_ops, NULL, 0200);

static int set_check(const char *s, const struct kernel_param *kp)
{
	unsigned int i;
	u16 mask;
	bool both = sysfs_streq(s, "both");
	if (!entries)
		return -EINVAL;
	if (sysfs_streq(s, "off") || both)
		mask = 0x7f;
	else if (sysfs_streq(s, "redirect"))
		mask = 0x2c;
	else if (sysfs_streq(s, "original"))
		mask = 0;
	else
		return -EINVAL;
	for (i = 0; i < count; i++) {
		struct entry *e = &entries[i];
		struct pci_dev *root = e->d;
		u16 val, expected;
		while (pci_upstream_bridge(root))
			root = pci_upstream_bridge(root);
		expected = e->seeded;
		if (root->bus->number == 0 && (root->devfn == PCI_DEVFN(3, 1) ||
		    (both && root->devfn == PCI_DEVFN(4, 0))))
			expected &= ~mask;
		if (pci_read_config_word(e->d, e->pos + PCI_ACS_CTRL, &val) || val != expected) {
			pci_err(e->d, "ACS-TEST expected=%#x actual=%#x\n", expected, val);
			return -EIO;
		}
	}
	pr_info("ACS-TEST check %s passed (%u controls)\n", s, count);
	return 0;
}
static const struct kernel_param_ops check_ops = { .set = set_check };
module_param_cb(check, &check_ops, &check, 0200);

static int replacement_action(const char *s, const struct kernel_param *kp)
{
    struct pci_dev *d = pci_get_domain_bus_and_slot(0, 0, PCI_DEVFN(3, 1));
    u16 ctrl;
    int pos, ret = 0;
    if (!d) return -ENODEV;
    pos = pci_find_ext_capability(d, PCI_EXT_CAP_ID_ACS);
    if (!pos || pci_read_config_word(d, pos + PCI_ACS_CTRL, &ctrl)) ret = -EIO;
    else if (sysfs_streq(s, "capture")) replacement_ctrl = ctrl;
    else if (!sysfs_streq(s, "check") || replacement_ctrl != ctrl) ret = -EINVAL;
    pci_dev_put(d);
    return ret;
}
static const struct kernel_param_ops replacement_ops = {.set = replacement_action};
module_param_cb(replacement, &replacement_ops, NULL, 0200);

static bool inject_partial, block_restore;
static unsigned int partial_writes;
module_param(inject_partial, bool, 0600);
module_param(block_restore, bool, 0600);
module_param(partial_writes, uint, 0400);
static struct pci_bus *hook_bus;
static struct pci_ops *old_ops, hook_ops;
static int fault_write(struct pci_bus *bus, unsigned int fn, int where, int size, u32 val)
{
    unsigned int i;
    if (inject_partial && fn == PCI_DEVFN(3, 1))
        pr_info("ACS-TEST fault access where=%#x size=%d value=%#x\n", where, size, val);
    for (i = 0; i < count; i++) {
        struct entry *e = &entries[i];
        if (e->d->bus != bus || e->d->devfn != fn ||
            where != e->pos + PCI_ACS_CTRL || size != 2) continue;
        if (inject_partial && !(val & 0x2c) && (e->seeded & 0x2c)) {
            partial_writes++;
            return old_ops->write(bus, fn, where, size, e->seeded & ~PCI_ACS_RR);
        }
        if (block_restore && (val & 0x2c)) return PCIBIOS_SET_FAILED;
    }
    return old_ops->write(bus, fn, where, size, val);
}

static int external_policy(const char *s, const struct kernel_param *kp)
{
    unsigned int i;
    bool partial = sysfs_streq(s, "partial");
    if (!partial && !sysfs_streq(s, "clear")) return -EINVAL;
    for (i = 0; i < count; i++) {
        struct entry *e = &entries[i];
        if (e->d->bus == hook_bus && e->d->devfn == PCI_DEVFN(3, 1))
            return old_ops->write(hook_bus, e->d->devfn, e->pos + PCI_ACS_CTRL, 2,
                partial ? e->seeded & ~PCI_ACS_RR : e->seeded & ~0x2c);
    }
    return -ENODEV;
}
static const struct kernel_param_ops external_ops = {.set = external_policy};
module_param_cb(external_policy, &external_ops, NULL, 0200);

static int __init fixture_init(void)
{
	struct pci_dev *d = NULL;
	unsigned int capacity = 0;
	if (!dmi_match(DMI_SYS_VENDOR, "QEMU") || !dmi_match(DMI_PRODUCT_NAME, "ACS-Scope-Test"))
		return -EPERM;
	for_each_pci_dev(d)
		capacity++;
	entries = kcalloc(capacity, sizeof(*entries), GFP_KERNEL);
	if (!entries)
		return -ENOMEM;
	for_each_pci_dev(d) {
		int pos = pci_find_ext_capability(d, PCI_EXT_CAP_ID_ACS);
		u16 cap;
		struct entry *e;
		if (!pos)
			continue;
		e = &entries[count++];
		e->d = pci_dev_get(d);
		e->pos = pos;
		pci_read_config_word(d, pos + PCI_ACS_CAP, &cap);
		pci_read_config_word(d, pos + PCI_ACS_CTRL, &e->original);
        if (native_kind) {
            struct pci_dev *ancestor = d;
            int depth = 0;
            while (pci_upstream_bridge(ancestor)) {
                ancestor = pci_upstream_bridge(ancestor);depth++;
            }
            pci_info(d, "ACS-TEST native=%s depth=%d original=%#x cap=%#x\n",
                     native_kind, depth, e->original, cap);
            /* Only the nested emulated ACS port is selected. Boundaries and
             * another root remain at the kernel's default isolation policy. */
            if (cap & (PCI_ACS_RR | PCI_ACS_CR)) {
                u16 expected = PCI_ACS_RR | PCI_ACS_CR;
                if (depth == 3 && ancestor->devfn == PCI_DEVFN(3, 1) &&
                    strcmp(native_kind, "baseline")) expected = 0;
                if ((e->original & (PCI_ACS_RR | PCI_ACS_CR)) != expected)
                    return -EINVAL;
            }
        }
		pci_write_config_word(d, pos + PCI_ACS_CTRL, cap & 0x7f);
		pci_read_config_word(d, pos + PCI_ACS_CTRL, &e->seeded);
		pci_info(d, "ACS-TEST seeded=%#x\n", e->seeded);
	}
    if (count) {
        hook_bus = entries[0].d->bus;old_ops = hook_bus->ops;
        hook_ops = *old_ops;hook_ops.write = fault_write;hook_bus->ops = &hook_ops;
    }
	seed_count = count;
	return 0;
}
static void __exit fixture_exit(void)
{
	unsigned int i;
    if (hook_bus) hook_bus->ops = old_ops;
	if (block)
		pci_unregister_driver(&blocker);
	for (i = 0; i < count; i++) {
		pci_write_config_word(entries[i].d, entries[i].pos + PCI_ACS_CTRL, entries[i].original);
		pci_dev_put(entries[i].d);
	}
	kfree(entries);
}
module_init(fixture_init);
module_exit(fixture_exit);
MODULE_LICENSE("GPL");
