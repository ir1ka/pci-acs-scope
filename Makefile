.PHONY: check deb source integration deb-prebuilt
check:
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
deb:
	python3 tools/build_debian.py binary
source:
	python3 tools/build_debian.py source
integration:
	tests/run-integration.sh

# Target ABI/header paths are explicit; never infer the host running kernel.
deb-prebuilt:
	@test -n "$(MODULE)$(KDIR)$(KERNEL_RELEASE)" || { echo 'Set MODULE, KDIR, or KERNEL_RELEASE' >&2; exit 2; }
	python3 tools/build_prebuilt_deb.py $(if $(MODULE),--module "$(MODULE)",--headers "$(if $(KDIR),$(KDIR),/lib/modules/$(KERNEL_RELEASE)/build)") $(if $(KERNEL_RELEASE),--kernel "$(KERNEL_RELEASE)") $(if $(KERNEL_PACKAGE_VERSION),--kernel-package-version "$(KERNEL_PACKAGE_VERSION)") $(if $(filter default undefined,$(origin CC)),,--cc "$(CC)") $(if $(ARCH),--arch "$(ARCH)") $(if $(CROSS_COMPILE),--cross-compile "$(CROSS_COMPILE)") --output "$(if $(OUTPUT_DIR),$(OUTPUT_DIR),dist)"

.PHONY: deb-module deb-native
deb-module deb-native: deb
	@echo 'Both mutually exclusive packages built; choose one to install.'

# Run inside a disposable container as root; no module insertion.
.PHONY: check-initramfs
check-initramfs:
	python3 tests/busybox_initramfs.py
	python3 tests/runtime_bundle.py
