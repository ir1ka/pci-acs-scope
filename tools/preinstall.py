"""Generate a self-contained preinst; no dependency on files not yet unpacked."""
from pathlib import Path
CHECK = '''
images=sorted({p.name.split('-',1)[1]for pattern in ('vmlinuz-*','vmlinux-*')for p in BOOT.glob(pattern)})
if not images:
    raise SystemExit('pci-acs-scope-native: no installed boot kernel image; native support cannot be verified')
failed=[]
for kernel in images:
    support=image_support(kernel)
    params=support['parameters']
    print('pci-acs-scope-native: kernel='+kernel+' disable_acs_redir='+str('disable_acs_redir'in params)+' config_acs='+str('config_acs'in params)+' path_selectors='+str(support['path_selectors']))
    if choose(support,'redirect',True)=='module':failed.append(kernel+': '+support['reason'])
if failed:raise SystemExit('Native ACS unsupported/unverified; refusing installation: '+'; '.join(failed))
'''
def text(root):
    detector=(Path(root)/'bin/pci-acs-native').read_text()
    return "#!/bin/sh\nset -eu\ncase ${1:-} in install|upgrade) ;; *) exit 0;; esac\npython3 - <<'ACS_NATIVE_PREINSTALL'\n"+detector+'\n'+CHECK+"\nACS_NATIVE_PREINSTALL\n"

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(text(Path(__file__).resolve().parents[1]))
    args.output.chmod(0o755)
