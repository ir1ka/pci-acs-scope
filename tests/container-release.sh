#!/bin/bash
# Disposable container only. Never mount host /lib/modules or /boot writable.
set -euo pipefail
: "${TARGET_KERNEL:=7.0.0-30-generic}"
: "${TARGET_KERNEL_PACKAGE_VERSION:=7.0.0-30.30~24.04.1}"
mkdir -p /work-acs
(cd /source/acs && tar --exclude=.git --exclude=build --exclude=dist --exclude=__pycache__ -cf - .) | tar -xf - -C /work-acs
cd /work-acs
version=$(dpkg-parsechangelog -SVersion)
export SOURCE_DATE_EPOCH=$(dpkg-parsechangelog -STimestamp)
export KBUILD_BUILD_USER=builder KBUILD_BUILD_HOST=acs-builder KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP=$(date -u -d "@$SOURCE_DATE_EPOCH" '+%a %b %d %T UTC %Y')
make check
make -C src KERNEL_RELEASE="$TARGET_KERNEL"
make deb
cp dist/*.deb /out/
make deb-prebuilt MODULE=src/pci_acs_scope.ko KERNEL_RELEASE="$TARGET_KERNEL" KERNEL_PACKAGE_VERSION="$TARGET_KERNEL_PACKAGE_VERSION" OUTPUT_DIR=/out
package_abi=${TARGET_KERNEL//-/.}
package_abi=${package_abi//+/.}
sha256sum "/out/pci-acs-scope-prebuilt_${version}+k${package_abi}_amd64.deb" > /out/first.sha256
make deb-prebuilt MODULE=src/pci_acs_scope.ko KERNEL_RELEASE="$TARGET_KERNEL" KERNEL_PACKAGE_VERSION="$TARGET_KERNEL_PACKAGE_VERSION" OUTPUT_DIR=/out
sha256sum "/out/pci-acs-scope-prebuilt_${version}+k${package_abi}_amd64.deb" > /out/second.sha256
cmp /out/first.sha256 /out/second.sha256
mkdir -p /out/validation /work/pkg /work/dkms /work/install
cp src/pci_acs_scope.ko /out/validation/tested-module.ko
dpkg-deb -x /out/pci-acs-scope-dkms_*.deb /work/pkg
dkms add -m pci-acs-scope -v "$version" --sourcetree /work/pkg/usr/src --dkmstree /work/dkms --installtree /work/install
dkms build -m pci-acs-scope -v "$version" -k "$TARGET_KERNEL" --kernelsourcedir "/lib/modules/$TARGET_KERNEL/build" --sourcetree /work/pkg/usr/src --dkmstree /work/dkms --installtree /work/install > /out/validation/dkms.log 2>&1
modinfo src/pci_acs_scope.ko > /out/validation/modinfo.txt
for f in /out/*.deb; do dpkg-deb -f "$f" Package Version Conflicts Replaces; done > /out/validation/package-conflicts.txt
# Diskless VM: same built recovery module; no real PCI devices passed through.
mkdir -p /out/validation/vm/fixture /out/validation/vm/root/{bin,dev,proc,sys,modules}
work=/out/validation/vm
cp tests/acs_fixture.c "$work/fixture/"
printf 'obj-m += acs_fixture.o\n' > "$work/fixture/Makefile"
make -C "/lib/modules/$TARGET_KERNEL/build" M="$work/fixture" modules
cp src/pci_acs_scope.ko "$work/fixture/acs_fixture.ko" "$work/root/modules/"
cp /bin/busybox "$work/root/bin/"
ln -sf busybox "$work/root/bin/sh"
cp tests/guest-init "$work/root/init"
(cd "$work/root" && find . -print0 | cpio --null -o -H newc --quiet | gzip -1) > "$work/initramfs.gz"
python3 tests/run-vm.py "$work" "/boot/vmlinuz-$TARGET_KERNEL" > /out/validation/vm.log
for kind in baseline disable config;do
 extra="acs_native_test=$kind intel_iommu=on"
 case "$kind" in
 disable) extra="$extra pci=disable_acs_redir=0000:00:03.1/00.0/01.0/00.0;0000:00:03.0/1f.7";;
 config) extra="$extra pci=config_acs=x0x00xx@0000:00:03.1/00.0/01.0/00.0;x0x00xx@0000:00:03.0/1f.7";;
 esac
 ACS_VM_NATIVE=1 ACS_VM_IOMMU=1 ACS_VM_CMDLINE="$extra" python3 tests/run-vm.py "$work" "/boot/vmlinuz-$TARGET_KERNEL" > "/out/validation/native-$kind.log"
done
printf '%s\n' 'PASS: ACS unit tests, reproducible prebuilt, isolated DKMS and target kernel VM' > /out/validation/build-result.txt
