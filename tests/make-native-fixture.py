#!/usr/bin/python3
"""Generate synthetic PCI sysfs for a disposable container's /fixture mount."""
from pathlib import Path
import sys
base=Path(sys.argv[1]);devices=base/'fake-devices';devices.mkdir(parents=True,exist_ok=True)
root=Path('pci0000:00/0000:00:03.1')
for bdf,path,kind,cap in [('0000:00:03.1',root,4,0x7f),('0000:14:00.0',root/'0000:14:00.0',5,None),('0000:15:00.0',root/'0000:14:00.0/0000:15:00.0',6,0x7f),('0000:15:01.0',root/'0000:14:00.0/0000:15:01.0',0,0x51)]:
    target=base/path;target.mkdir(parents=True,exist_ok=True)
    data=bytearray(4096);data[:4]=(0x12348086).to_bytes(4,'little')
    data[0x34]=0x40;data[0x40]=0x10;data[0x42]=kind<<4
    if cap is not None:
        data[0x100:0x104]=(0x1000d).to_bytes(4,'little')
        data[0x104:0x106]=cap.to_bytes(2,'little');data[0x106:0x108]=(cap&0x1d).to_bytes(2,'little')
    (target/'config').write_bytes(data);(target/'class').write_text('0x060400\n'if kind else'0x030000\n')
    link=devices/bdf
    if link.is_symlink():link.unlink()
    link.symlink_to('/fixture/'+str(path))
