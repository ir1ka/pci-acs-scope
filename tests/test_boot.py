import contextlib,importlib.machinery,importlib.util,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parents[1]
l=importlib.machinery.SourceFileLoader('acsboot',str(P/'bin/pci-acs-boot'));s=importlib.util.spec_from_loader(l.name,l);app=importlib.util.module_from_spec(s);l.exec_module(app)
class BootTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
  for name,value in [('CONFIG',self.base/'boot.json'),('STATE',self.base/'state')]:
   p=patch.object(app,name,value);p.start();self.addCleanup(p.stop)
  for target,value in [(app.NATIVE,'GRUB'),(app.NATIVE,'BOOT')]:
   p=patch.object(target,value,self.base/value);p.start();self.addCleanup(p.stop)
  p=patch.object(app.shutil,'which',return_value='/usr/sbin/test-tool');p.start();self.addCleanup(p.stop)
 def test_default_off_noop_disable(self):
  with patch.object(app,'refresh')as r:app.disable();r.assert_not_called()
 def test_enable_requires_scope_and_defaults_are_explicit(self):
  with contextlib.redirect_stdout(io.StringIO()),patch.object(app,'refresh')as r:
   app.enable(['00:03.1'],'redirect');r.assert_called_once()
  self.assertEqual(app.config()['ports'],['0000:00:03.1'])
  self.assertEqual(app.config()['policy'],'redirect')
 def test_ordered_port_flags_persist_individual_inclusion(self):
  argv=['pci-acs-boot','enable','--port','00:03.1','--include','--port','0c:00.0','--port','15:10.0','--include']
  original=open
  with patch('sys.argv',argv),patch.object(app.os,'geteuid',return_value=0),patch.object(app,'refresh'),patch('builtins.open',side_effect=open)as opened,patch.object(app.fcntl,'flock'),contextlib.redirect_stdout(io.StringIO()):
   # Redirect the lock as well as config; never require root on the test host.
   opened.side_effect=lambda name,*a,**kw:original(str(self.base/'lock')if name=='/run/lock/pci-acs-boot.lock'else name,*a,**kw)
   self.assertEqual(app.main(),0)
  c=app.config();self.assertEqual(c['included_ports'],['0000:00:03.1','0000:15:10.0']);self.assertEqual(len(c['ports']),3)
 def test_enable_failure_restores_previous_config_and_images(self):
  with contextlib.redirect_stdout(io.StringIO()),patch.object(app,'refresh'):app.enable(['00:03.1'],'off')
  before=app.CONFIG.read_text()
  with patch.object(app,'refresh',side_effect=[OSError('fail'),None])as r,self.assertRaises(RuntimeError):app.enable(['00:04.0'],'redirect')
  self.assertEqual(app.CONFIG.read_text(),before);self.assertEqual(r.call_count,2)
 def test_scope_probe_failure_rolls_back_new_optin(self):
  with patch.object(app,'refresh',side_effect=[RuntimeError('scope unavailable'),None]),self.assertRaises(RuntimeError):app.enable(['00:03.1'],'redirect')
  self.assertFalse(app.CONFIG.exists());self.assertFalse(app.STATE.exists())
 def test_disable_failure_keeps_retry_state(self):
  with contextlib.redirect_stdout(io.StringIO()),patch.object(app,'refresh'):app.enable(['00:03.1'],'off')
  with patch.object(app.shutil,'which',return_value=None),patch.object(app,'refresh',side_effect=OSError('fail')),self.assertRaises(OSError):app.disable()
  self.assertFalse(app.CONFIG.exists());self.assertTrue(app.STATE.exists())
 def test_unmanaged_configuration_is_not_overwritten(self):
  app.CONFIG.write_text('{}')
  with self.assertRaises(RuntimeError):app.enable(['00:03.1'],'off')
 def test_status_does_not_apply_policy(self):
  with patch('sys.argv',['pci-acs-boot','status']),patch.object(app,'refresh')as r,contextlib.redirect_stdout(io.StringIO()):self.assertEqual(app.main(),0);r.assert_not_called()
class WrapperTests(unittest.TestCase):
 def test_wrapper_chains_arbitrary_existing_udev_entry(self):
  import os,subprocess
  with tempfile.TemporaryDirectory()as temp:
   root=Path(temp);log=root/'log'
   for name in ('acs','vendor'):
    f=root/name;f.write_text('#!/bin/sh\necho '+name+' >> "$ORDER_LOG"\n');f.chmod(0o755)
   wrapper=(P/'lib/early-udev-wrapper').read_text().replace('/lib/pci-acs-scope/runtime/sh','/bin/sh').replace('/lib/pci-acs-scope/run-early',str(root/'acs')).replace('/lib/pci-acs-scope/udev.original',str(root/'vendor'))
   subprocess.run(['sh','-c',wrapper],check=True,env=dict(os.environ,ORDER_LOG=str(log)))
   self.assertEqual(log.read_text().splitlines(),['acs','vendor'])

class PlanCleanup(unittest.TestCase):
 def test_disable_cleans_only_owned_plan_without_configuration(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp)
   with patch.object(app,'CONFIG',root/'config'),patch.object(app,'STATE',root/'state'),patch.object(app.NATIVE,'GRUB',root/'grub'):
    app.atomic(app.plan_path(),json.dumps({'schema':1,'managed_by':app.MANAGER,'grub_sha256':None}))
    with patch.object(app,'refresh')as refresh,patch.object(app,'audit_timer'),contextlib.redirect_stdout(io.StringIO()):app.disable()
    refresh.assert_called_once();self.assertFalse(app.plan_path().exists());self.assertFalse(app.STATE.exists())

 def test_failed_image_rebuild_retains_pending_grub_refresh(self):
  import subprocess
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp)
   with patch.object(app,'CONFIG',root/'config'),patch.object(app,'STATE',root/'state'),patch.object(app.NATIVE,'GRUB',root/'grub'),patch.object(app,'delivery_backend',return_value='module'),patch.object(app,'grub_available',return_value=True):
    app.NATIVE.GRUB.write_text('old generated file')
    def prepare():
     app.NATIVE.GRUB.unlink(missing_ok=True)
     app.atomic(app.plan_path(),json.dumps({'schema':1,'managed_by':app.MANAGER,'grub_sha256':None}))
    with patch.object(app,'prepare_grub',side_effect=prepare),patch.object(app.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['update-initramfs'])):
     with self.assertRaises(subprocess.CalledProcessError):app.refresh()
    self.assertTrue(app.read_plan()['grub_rebuild_needed'])
    with patch.object(app,'prepare_grub',side_effect=prepare),patch.object(app.subprocess,'run')as run:app.refresh(images=False)
    run.assert_called_once_with(['update-grub'],check=True)
    self.assertFalse(app.read_plan().get('grub_rebuild_needed',False))

class ForeignConfiguration(unittest.TestCase):
 def test_foreign_package_configuration_is_never_deleted(self):
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp)
   with patch.object(app,'CONFIG',root/'config'),patch.object(app,'STATE',root/'state'),patch.object(app.NATIVE,'GRUB',root/'grub'):
    app.CONFIG.write_text(json.dumps({'schema':1,'managed_by':app.MANAGER,'ports':['0000:00:03.1'],'policy':'redirect'}))
    query=Mock();query.communicate.return_value=('configuration-package: '+str(app.CONFIG)+'\n',);query.returncode=0
    with patch.object(app.subprocess,'Popen')as popen,patch.object(app,'refresh')as refresh:
     popen.return_value.__enter__.return_value=query
     with self.assertRaisesRegex(RuntimeError,'another package'):app.disable()
    refresh.assert_not_called();self.assertTrue(app.CONFIG.exists())
