#!/bin/bash
# All paths below belong to a disposable container; no host /etc or /boot mounts.
set -euo pipefail
: "${TARGET_KERNEL:=7.0.0-30-generic}"
dpkg -i /opt/kernel-debs/*.deb > /out/kernel-install.log 2>&1
dpkg -i /old/*.deb > /out/acs-old-install.log 2>&1
[ ! -e /etc/pci-acs-scope/boot.json ]
pci-acs-boot enable --port 0000:00:03.1 > /out/acs-enable.log 2>&1
dpkg -i /new/*prebuilt*.deb > /out/acs-upgrade.log 2>&1
python3 - <<'PY'
import json
v=json.load(open('/etc/pci-acs-scope/boot.json'))
assert v['ports']==['0000:00:03.1']and v['policy']=='redirect'and v['included_ports']==[]
assert all(k['backend']=='module'for k in v['kernel_backends'])
PY
dpkg --remove pci-acs-scope-prebuilt > /out/acs-remove.log 2>&1
[ ! -e /etc/pci-acs-scope/boot.json ]
[ ! -e /etc/default/grub.d/60-pci-acs-scope.cfg ]
lsinitramfs "/boot/initrd.img-$TARGET_KERNEL" > /out/acs-removed-image.txt
! grep -q 'pci-acs-scope/run-early' /out/acs-removed-image.txt
dpkg --purge pci-acs-scope-prebuilt > /out/acs-purge.log 2>&1
printf '%s\n' 'PASS: ACS prebuilt upgrade with opt-in and remove/purge cleanup; real target kernel packages' > /out/result.txt
