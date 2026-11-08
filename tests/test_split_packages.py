import gzip,subprocess,tempfile,unittest,sys,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import preinstall
class Split(unittest.TestCase):
 def run_preinst(self,kernels,action='install'):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp);boot=root/'boot';boot.mkdir()
   for kernel,params,path in kernels:
    elf=b'\x7fELF'+(b'/%x.%x%c\0%x:%x:%x.%x%c\0'if path else b'')+b'\0'.join(p.encode()+b'=\0'for p in params)
    (boot/('vmlinuz-'+kernel)).write_bytes(gzip.compress(elf))
   script=root/'preinst';script.write_text(preinstall.text(ROOT).replace("BOOT=Path('/boot')",'BOOT=Path('+repr(str(boot))+')'))
   return subprocess.run(['sh',str(script),action],capture_output=True,text=True)
 def test_preinst_reports_both_features_without_changing_configuration(self):
  r=self.run_preinst([('6.12.test',['disable_acs_redir','config_acs'],True)])
  self.assertEqual(r.returncode,0,r.stderr);self.assertIn('disable_acs_redir=True config_acs=True',r.stdout)
 def test_backport_with_only_disable_is_accepted(self):
  self.assertEqual(self.run_preinst([('4.19.test',['disable_acs_redir'],True)]).returncode,0)
 def test_unsupported_or_unknown_path_parser_blocks_before_unpack(self):
  for kernels in ([],[('old',[],True)],[('unknown',['disable_acs_redir'],False)],[('new',['config_acs'],True),('old',[],True)]):
   with self.subTest(kernels=kernels):self.assertNotEqual(self.run_preinst(kernels).returncode,0)
 def test_abort_upgrade_does_not_probe(self):
  self.assertEqual(self.run_preinst([],action='abort-upgrade').returncode,0)
 def test_packages_are_mutually_exclusive_and_native_has_no_module_hook(self):
  control=(ROOT/'debian/control').read_text()
  module=control.split('Package: pci-acs-scope-dkms\n',1)[1].split('\nPackage:',1)[0]
  native=control.split('Package: pci-acs-scope-native\n',1)[1]
  self.assertIn('Conflicts: pci-acs-scope-prebuilt, pci-acs-scope-native',module)
  self.assertIn('Conflicts: pci-acs-scope-dkms, pci-acs-scope-prebuilt',native)
  for stanza in (module,native):
   self.assertIn('Provides: pci-acs-scope (= ${binary:Version})',stanza)
  self.assertIn('Pre-Depends: python3',native)
  install=(ROOT/'debian/pci-acs-scope-native.install').read_text()
  for value in ('src/','initramfs/','early-udev-wrapper','run-early'):self.assertNotIn(value,install)
