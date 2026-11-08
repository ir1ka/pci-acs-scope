#!/usr/bin/python3
"""Diskless guest; no host disks, networking or PCI passthrough."""
from pathlib import Path
import os
import subprocess
import sys

work, kernel = Path(sys.argv[1]), sys.argv[2]
cmd = ['qemu-system-x86_64', '-machine', 'q35,accel=kvm', '-cpu', 'host', '-m', '512',
       '-smp', '2', '-nodefaults', '-no-reboot', '-display', 'none', '-serial', 'stdio',
       '-monitor', 'none', '-smbios', 'type=1,manufacturer=QEMU,product=ACS-Scope-Test',
       '-kernel', kernel, '-initrd', str(work/'initramfs.gz'),
       '-append', 'console=ttyS0 rdinit=/init panic=1 loglevel=7 pci=pcie_scan_all '+os.environ.get('ACS_VM_CMDLINE',''),
       '-device', 'pcie-root-port,id=dummy,bus=pcie.0,addr=3.0,multifunction=on,chassis=4',
       '-device', 'pcie-root-port,id=target,bus=pcie.0,addr=3.1,chassis=5',
       '-device', 'x3130-upstream,id=sw1,bus=target,addr=0',
       '-device', 'xio3130-downstream,id=near,bus=sw1,addr=1,chassis=1',
       '-device', 'xio3130-downstream,id=deep,bus=sw1,addr=2,chassis=2',
       '-device', 'x3130-upstream,id=sw2,bus=deep,addr=0',
       '-device', 'xio3130-downstream,id=leaf,bus=sw2,addr=1,chassis=3',
       '-device', 'pcie-root-port,id=outside,bus=pcie.0,addr=4,chassis=6',
       '-device', 'pci-testdev,id=endpoint,bus=near,addr=0',
       '-device', 'pci-testdev,id=endpoint2,bus=leaf,addr=0']
if os.environ.get('ACS_VM_NATIVE'):
    index=cmd.index('pci-testdev,id=endpoint,bus=near,addr=0')
    cmd[index]='pcie-root-port,id=nativeleaf,bus=near,addr=0,chassis=8'
if os.environ.get('ACS_VM_IOMMU')=='1':
    cmd+=['-device','intel-iommu,intremap=off,caching-mode=on']
with (work/'serial.log').open('w+') as f:
    result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=60)
    f.seek(0)
    output = f.read()
print(output)
if result.returncode or 'ACS-TEST PASS' not in output or 'ACS-TEST FAIL' in output:
    raise SystemExit(1)
