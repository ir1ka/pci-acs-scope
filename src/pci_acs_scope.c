// SPDX-License-Identifier: GPL-2.0-only
/* Explicit, one-shot ACS policy for selected root-port subtrees. */
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/pm_runtime.h>
#include <linux/slab.h>
#include <linux/mutex.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>

#define MAX_PORTS 32
#define ACS_ALL (PCI_ACS_SV | PCI_ACS_TB | PCI_ACS_RR | PCI_ACS_CR | \
		 PCI_ACS_UF | PCI_ACS_EC | PCI_ACS_DT)
#define ACS_REDIRECT (PCI_ACS_RR | PCI_ACS_CR | PCI_ACS_EC)

static bool execute;
module_param(execute, bool, 0444);
MODULE_PARM_DESC(execute, "Explicit execution gate; default false");
static char *ports[MAX_PORTS];
static unsigned int nports;
module_param_array(ports, charp, &nports, 0444);
MODULE_PARM_DESC(ports, "Required full domain:bus:slot.func root/upstream/downstream port list");
static bool include_ports;
module_param(include_ports, bool, 0444);
MODULE_PARM_DESC(include_ports, "Include all selected boundary ports; default false (descendants only)");
static char *include[MAX_PORTS];
static unsigned int ninclude;
module_param_array(include, charp, &ninclude, 0444);
MODULE_PARM_DESC(include, "Subset of selected full BDFs whose own ACS may change");
static bool root_include[MAX_PORTS];
static char *policy = "redirect";
module_param(policy, charp, 0444);
MODULE_PARM_DESC(policy, "redirect (default): clear RR/CR/EC like disable_acs_redir; off: clear standard ACS bits 0-6");
static unsigned int changed, remaining;
module_param(changed, uint, 0444);
module_param(remaining, uint, 0444);
static bool failed;
module_param(failed, bool, 0444);
static bool ready;
static bool snapshot_held;
module_param(snapshot_held, bool, 0444);
static bool wake;
module_param(wake, bool, 0444);
MODULE_PARM_DESC(wake, "Temporarily resume ACS devices using runtime-PM references; policy unchanged");
static bool require_idle;
module_param(require_idle, bool, 0444);
MODULE_PARM_DESC(require_idle, "Require unused endpoints when initially applying policy");
static struct proc_dir_entry *snapshot_entry;
static u16 mask;
static DEFINE_MUTEX(action_lock);
static struct pci_dev *roots[MAX_PORTS];

struct record {
	struct pci_dev *dev;
	u16 pos, cap, original, owned_partial;
    bool owned_partial_valid;
	bool dirty, locked, pinned, cfg_locked, gone, woke, managed;
};
static struct record *records;
static unsigned int count;

static bool boundary(struct pci_dev *dev)
{
    unsigned int i;
    for (i = 0; i < nports; i++)
        if (dev == roots[i]) return true;
    return false;
}

static bool included(struct pci_dev *dev)
{
    unsigned int i;
    if (include_ports) return true;
    for (i = 0; i < nports; i++)
        if (dev == roots[i] && root_include[i]) return true;
    return false;
}

static bool selected(struct pci_dev *dev)
{
	unsigned int i;
	for (; dev; dev = pci_upstream_bridge(dev))
		for (i = 0; i < nports; i++)
			if (dev == roots[i])
				return true;
	return false;
}

static int resolve_roots(void)
{
	unsigned int i, j, dom, bus, slot, fn;
	char tail;
	if (!nports)
		return -EINVAL;
	for (i = 0; i < nports; i++) {
		if (sscanf(ports[i], "%x:%x:%x.%x%c", &dom, &bus, &slot, &fn, &tail) != 4 ||
		    dom > 0xffff || bus > 255 || slot > 31 || fn > 7)
			return -EINVAL;
		roots[i] = pci_get_domain_bus_and_slot(dom, bus, PCI_DEVFN(slot, fn));
		if (!roots[i] || !pci_is_pcie(roots[i]) ||
		    (pci_pcie_type(roots[i]) != PCI_EXP_TYPE_ROOT_PORT &&
             pci_pcie_type(roots[i]) != PCI_EXP_TYPE_UPSTREAM &&
             pci_pcie_type(roots[i]) != PCI_EXP_TYPE_DOWNSTREAM))
			return -EINVAL;
		for (j = 0; j < i; j++)
			if (roots[j] == roots[i])
				return -EINVAL;
	}
    for (i = 0; i < ninclude; i++) {
        for (j = 0; j < nports; j++)
            if (!strcmp(include[i], pci_name(roots[j]))) break;
        if (j == nports) return -EINVAL;
        root_include[j] = true;
    }
	return 0;
}

static void release_records(void)
{
	unsigned int i;
	for (i = 0; i < count; i++)
		pci_dev_put(records[i].dev);
	kfree(records);
	records = NULL;
	count = 0;
	for (i = 0; i < nports; i++) {
		pci_dev_put(roots[i]);
		roots[i] = NULL;
	}
}

static void unlock_records(void)
{
	unsigned int i = count;
	while (i--) {
		struct record *r = &records[i];
		if (r->cfg_locked) {
			pci_cfg_access_unlock(r->dev);
			r->cfg_locked = false;
		}
		if (r->pinned) {
            if (r->woke) pm_runtime_put(&r->dev->dev);
            else pm_runtime_put_noidle(&r->dev->dev);
			r->pinned = false;
            r->woke = false;
		}
		if (r->locked) {
			device_unlock(&r->dev->dev);
			r->locked = false;
		}
	}
}

/* Resume before device/config locks, so runtime-PM callbacks never run while
 * these tool-owned locks are held. Refcounts are returned even on rejection. */
static int wake_records(void)
{
    unsigned int i;
    if (!wake) return 0;
    for (i = 0; i < count; i++) {
        struct record *r = &records[i];
        struct pci_dev *now;
        int ret;
        if (r->gone || !r->pos) continue;
        now = pci_get_domain_bus_and_slot(pci_domain_nr(r->dev->bus), r->dev->bus->number, r->dev->devfn);
        if (now != r->dev) { pci_dev_put(now); return -ENODEV; }
        pci_dev_put(now);
        ret = pm_runtime_resume_and_get(&r->dev->dev);
        if (ret < 0) return ret;
        r->pinned = true; r->woke = true;
    }
    return 0;
}

/* No driver removal, reset, BAR changes or global IOMMU changes. */
static int lock_records(bool strict)
{
	unsigned int i;
	for (i = 0; i < count; i++) {
		struct record *r = &records[i];
		struct pci_dev *d = r->dev;
		struct pci_dev *now;
		u16 command;
		if (r->gone)
			continue;
		now = pci_get_domain_bus_and_slot(pci_domain_nr(d->bus),
					       d->bus->number, d->devfn);
		if (now != d) {
			pci_err(d, "acs-scope: device removed/replaced\n");
			pci_dev_put(now);
			return -ENODEV;
		}
		pci_dev_put(now);
		if (!device_trylock(&d->dev)) {
			pci_err(d, "acs-scope: device lock busy\n");
			return -EBUSY;
		}
		r->locked = true;
        if (strict && ((!pci_is_bridge(d) && (d->driver || pci_is_enabled(d))) ||
            (pci_is_bridge(d) && d->driver && strcmp(d->driver->name, "pcieport")) ||
            d->is_virtfn || pci_num_vf(d) ||
            pci_read_config_word(d, PCI_COMMAND, &command) ||
            (!pci_is_bridge(d) && (command & PCI_COMMAND_MASTER)))) {
            pci_err(d, "acs-scope: strict idle check failed\n");
            return -EBUSY;
        }
		if (d->driver && (strstr(d->driver->name, "vfio") ||
				 strstr(d->driver->name, "uio"))) {
			pci_err(d, "acs-scope: refusing driver %s\n", d->driver->name);
			return -EBUSY;
		}
		/* Capability offsets are cached by PCI enumeration. No-ACS devices
		 * need driver exclusion, but must not be powered up for this tool. */
		if (!r->pos)
			continue;
        if (!r->pinned) {
		    pm_runtime_get_noresume(&d->dev);
		    r->pinned = true;
        }
		/* Reject transitioning/suspended devices rather than waking them. */
		if ((pm_runtime_enabled(&d->dev) && !pm_runtime_active(&d->dev)) ||
		    d->current_state != PCI_D0) {
			pci_err(d, "acs-scope: not active D0 (state=%d runtime=%d)\n",
				d->current_state, d->dev.power.runtime_status);
			return -EBUSY;
		}
		if (!pci_cfg_access_trylock(d)) {
			pci_err(d, "acs-scope: configuration access busy\n");
			return -EBUSY;
		}
		r->cfg_locked = true;
	}
	return 0;
}

static int collect(void)
{
	struct pci_dev *d = NULL;
	unsigned int capacity = 0;
	for_each_pci_dev(d)
		if (selected(d))
			capacity++;
	records = kcalloc(capacity, sizeof(*records), GFP_KERNEL);
	if (!records)
		return -ENOMEM;
	for_each_pci_dev(d)
		if (selected(d)) {
			records[count].dev = pci_dev_get(d);
			records[count].managed = included(d) || !boundary(d);
            records[count++].pos = d->acs_cap;
		}
	return 0;
}

/* Only restore our bits. A concurrent policy change is a conflict, not ours. */
/* Immediate rollback owns the locked transaction: accept only subsets of the
 * original bits (our clear may have partially succeeded). Later restore keeps
 * strict conflict detection and never overwrites another policy. */
static bool restore_conflict(u16 ctrl, u16 original, u16 bits, bool immediate)
{
    return immediate ? !!(ctrl & bits & ~original) :
        !!((ctrl & bits) && (ctrl & bits) != (original & bits));
}

static int restore_locked(bool immediate)
{
	unsigned int i = count;
	int ret = 0;
	remaining = 0;
	while (i--) {
		struct record *r = &records[i];
		u16 ctrl, check;
		if (!r->dirty || r->gone)
			continue;
		if (pci_find_ext_capability(r->dev, PCI_EXT_CAP_ID_ACS) != r->pos ||
		    pci_read_config_word(r->dev, r->pos + PCI_ACS_CTRL, &ctrl) ||
		    ctrl == 0xffff ||
		    (restore_conflict(ctrl, r->original, mask, immediate) &&
             !(r->owned_partial_valid && (ctrl & mask) == r->owned_partial)))
			goto failed;
		ctrl = (ctrl & ~mask) | (r->original & mask);
		if (pci_write_config_word(r->dev, r->pos + PCI_ACS_CTRL, ctrl) ||
		    pci_read_config_word(r->dev, r->pos + PCI_ACS_CTRL, &check) ||
		    check != ctrl)
			goto failed;
		r->dirty = false;
        r->owned_partial_valid = false;
		pci_info(r->dev, "acs-scope: restored ACSCtl=%#06x\n", ctrl);
		continue;
failed:
        /* A failed immediate rollback can leave a known partial clear. Retain
         * that exact masked value so an explicit retry can recover it, without
         * treating arbitrary later partial changes as ours. */
        if (immediate &&
            !pci_read_config_word(r->dev, r->pos + PCI_ACS_CTRL, &check) &&
            check != 0xffff && !(check & mask & ~r->original)) {
            r->owned_partial = check & mask;
            r->owned_partial_valid = true;
        }
		pci_err(r->dev, "acs-scope: restore failed/conflict; original=%#06x mask=%#06x\n",
			r->original, mask);
		remaining++;
		ret = -EIO;
	}
	return ret;
}

static int restore_action(bool refresh)
{
	int ret;
	mutex_lock(&action_lock);
	pci_lock_rescan_remove();
    if (refresh) {
        unsigned int i;
        for (i = 0; i < count; i++) {
            struct record *r = &records[i];
            struct pci_dev *now = pci_get_domain_bus_and_slot(pci_domain_nr(r->dev->bus), r->dev->bus->number, r->dev->devfn);
            if (now != r->dev) {
                r->gone = true;
                r->dirty = false;
                pci_info(r->dev, "acs-scope: removed/replaced original device; no writes to replacement\n");
            }
            pci_dev_put(now);
        }
    }
	ret = wake_records();
    if (!ret) ret = lock_records(refresh);
	if (!ret)
		ret = restore_locked(false);
	unlock_records();
	pci_unlock_rescan_remove();
    if (!remaining && snapshot_held) {
        snapshot_held = false;
        module_put(THIS_MODULE);
    }
	mutex_unlock(&action_lock);
	return ret;
}

static int set_restore(const char *val, const struct kernel_param *kp)
{
	bool requested;
	if (!ready || kstrtobool(val, &requested) || !requested)
		return -EINVAL;
	return restore_action(false);
}
static const struct kernel_param_ops restore_ops = { .set = set_restore };
module_param_cb(restore, &restore_ops, NULL, 0200);
MODULE_PARM_DESC(restore, "Write 1 to restore original bits; errors keep module loaded");

static int set_refresh_restore(const char *val, const struct kernel_param *kp)
{
    bool requested;
    if (!ready || kstrtobool(val, &requested) || !requested)
        return -EINVAL;
    return restore_action(true);
}
static const struct kernel_param_ops refresh_ops = {.set = set_refresh_restore};
module_param_cb(refresh_restore, &refresh_ops, NULL, 0200);
MODULE_PARM_DESC(refresh_restore, "Explicit idle restore for reapply; skip removed originals, never write replacements");

/* Authoritative original values, captured while device/config locks were held.
 * seq_file supports large scopes without truncating a page-sized parameter. */
static int snapshot_show(struct seq_file *s, void *unused)
{
    unsigned int i, emitted = 0;
    mutex_lock(&action_lock);
    if (!ready) { mutex_unlock(&action_lock); return -EAGAIN; }
    seq_printf(s, "{\"schema\":1,\"module_version\":\"0.5.0\",\"policy\":\"%s\",\"mask\":%u,\"include_ports\":%s,\"included_ports\":[", policy, mask, include_ports ? "true" : "false");
    for (i = 0; i < nports; i++)
        if (included(roots[i])) seq_printf(s, "%s\"%s\"", emitted++ ? "," : "", pci_name(roots[i]));
    seq_puts(s, "],\"ports\":[");
    for (i = 0; i < nports; i++)
        seq_printf(s, "%s\"%s\"", i ? "," : "", pci_name(roots[i]));
    seq_puts(s, "],\"devices\":[");
    for (i = 0; i < count; i++) {
        struct record *r = &records[i];
        struct pci_dev *now = pci_get_domain_bus_and_slot(pci_domain_nr(r->dev->bus), r->dev->bus->number, r->dev->devfn);
        bool same = now == r->dev;
        pci_dev_put(now);
        seq_printf(s, "%s{\"bdf\":\"%s\",\"vendor_device\":\"%04x%04x\",\"offset\":%u,\"cap\":%u,\"original\":%u,\"gone\":%s,\"same_device\":%s,\"managed\":%s,\"dirty\":%s,\"owned_partial_valid\":%s,\"owned_partial\":%u}",
                   i ? "," : "", pci_name(r->dev), r->dev->device, r->dev->vendor,
                   r->pos, r->cap, r->original, r->gone ? "true" : "false", same ? "true" : "false", r->managed ? "true" : "false", r->dirty ? "true" : "false", r->owned_partial_valid ? "true" : "false", r->owned_partial);
    }
    seq_puts(s, "]}\n");
    mutex_unlock(&action_lock);
    return 0;
}

static int __init scope_init(void)
{
	unsigned int i;
	int ret;
	if (!execute) {
		ready = true;
		pr_info("acs-scope: inert load (execute=false)\n");
		return 0;
	}
	if (!strcmp(policy, "off"))
		mask = ACS_ALL;
	else if (!strcmp(policy, "redirect"))
		mask = ACS_REDIRECT;
	else
		return -EINVAL;
	pci_lock_rescan_remove();
	ret = resolve_roots();
	if (ret)
		goto out;
	ret = collect();
	if (ret)
		goto out;
	ret = wake_records();
    if (!ret) ret = lock_records(require_idle);
	if (ret)
		goto out;
	/* Snapshot the whole scope before the first write. No-ACS is explicit. */
	for (i = 0; i < count; i++) {
		struct record *r = &records[i];
		if (!r->pos) {
			pci_info(r->dev, "acs-scope: no standard ACS control; unchanged\n");
			continue;
		}
		if (pci_read_config_word(r->dev, r->pos + PCI_ACS_CAP, &r->cap) ||
		    pci_read_config_word(r->dev, r->pos + PCI_ACS_CTRL, &r->original) ||
		    r->cap == 0xffff || r->original == 0xffff) {
			ret = -EIO;
			goto out;
		}
	}
    snapshot_entry = proc_create_single("pci_acs_scope_snapshot", 0400, NULL, snapshot_show);
    if (!snapshot_entry) { ret = -ENOMEM; goto out; }
	for (i = 0; i < count; i++) {
		struct record *r = &records[i];
		u16 next, check;
		if (!r->managed || !r->pos || !(r->original & mask))
			continue;
		next = r->original & ~mask;
		r->dirty = true; /* A reported write failure might still have changed HW. */
		if (pci_write_config_word(r->dev, r->pos + PCI_ACS_CTRL, next) ||
		    pci_read_config_word(r->dev, r->pos + PCI_ACS_CTRL, &check) ||
		    check != next) {
			ret = -EIO;
			/* Leave module loaded if rollback fails, preserving retry data. */
			if (restore_locked(true)) {
				failed = true;
				ready = true;
				pr_err("acs-scope: PARTIAL FAILURE; remaining=%u; restore required\n", remaining);
				ret = 0;
			}
			goto out;
		}
		changed++;
		remaining++;
		pci_info(r->dev, "acs-scope: ACSCtl %#06x -> %#06x mask=%#06x\n",
			 r->original, next, mask);
	}
	ready = true;
	pr_info("acs-scope: %u functions covered, %u controls changed; one-shot only\n",
		count, changed);
out:
    /* Normal rmmod must not destroy dirty snapshots. Explicit restore drops
     * this reference only after every managed record is clean/gone. */
    if (!ret && remaining && !snapshot_held) {
        __module_get(THIS_MODULE);
        snapshot_held = true;
    }
	unlock_records();
	pci_unlock_rescan_remove();
	if (ret) {
        proc_remove(snapshot_entry);
        snapshot_entry = NULL;
		release_records();
    }
	return ret;
}

static void __exit scope_exit(void)
{
    proc_remove(snapshot_entry);
    WARN_ON(remaining); /* Force-unload bypasses module lifetime guarantees. */
	release_records();
}

module_init(scope_init);
module_exit(scope_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Opt-in standard ACS control for explicit root-port subtrees");
MODULE_VERSION("0.5.0");
