// SPDX-License-Identifier: GPL-2.0-only
/* QEMU-only observation of native boot ACS policies; no production package. */
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/dmi.h>
static unsigned int expected_mask;
module_param(expected_mask, uint, 0400);
static bool restore_test;
module_param(restore_test, bool, 0400);
static int __init probe_init(void)
{
    struct pci_dev *d = NULL;
    if (!dmi_match(DMI_SYS_VENDOR, "QEMU")) return -EPERM;
    for_each_pci_dev(d) {
        int pos = pci_find_ext_capability(d, PCI_EXT_CAP_ID_ACS);
        u16 cap, ctrl, restored;
        if (!pos) continue;
        pci_read_config_word(d, pos + PCI_ACS_CAP, &cap);
        pci_read_config_word(d, pos + PCI_ACS_CTRL, &ctrl);
        pr_info("NATIVE-ACS bdf=%s cap=%04x ctrl=%04x\n", pci_name(d), cap, ctrl);
        if (restore_test && d->bus->number == 0 && d->devfn == PCI_DEVFN(3, 1)) {
            pci_save_state(d);
            pci_write_config_word(d, pos + PCI_ACS_CTRL, cap & 0x7f);
            pci_restore_state(d);
            pci_read_config_word(d, pos + PCI_ACS_CTRL, &restored);
            pr_info("NATIVE-ACS restore bdf=%s before=%04x restored=%04x\n", pci_name(d), ctrl, restored);
            if (restored & expected_mask) { pci_dev_put(d); return -EINVAL; }
        }
    }
    return 0;
}
static void __exit probe_exit(void) {}
module_init(probe_init);
module_exit(probe_exit);
MODULE_LICENSE("GPL");
