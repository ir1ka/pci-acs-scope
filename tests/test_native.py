import gzip,importlib.machinery,importlib.util,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
P=Path(__file__).resolve().parents[1]
def load(name,file):
 l=importlib.machinery.SourceFileLoader(name,str(P/'bin'/file));s=importlib.util.spec_from_loader(l.name,l);m=importlib.util.module_from_spec(s);l.exec_module(m);return m
n=load('native_test','pci-acs-native');boot=load('native_boot_test','pci-acs-boot')
class NativeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  for module,name,path in [(n,'BOOT','boot'),(boot.NATIVE,'BOOT','boot'),(boot.NATIVE,'GRUB','grub.d/60.cfg'),(boot,'CONFIG','config.json'),(boot,'STATE','state/early-image-state')]:
   p=patch.object(module,name,self.root/path);p.start();self.addCleanup(p.stop)
  n.BOOT.mkdir()
  marker=self.root/'backend';marker.write_text('native\n');p=patch.object(boot,'BACKEND',marker);p.start();self.addCleanup(p.stop)
 def kernel(self,name,params,compressed=True):
  elf=b'\x7fELF/%x.%x%c\x00%x:%x:%x.%x%c\x00'+b'\x00'.join(p.encode()+b'='for p in params)+b'\x00'
  (n.BOOT/('vmlinuz-'+name)).write_bytes(b'fake-bzImage-header'+gzip.compress(elf)if compressed else elf)
 def config(self,policy='redirect'):
  boot.CONFIG.write_text(json.dumps(dict(schema=1,managed_by='pci-acs-boot',ports=['0000:00:03.1'],policy=policy)))
 def test_real_image_handlers_not_version_inference(self):
  self.kernel('old-backport',['disable_acs_redir']);self.kernel('99.0-custom',[])
  self.assertEqual(n.image_support('old-backport')['parameters'],['disable_acs_redir'])
  self.assertEqual(n.choose(n.image_support('99.0-custom'),'redirect',True),'module')
 def test_unknown_compression_falls_back(self):
  (n.BOOT/'vmlinuz-test').write_bytes(b'unrecognized')
  self.assertEqual(n.image_support('test')['parameters'],[])
 def test_redirect_prefers_native_disable_and_off_requires_config(self):
  support={'parameters':['disable_acs_redir'],'path_selectors':True}
  self.assertEqual(n.choose(support,'redirect',True),'disable_acs_redir')
  self.assertEqual(n.choose(support,'off',True),'module')
  self.assertEqual(n.choose({'parameters':['config_acs'],'path_selectors':True},'off',True),'config_acs')
  self.assertEqual(n.choose(support,'redirect',False),'module')
 def test_grub_shell_preserves_existing_args_and_semicolons(self):
  args=[n.argument('disable_acs_redir',['0000:15:00.0','0000:15:10.0'],'redirect')]
  script='GRUB_CMDLINE_LINUX="existing=1"\n'+n.grub_text(args)+'\nprintf "%s" "$GRUB_CMDLINE_LINUX"\n'
  r=subprocess.check_output(['sh','-c',script],text=True)
  self.assertEqual(r,"existing=1 '"+args[0]+"'")
  import shlex
  self.assertEqual(shlex.split(r),['existing=1',args[0]])
 def test_mixed_kernel_native_plan_rejects_without_fallback(self):
  self.kernel('old',[]);self.kernel('middle',['disable_acs_redir']);self.kernel('new',['config_acs']);self.config()
  client=Mock();client.normalize.side_effect=lambda p:p;client.include_set.side_effect=lambda ports,inc:set(inc);client.native_plan.return_value={'devices':['0000:15:00.0','0000:15:10.0'],'selectors':['0000:00:03.1/00.0/00.0','0000:00:03.1/00.0/10.0']}
  with patch.object(boot,'grub_available',return_value=True),patch.object(boot,'app',return_value=client),self.assertRaisesRegex(RuntimeError,'Native package'):boot.prepare_grub()
  self.assertFalse(boot.NATIVE.GRUB.exists())
 def test_empty_native_plan_removes_dropin_without_global_match(self):
  self.kernel('new',['disable_acs_redir']);self.config()
  client=Mock();client.normalize.side_effect=lambda p:p;client.include_set.side_effect=lambda ports,inc:set(inc);client.native_plan.return_value={'devices':[],'selectors':[]}
  with patch.object(boot,'grub_available',return_value=True),patch.object(boot,'app',return_value=client):boot.prepare_grub()
  self.assertFalse(boot.NATIVE.GRUB.exists())
  self.assertEqual(boot.config()['native_arguments'],[])
 def test_unmanaged_grub_not_overwritten(self):
  boot.NATIVE.GRUB.parent.mkdir();boot.NATIVE.GRUB.write_text('# administrator file\n');self.config()
  with self.assertRaises(RuntimeError):boot.prepare_grub()
  self.assertEqual(boot.NATIVE.GRUB.read_text(),'# administrator file\n')
 def test_disable_removes_only_managed_grub(self):
  boot.NATIVE.GRUB.parent.mkdir();boot.NATIVE.GRUB.write_text(n.grub_text(['pci=disable_acs_redir=0000:15:00.0']))
  import hashlib
  boot.atomic(boot.plan_path(),json.dumps({'schema':1,'managed_by':boot.MANAGER,'grub_sha256':hashlib.sha256(boot.NATIVE.GRUB.read_bytes()).hexdigest()}))
  boot.prepare_grub();self.assertFalse(boot.NATIVE.GRUB.exists())
 def test_no_grub_uses_module_without_hardware_probe(self):
  self.kernel('new',['config_acs']);self.config()
  with patch.object(boot,'grub_available',return_value=False),patch.object(boot,'app')as client:
   client.return_value.normalize.side_effect=lambda p:p
   client.return_value.include_set.side_effect=lambda ports,inc:set(inc)
   with self.assertRaisesRegex(RuntimeError,'Native package'):boot.prepare_grub()
   client.return_value.native_plan.assert_not_called()
  self.assertFalse(boot.NATIVE.GRUB.exists())

 def test_parameter_without_path_grammar_falls_back(self):
  (n.BOOT/'vmlinux-ancient').write_bytes(b'\x7fELFdisable_acs_redir=\x00')
  support=n.image_support('ancient')
  self.assertEqual(support['parameters'],['disable_acs_redir'])
  self.assertFalse(support['path_selectors'])
  self.assertEqual(n.choose(support,'redirect',True),'module')
 def test_native_selector_and_backend_validation(self):
  for selector in ('0000:00:03.1/20.0','0000:00:03.1;evil','*','0000:00:03.8'):
   with self.subTest(selector=selector),self.assertRaises(ValueError):n.argument('disable_acs_redir',[selector],'redirect')
  self.assertEqual(n.argument('config_acs',['0000:00:03.1/00.0/01.0'],'redirect'),'pci=config_acs=x0x00xx@0000:00:03.1/00.0/01.0')

 def test_module_package_never_selects_native(self):
  self.kernel('new',['disable_acs_redir','config_acs']);self.config()
  boot.BACKEND.write_text('module\n')
  with patch.object(boot,'grub_available',return_value=True),patch.object(boot.NATIVE,'image_support',side_effect=AssertionError('no native probing')):
   self.assertEqual(boot.backend('new',boot.config())['backend'],'module')
 def test_native_refresh_does_not_build_initramfs(self):
  with patch.object(boot,'prepare_grub'),patch.object(boot,'grub_available',return_value=True),patch.object(boot.subprocess,'run')as run:
   boot.refresh()
  self.assertEqual(run.call_args.args[0],['update-grub'])
  self.assertEqual(run.call_count,1)
 def test_native_off_requires_config_acs_without_module_fallback(self):
  self.kernel('only-disable',['disable_acs_redir'])
  with patch.object(boot,'grub_available',return_value=True),self.assertRaisesRegex(RuntimeError,'Native package'):
   boot.backend('only-disable',{'policy':'off'})

 def test_refresh_preserves_config_bytes(self):
  self.kernel('new',['disable_acs_redir']);self.config()
  before=boot.CONFIG.read_bytes()
  client=Mock();client.normalize.side_effect=lambda p:p;client.include_set.side_effect=lambda p,i:set(i);client.native_plan.return_value={'devices':['0000:15:00.0'],'selectors':['0000:00:03.1/00.0']}
  with patch.object(boot,'grub_available',return_value=True),patch.object(boot,'app',return_value=client):boot.prepare_grub()
  self.assertEqual(boot.CONFIG.read_bytes(),before)
 def test_marked_but_edited_grub_is_preserved(self):
  self.kernel('new',['disable_acs_redir']);self.config()
  client=Mock();client.normalize.side_effect=lambda p:p;client.include_set.side_effect=lambda p,i:set(i);client.native_plan.return_value={'devices':['0000:15:00.0'],'selectors':['0000:00:03.1/00.0']}
  with patch.object(boot,'grub_available',return_value=True),patch.object(boot,'app',return_value=client):boot.prepare_grub()
  edited=boot.NATIVE.GRUB.read_text()+'# administrator edit\n';boot.NATIVE.GRUB.write_text(edited)
  with self.assertRaisesRegex(RuntimeError,'modified'):boot.prepare_grub()
  self.assertEqual(boot.NATIVE.GRUB.read_text(),edited)
 def test_config_symlink_refused(self):
  target=self.root/'administrator';target.write_text('{}')
  boot.CONFIG.symlink_to(target)
  with self.assertRaisesRegex(RuntimeError,'symlink'):boot.config()
  self.assertEqual(target.read_text(),'{}')
