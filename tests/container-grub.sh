#!/bin/bash
# Disposable container only. Real grub-mkconfig/10_linux; synthetic disk probe.
set -euo pipefail
if ! command -v grub-mkconfig >/dev/null;then
 apt-get update -qq
 DEBIAN_FRONTEND=noninteractive apt-get install -y -qq grub-common grub2-common > /out/validation/grub-install.log
fi
mkdir -p /boot/grub /etc/default/grub.d /work
# An overlay container has no GRUB-addressable root disk. Replace disk probing
# only, retaining real generators and grub-script-check.
mv /usr/sbin/grub-probe /usr/sbin/grub-probe.real
cat > /usr/sbin/grub-probe <<'EOF'
#!/bin/sh
target=
while [ "$#" -gt 0 ]; do
 case "$1" in -t|--target) shift;target=$1;; --target=*) target=${1#*=};; esac
 shift
done
case "$target" in
 device) echo /dev/sda;; fs) echo ext2;; fs_uuid) echo 00000000-1111-2222-3333-444444444444;;
 drive) echo '(hd0,gpt1)';; partmap) echo gpt;; abstraction|cryptodisk_uuid) :;;
 *) exit 0;; esac
EOF
chmod 755 /usr/sbin/grub-probe
# The real kernel is already present in the builder, no host /boot mounted.
cp /boot/vmlinuz-7.0.0-30-generic /work/kernel-copy
printf 'GRUB_CMDLINE_LINUX="existing=1"\nGRUB_DISABLE_OS_PROBER=true\n' > /etc/default/grub
python3 - <<'PY'
import runpy
m=runpy.run_path('/source/acs/bin/pci-acs-native')
selectors=['0000:00:03.1/00.0/08.0/00.0/00.0','0000:00:03.1/00.0/08.0/00.0/10.0']
args=[m['argument']('disable_acs_redir',selectors,'redirect'),m['argument']('config_acs',selectors,'off')]
open('/etc/default/grub.d/60-pci-acs-scope.cfg','w').write(m['grub_text'](args))
open('/out/validation/generated-60-pci-acs-scope.cfg','w').write(m['grub_text'](args))
PY
grub-mkconfig -o /out/validation/generated-grub.cfg
# Parse the generated menu with GRUB's actual parser, then verify each linux
# line retains one quoted argument (and both entire lists) per policy.
grub-script-check /out/validation/generated-grub.cfg
python3 - <<'PY'
import shlex
text=open('/out/validation/generated-grub.cfg').read()
lines=[l.strip()for l in text.splitlines()if l.strip().startswith(('linux ','linux\t'))]
assert lines,'No actual linux menu entries generated'
for line in lines:
 assert "'pci=disable_acs_redir=" in line and "'pci=config_acs=" in line,line
 args=shlex.split(line)
 for prefix in ('pci=disable_acs_redir=','pci=config_acs='):
  matching=[a for a in args if a.startswith(prefix)]
  assert len(matching)==1 and ';' in matching[0]and matching[0].endswith('/10.0'),line
print('PASS: real grub-mkconfig + 10_linux + grub-script-check; semicolons remain in quoted single kernel arguments (disk probing mocked, not a GRUB boot)')
PY
