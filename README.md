# PCI ACS Scope

**English** | [Chinese](README.zh-CN.md)

**Distribution validation:** Tested only on Debian 13. Other distributions, including Ubuntu, have not been validated.

Manage ACS policy within selected PCIe port subtrees. Root, upstream and downstream ports are supported. By default, the selected boundary port itself is excluded; only its descendants are affected.

## Quick start

The initial public release ships the DKMS package only. Source also supports a mutually exclusive native package. Neither package enables a policy automatically.

| Package | Implementation | Boot integration |
| --- | --- | --- |
| `pci-acs-scope-dkms` | Project kernel module, rebuilt by DKMS | Explicit opt-in before udev in initramfs |
| `pci-acs-scope-native` (build locally) | Kernel `disable_acs_redir` / `config_acs` parameters | Separate GRUB configuration; no project module |

```sh
sudo apt install ./pci-acs-scope-dkms_0.5.0-6_all.deb
# Apply on subsequent boot, without immediately changing devices:
sudo pci-acs-boot enable --port 0000:00:03.1
# Disable future boot configuration:
sudo pci-acs-boot disable
```

Use your actual port address. The source also retains an explicit-target precompiled build interface; precompiled modules are not part of the initial release.

## Scope and policy

`--port A --include --port B --port C --include` includes ports A and C themselves but excludes B itself. Overlapping boundaries retain their individual include choice. A compatibility `--ports` interface accepts comma/space-separated lists.

Default `--policy redirect` follows the RR/CR/EC controls used by the kernel's `disable_acs_redir`. Explicit `--policy off` clears standard ACS bits 0–6; the native backend requires `config_acs` for this policy. Functions lacking the relevant ACS capability are omitted. Functions whose controls are already disabled are still included when supported, because boot initialization may re-enable them.

Native device paths start with the root BDF followed by device.function hops, for example `0000:00:03.1/00.0/08.0/00.0/00.0`. This tolerates downstream bus renumbering but is an exact path, not a subtree wildcard. After adding a bridge or changing physical connections, run:

```sh
sudo pci-acs-boot refresh
```

The generated `/etc/default/grub.d/60-pci-acs-scope.cfg` quotes the complete parameter so semicolons survive into grub.cfg instead of becoming GRUB command separators.

## Installation and upgrades

The native package's self-contained preinst detector examines installed kernel ELF images under `/boot` for `disable_acs_redir`, `config_acs` and path-selector support. Every target kernel must provide an accepted redirect implementation. Unsupported installation is rejected rather than silently switching to the module backend. Python and required decompression tools are covered by Pre-Depends. Support is checked from kernel evidence, not inferred solely from a version number.

Enabling native policy checks capabilities again and requires manageable GRUB configuration. To change backend, first remove the old explicit boot configuration, then install and explicitly enable the new backend. Normal upgrades within one backend preserve opt-in and refresh its own configuration. The module backend supports initramfs-tools; native adds no initramfs hook. Kernel postinst/postrm hooks only refresh this project's configuration.

## One-time module policy and restore

```sh
sudo pci-acs-scope disable --port 0000:00:03.1 --save /var/tmp/acs-before-new.json
sudo pci-acs-scope restore
```

Only the module backend supports immediate disable/restore/reapply. Original values are saved before policy changes; failures trigger rollback. Pending restore records hold a module reference, preventing normal rmmod until restoration. Failed restores retain the kernel snapshot. Restoration targets the original pci_dev, never a replacement device. Explicit refresh handles removed/replaced-device records. Forced unloading bypasses normal reference protection.

Both backends provide read-only `status`, `audit` and `native-plan`; `pci-acs-boot audit` checks enabled boot policy. Optional `audit-enable`/`audit-disable` controls periodic auditing, which reports drift without writing PCI configuration. Audit again after reset, suspend/resume or hotplug. Runtime reapply requires an idle subtree.

## Build and limitations

```sh
make check
make deb                     # builds both mutually exclusive packages
```

Dependencies: Python 3, Git, make, a compiler, debhelper and dh-sequence-dkms; module builds additionally require matching kernel headers. Builds do not install or load host modules, and do not require rebuilding the kernel. Secure Boot uses the target system's trusted DKMS key.

Native parameters take effect during PCI initialization. Loading a module later does not reconstruct existing IOMMU groups. Disabling ACS does not prove a P2P traffic route or force CUDA P2P support. It changes access isolation and should not be used between untrusted devices/tenants; the device driver still needs a compatible P2P implementation.

## Removal

```sh
sudo pci-acs-boot disable
# If a temporary module policy was applied, restore it first:
sudo pci-acs-scope restore
sudo apt remove pci-acs-scope-dkms
```

Native users remove `pci-acs-scope-native` and do not run runtime restore. Removing native boot parameters takes effect on a subsequent normal boot; the tool does not reboot automatically.

## Contact and license

Maintained by IriKa Qiu ([ir1ka](https://github.com/ir1ka)). Use [Issues](https://github.com/ir1ka/pci-acs-scope/issues) and [Pull Requests](https://github.com/ir1ka/pci-acs-scope/pulls). For security issues, request a private channel without posting vulnerability details or keys publicly.

GPL v2; preserve all third-party license and attribution notices.
