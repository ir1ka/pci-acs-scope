#!/bin/bash
# Disposable container only, with matching target kernel headers already present.
set -euo pipefail
: "${TARGET_KERNEL:=7.0.0-30-generic}"
mkdir -p /out/validation
update-initramfs -c -k "$TARGET_KERNEL" > /out/validation/initial-initramfs.log 2>&1
# Install and compile custom implementation through real generated dh_dkms scripts.
dpkg -i /out/pci-acs-scope-dkms_*.deb > /out/validation/module-install.log 2>&1
[ "$(cat /usr/share/pci-acs-scope/backend)" = module ]
[ ! -e /etc/pci-acs-scope/boot.json ]
[ "$(pci-acs-boot kernel-backend --kernel "$TARGET_KERNEL")" = module ]
dkms status > /out/validation/module-dkms.txt
pci-acs-boot enable --port 0000:00:03.1 > /out/validation/module-enable.log 2>&1
[ ! -e /etc/default/grub.d/60-pci-acs-scope.cfg ]
dpkg -i /out/pci-acs-scope-dkms_*.deb > /out/validation/module-reinstall.log 2>&1
[ -e /etc/pci-acs-scope/boot.json ]
# Explicitly choosing the native package replaces the conflicting provider.
dpkg -i /out/pci-acs-scope-native_*.deb > /out/validation/native-install.log 2>&1
[ "$(dpkg-query -W -f='${db:Status-Status}' pci-acs-scope-dkms 2>/dev/null || :)" != installed ]
[ "$(cat /usr/share/pci-acs-scope/backend)" = native ]
[ ! -e /etc/pci-acs-scope/boot.json ]
[ ! -e /usr/share/initramfs-tools/hooks/pci-acs-scope ]
[ ! -e /usr/src/pci-acs-scope-0.5.0 ]
! dkms status | grep -q '^pci-acs-scope/'
# Native policy config uses a synthetic inventory; no host PCI writes.
mkdir -p /etc/default/grub.d
printf 'GRUB_CMDLINE_LINUX="existing=1"\n' > /etc/default/grub
cat > /usr/sbin/update-grub <<'SH'
#!/bin/sh
set -eu
. /etc/default/grub
for file in /etc/default/grub.d/*.cfg;do [ ! -f "$file" ] || . "$file";done
printf '%s\n' "$GRUB_CMDLINE_LINUX" > /out/validation/native-arguments.txt
SH
chmod 755 /usr/sbin/update-grub
python3 - <<'PY'
import importlib.machinery,importlib.util
p='/usr/sbin/pci-acs-boot';l=importlib.machinery.SourceFileLoader('boot',p);s=importlib.util.spec_from_loader(l.name,l);b=importlib.util.module_from_spec(s);l.exec_module(b)
a=b.app();a.native_plan=lambda *args:{'devices':['0000:02:01.0','0000:02:02.0'],'selectors':['0000:00:03.1/00.0/01.0','0000:00:03.1/00.0/02.0']}
b.app=lambda:a
b.enable(['0000:00:03.1'],'redirect')
# A native refresh must never invoke initramfs-tools.
real_run=b.subprocess.run
def guarded(argv,**kw):
 if argv[0]=='update-initramfs':raise AssertionError(argv)
 return real_run(argv,**kw)
b.subprocess.run=guarded
b.refresh()
PY
grep -q "'pci=disable_acs_redir=.*;" /out/validation/native-arguments.txt
# Preserve enabled state on upgrade without accessing physical topology.
# Use the same generated synthetic selector data during refresh.
# Preinst itself always checks the real builder kernel image.
python3 - <<'PY'
import json
p='/etc/pci-acs-scope/boot.json';c=json.load(open(p));assert c['included_ports']==[]and c['ports']==['0000:00:03.1']
PY
dpkg --purge pci-acs-scope-native > /out/validation/native-purge.log 2>&1
[ ! -e /etc/pci-acs-scope/boot.json ]
[ ! -e /etc/default/grub.d/60-pci-acs-scope.cfg ]
# Real preinst refusal before files are unpacked for an unsupported boot image.
printf 'not a supported kernel image' > /boot/vmlinuz-unsupported-test
if dpkg --unpack /out/pci-acs-scope-native_*.deb > /out/validation/native-reject.log 2>&1;then exit 1;fi
[ ! -e /usr/share/pci-acs-scope/backend ]
printf '%s\n' 'PASS: two exclusive packages, real DKMS, default off, scoped native config, native preinst rejection and clean removal; GRUB execution shell-stubbed, inventory synthetic' > /out/validation/package-result.txt
