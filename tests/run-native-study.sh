#!/bin/bash
# Disposable build container only. Guest has no disks or PCI passthrough.
set -euo pipefail
: "${NATIVE_KERNEL:=6.12.113+deb13-amd64}"
: "${NATIVE_KDIR:=/usr/src/linux-headers-$NATIVE_KERNEL}"
work=/out/native-vm
mkdir -p "$work/fixture" "$work/root"/{bin,dev,proc,sys,modules}
cp /source/acs/tests/native_acs_probe.c "$work/fixture/"
printf 'obj-m += native_acs_probe.o\n' > "$work/fixture/Makefile"
if [ -z "${ACS_NATIVE_MODULE:-}" ]; then
    make -C "$NATIVE_KDIR" M="$work/fixture" CC=gcc-14 CONFIG_DEBUG_INFO_BTF_MODULES= modules
else
    cp "$ACS_NATIVE_MODULE" "$work/fixture/native_acs_probe.ko"
fi
cp "$work/fixture/native_acs_probe.ko" "$work/root/modules/"
cp /bin/busybox "$work/root/bin/"
ln -sf busybox "$work/root/bin/sh"
cp /source/acs/tests/native-guest-init "$work/root/init"
(cd "$work/root" && find . -print0 | cpio --null -o -H newc --quiet | gzip -1) > "$work/initramfs.gz"
for mode in baseline root redirect_all off_all; do
 case "$mode" in
 baseline) extra=;;
 root) extra='pci=disable_acs_redir=0000:00:03.1';;
 redirect_all) extra='pci=disable_acs_redir=0000:00:03.1;0000:03:01.0;0000:03:02.0;0000:06:01.0';;
 off_all) extra='pci=config_acs=0000000@0000:00:03.1;0000000@0000:03:01.0;0000000@0000:03:02.0;0000000@0000:06:01.0';;
 esac
 ACS_VM_IOMMU=1 ACS_VM_CMDLINE="intel_iommu=on $extra" python3 /source/acs/tests/run-vm.py "$work" "/boot/vmlinuz-$NATIVE_KERNEL" > "/out/native-$mode.log"
done
