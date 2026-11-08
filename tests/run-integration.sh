#!/bin/bash
# Only independent guest modules are loaded. No host installation/PCI changes.
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")/.." && pwd)
kernel=${ACS_TEST_KERNEL_VERSION:-$(uname -r)}
work=${ACS_TEST_DIR:-"$base/build/guest-$kernel"}
mkdir -p "$work/module" "$work/fixture" "$work/root"/{bin,dev,proc,sys,modules}
if [ -n "${ACS_TEST_MODULE:-}" ]; then
    cp "$ACS_TEST_MODULE" "$work/root/modules/pci_acs_scope.ko"
else
    cp "$base/src/pci_acs_scope.c" "$base/src/Makefile" "$work/module/"
    make -C "$work/module" KERNEL_RELEASE="$kernel"
    cp "$work/module/pci_acs_scope.ko" "$work/root/modules/"
fi
cp "$base/tests/acs_fixture.c" "$work/fixture/"
printf 'obj-m += acs_fixture.o\n' > "$work/fixture/Makefile"
make -C "/lib/modules/$kernel/build" M="$work/fixture" modules
cp "$work/fixture/acs_fixture.ko" "$work/root/modules/"
file /bin/busybox | grep -q 'statically linked' || { echo 'Need busybox-static' >&2; exit 1; }
cp /bin/busybox "$work/root/bin/"
ln -sf busybox "$work/root/bin/sh"
cp "$base/tests/guest-init" "$work/root/init"
chmod 755 "$work/root/init"
(cd "$work/root" && find . -print0 | cpio --null -o -H newc --quiet | gzip -1) > "$work/initramfs.gz"
sudo -n python3 "$base/tests/run-vm.py" "$work" "/boot/vmlinuz-$kernel"
