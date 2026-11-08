#!/bin/bash
set -euo pipefail
apt-get update -qq
apt-get install -y -qq gcc-14 > /out/validation/compiler-6.12.log
cp -r /source/acs /work-acs
cd /work-acs
kernel=6.12.113+deb13-amd64
headers=/usr/src/linux-headers-$kernel
export SOURCE_DATE_EPOCH=$(dpkg-parsechangelog -STimestamp)
export KBUILD_BUILD_USER=builder KBUILD_BUILD_HOST=acs-builder KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP=$(date -u -d "@$SOURCE_DATE_EPOCH" '+%a %b %d %T UTC %Y')
make -C src KDIR="$headers" KERNEL_RELEASE="$kernel" CC=gcc-14 CONFIG_DEBUG_INFO_BTF_MODULES=
make deb-prebuilt MODULE=src/pci_acs_scope.ko KERNEL_RELEASE="$kernel" KERNEL_PACKAGE_VERSION=6.12.113-1 OUTPUT_DIR=/out
work=/out/validation/vm-6.12
mkdir -p "$work/fixture" "$work/root/"{bin,dev,proc,sys,modules}
cp tests/acs_fixture.c "$work/fixture/"
printf 'obj-m += acs_fixture.o\n' > "$work/fixture/Makefile"
make -C "$headers" M="$work/fixture" CC=gcc-14 CONFIG_DEBUG_INFO_BTF_MODULES= modules
cp src/pci_acs_scope.ko "$work/fixture/acs_fixture.ko" "$work/root/modules/"
cp /bin/busybox "$work/root/bin/"
ln -sf busybox "$work/root/bin/sh"
cp tests/guest-init "$work/root/init"
(cd "$work/root" && find . -print0 | cpio --null -o -H newc --quiet | gzip -1) > "$work/initramfs.gz"
python3 tests/run-vm.py "$work" "/boot/vmlinuz-$kernel" > /out/validation/vm-6.12.log
python3 -c "import runpy; m=runpy.run_path('bin/pci-acs-native'); print(m['image_support']('$kernel'))" > /out/validation/native-detection-6.12.txt
