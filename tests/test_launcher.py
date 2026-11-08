import importlib.machinery
import importlib.util
import contextlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch, Mock

SOURCE = Path(__file__).resolve().parents[1]/'bin/pci-acs-scope'
loader = importlib.machinery.SourceFileLoader('acs_launcher', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
acs = importlib.util.module_from_spec(spec)
loader.exec_module(acs)


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        backend=patch.object(acs,'BACKEND',self.base/'backend');backend.start();self.addCleanup(backend.stop)
        (self.base/'backend').write_text('module')
        snapshot=patch.object(acs,'kernel_snapshot',return_value={'schema':1,'ports':['0000:00:03.1'],'policy':'off','include_ports':False,'devices':[]})
        snapshot.start();self.addCleanup(snapshot.stop)
        self.devices = self.base/'devices'
        self.devices.mkdir()
        self.root = self.add_device('0000:00:03.1', self.base/'pci0000:00', root=True)
        self.bridge = self.add_device('0000:0c:00.0', self.root)
        self.ep = self.add_device('0000:0f:00.0', self.bridge, acs_cap=False)
        self.outside = self.add_device('0000:00:03.2', self.base/'pci0000:00', root=True)
        self.add_device('0000:1d:00.0', self.outside)

    def add_device(self, bdf, parent, root=False, acs_cap=True):
        p = parent/bdf
        p.mkdir(parents=True)
        config = bytearray(4096)
        struct.pack_into('<I', config, 0, 0x12341000)
        config[0x34] = 0x50
        struct.pack_into('<HH', config, 0x50, 0x10, 0x42 if root else 0x62)
        if acs_cap:
            struct.pack_into('<IHH', config, 0x100, 0x1000d, 0x207f, 0x1d)
        (p/'config').write_bytes(config)
        (self.devices/bdf).symlink_to(p)
        return p

    def test_normalize_and_reject_wildcards(self):
        self.assertEqual(acs.normalize('00:03.1'), '0000:00:03.1')
        self.assertEqual(acs.normalize('ABCD:AF:1F.7'), 'abcd:af:1f.7')
        for bdf in ('*', 'pci:1000:c010', '00:20.0', '00:03.8', '../config', '0:3.1', ''):
            with self.assertRaises(ValueError):
                acs.normalize(bdf)

    def test_scope_uses_ancestry_includes_root_and_all_levels(self):
        rows = acs.inventory(['0000:00:03.1'], self.devices)
        self.assertEqual([r['bdf'] for r in rows], ['0000:00:03.1', '0000:0c:00.0', '0000:0f:00.0'])
        self.assertEqual(rows[0]['acs']['ctrl'], 0x1d)
        self.assertIsNone(rows[2]['acs'])

    def test_multiple_roots(self):
        self.assertEqual(len(acs.inventory(['0000:00:03.1', '0000:00:03.2'], self.devices)), 5)

    def test_non_root_port_accepted_endpoint_and_missing_rejected(self):
        self.assertEqual(len(acs.inventory(['0000:0c:00.0'],self.devices)),2)
        with (self.ep/'config').open('r+b')as f:f.seek(0x52);f.write(struct.pack('<H',2))
        with self.assertRaises(ValueError):acs.inventory(['0000:0f:00.0'],self.devices)
        with self.assertRaises(FileNotFoundError):
            acs.inventory(['0000:00:05.0'], self.devices)

    def test_malformed_capability_is_error_not_no_acs(self):
        with (self.bridge/'config').open('r+b') as f:
            f.seek(0x100)
            f.write(struct.pack('<I', (0x100 << 20) | 1))
        with self.assertRaises(ValueError):
            acs.inventory(['0000:00:03.1'], self.devices)

    def test_short_read_fails(self):
        (self.bridge/'config').write_bytes(b'\0'*300)
        with (self.bridge/'config').open('rb') as f:
            with self.assertRaises(OSError):
                acs.read(f.fileno(), 300, 4)

    def test_status_never_launches_or_writes(self):
        with patch.object(acs, 'status', return_value={}) as status, patch.object(acs.subprocess, 'run') as run, patch.object(acs, 'save_new') as save:
            self.assertEqual(acs.main(['status', '--ports', '00:03.1']), 0)
            status.assert_called_once_with(['0000:00:03.1'])
            run.assert_not_called()
            save.assert_not_called()

    def test_save_is_exclusive_and_private(self):
        p = self.base/'original.json'
        acs.save_new(p, {'hello': 1})
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            acs.save_new(p, {})
        self.assertEqual(json.loads(p.read_text()), {'hello': 1})

    def test_disable_saves_before_modprobe_with_exact_scope(self):
        module = self.base/'module'
        initial = {'devices': [{'bdf': '0000:00:03.1', 'acs': {'ctrl': 0x1d}}]}
        after = {'devices': [{'bdf': '0000:00:03.1', 'acs': {'ctrl': 0}}]}
        def probe(args, check, **kwargs):
            self.assertTrue((self.base/'save').exists())
            self.assertEqual(args[-2:], ['policy=off', 'wake=1'])
            (module/'parameters').mkdir(parents=True)
            (module/'parameters/failed').write_text('N')
        with patch.object(acs, 'MODULE', module), patch.object(acs, 'status', side_effect=[initial, after]), patch.object(acs.subprocess, 'run', side_effect=probe):
            result = acs.disable(['0000:00:03.1'], 'off', self.base/'save')
            self.assertEqual(result['mode'], 'disabled-off')

    def test_partial_failure_is_not_reported_success(self):
        module = self.base/'module'
        def probe(*args, **kwargs):
            (module/'parameters').mkdir(parents=True)
            (module/'parameters/failed').write_text('Y')
        with patch.object(acs, 'MODULE', module), patch.object(acs, 'status', return_value={'devices': []}), patch.object(acs.subprocess, 'run', side_effect=probe):
            with self.assertRaises(RuntimeError):
                acs.disable(['0000:00:03.1'], 'off', self.base/'save')

    def test_authoritative_snapshot_is_saved_separately(self):
        module=self.base/'module'
        def probe(*args,**kwargs):
            (module/'parameters').mkdir(parents=True);(module/'parameters/failed').write_text('N')
        before={'devices':[{'bdf':'0000:00:03.1','acs':{'ctrl':0x1d}}]}
        after={'devices':[{'bdf':'0000:00:03.1','acs':{'ctrl':0}}]}
        actual={'schema':1,'ports':['0000:00:03.1'],'policy':'off','include_ports':False,'mask':127,'devices':[{'original':0x11}]}
        with patch.object(acs,'MODULE',module),patch.object(acs,'status',side_effect=[before,after]),patch.object(acs,'kernel_snapshot',return_value=actual),patch.object(acs.subprocess,'run',side_effect=probe):
            acs.disable(['0000:00:03.1'],'off',self.base/'values')
        self.assertEqual(json.loads((self.base/'values.kernel.json').read_text()),actual)
    def test_post_verification_error_attempts_restore(self):
        module=self.base/'module'
        def probe(*args,**kwargs):
            (module/'parameters').mkdir(parents=True);(module/'parameters/failed').write_text('N')
        bad={'devices':[{'bdf':'0000:00:03.1','acs':{'ctrl':0x1d}}]}
        with patch.object(acs,'MODULE',module),patch.object(acs,'status',return_value=bad),patch.object(acs.subprocess,'run',side_effect=probe),patch.object(acs,'restore')as restore:
            with self.assertRaisesRegex(RuntimeError,'original controls restored'):acs.disable(['0000:00:03.1'],'off',self.base/'values')
            restore.assert_called_once()
    def test_native_plan_is_exact_list_and_does_not_apply(self):
        with patch.object(acs,'inventory',return_value=[{'bdf':'0000:00:03.1','acs':{}},{'bdf':'0000:04:00.0','acs':None},{'bdf':'0000:02:01.0','root_port':'0000:00:03.1','path':'/sys/devices/pci0000:00/0000:00:03.1/0000:01:00.0/0000:02:01.0','acs':{'ctrl':29,'cap':0x7f}}]),patch.object(acs.subprocess,'run')as run:
            plan=acs.native_plan(['0000:00:03.1'],'off')
            self.assertEqual(plan['kernel_argument'],'pci=config_acs=0000000@0000:00:03.1/00.0/01.0')
            run.assert_not_called()
    def test_native_plan_filters_unsupported_redirect_controls_not_current_state(self):
        root='0000:00:03.1'
        def row(bdf,cap,ctrl):
            return {'bdf':bdf,'root_port':root,'path':'/sys/devices/pci0000:00/'+root+('/'+bdf if bdf!=root else ''),'acs':{'cap':cap,'ctrl':ctrl}if cap is not None else None}
        rows=[row(root,0x1d,0),row('0000:01:00.0',0x51,0x51),row('0000:02:00.0',None,0),row('0000:03:00.0',0x20,0)]
        with patch.object(acs,'inventory',return_value=rows):
            plan=acs.native_plan([root],'redirect')
            self.assertEqual(plan['devices'],['0000:03:00.0'])
            self.assertEqual(len(plan['skipped']),3)
            self.assertEqual(acs.native_plan([root],'off',True)['devices'],[root,'0000:01:00.0','0000:03:00.0'])
        with patch.object(acs,'inventory',return_value=[rows[1],rows[2]]):
            plan=acs.native_plan([root],'redirect')
            self.assertEqual(plan['selectors'],[]);self.assertIsNone(plan['kernel_argument'])

    def test_native_redirect_preserves_each_selected_root_and_audits_children(self):
        roots=['0000:00:03.1','0000:00:04.0']
        rows=[{'bdf':r,'root_port':r,'path':'/sys/devices/pci0000:00/'+r,'acs':{'cap':0x1d,'ctrl':0x1d}}for r in roots]
        child={'bdf':'0000:15:00.0','root_port':roots[0],'path':rows[0]['path']+'/0000:15:00.0','acs':{'cap':0x1d,'ctrl':0}}
        with patch.object(acs,'inventory',return_value=rows):
            plan=acs.native_plan(roots,'redirect');self.assertIsNone(plan['kernel_argument']);self.assertEqual(len(plan['skipped']),2)
            self.assertTrue(all('boundary preserved'in r['reason']for r in plan['skipped']))
        with patch.object(acs,'inventory',return_value=rows+[child]):
            self.assertEqual(acs.native_plan(roots,'redirect')['devices'],[child['bdf']])
            self.assertEqual(acs.native_plan(roots,'off',True)['devices'],roots+[child['bdf']])
        with patch.object(acs,'SNAPSHOT',self.base/'absent'),patch.object(acs,'status',return_value={'devices':rows+[child]}):
            self.assertTrue(acs.audit(roots,'redirect',native=True)['compliant'])
            self.assertFalse(acs.audit(roots,'redirect',include_ports=True)['compliant'])
            child['acs']['ctrl']=0x1d
            self.assertEqual(acs.audit(roots,'redirect',native=True)['drift'],[child['bdf']])

    def test_mixed_and_nested_ports_exclude_all_boundaries_but_keep_root_anchor(self):
        ports=['0000:00:03.1','0000:0c:00.0']
        with (self.bridge/'config').open('r+b')as f:f.seek(0x52);f.write(struct.pack('<H',0x52))
        self.add_device('0000:10:00.0',self.bridge)
        rows=acs.inventory(ports,self.devices)
        self.assertEqual(len(rows),4)
        self.assertEqual([r['bdf']for r in rows if r['scope_boundary']],ports)
        with patch.object(acs,'inventory',return_value=rows):
            self.assertEqual(acs.native_plan(ports,'redirect')['devices'],['0000:10:00.0'])
            included=acs.native_plan(ports,'redirect',True)
            self.assertEqual(len(included['devices']),3)
            self.assertIn('0000:00:03.1/00.0/00.0',included['selectors'])
        with patch.object(acs,'inventory',return_value=acs.inventory(['0000:0c:00.0'],self.devices)):
            self.assertEqual(acs.native_plan(['0000:0c:00.0'],'redirect')['selectors'],['0000:00:03.1/00.0/00.0'])

    def test_repeated_port_include_applies_only_to_preceding_port(self):
        import io
        args=['native-plan','--port','00:03.1','--include','--port','0c:00.0','--port','15:10.0','--include']
        with patch.object(acs,'native_plan',return_value={})as plan,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(acs.main(args),0)
            self.assertEqual(plan.call_args.args,(['0000:00:03.1','0000:0c:00.0','0000:15:10.0'],'redirect',['0000:00:03.1','0000:15:10.0']))
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):acs.main(['native-plan','--include','--port','00:03.1'])
        with patch.object(acs,'native_plan',return_value={})as plan,contextlib.redirect_stdout(io.StringIO()):
            acs.main(['native-plan','--ports','00:03.1,0c:00.0','--port','00:03.1','--include'])
            self.assertEqual(plan.call_args.args[2],['0000:00:03.1'])

    def test_cli_write_defaults_match_native_redirect(self):
        import io
        for command in ('disable','reapply'):
            with patch.object(acs.os,'geteuid',return_value=0),patch.object(acs,'action_lock',return_value=contextlib.nullcontext()),patch.object(acs,command)as action,contextlib.redirect_stdout(io.StringIO()):
                action.return_value={'mode':'test'}
                self.assertEqual(acs.main([command,'--ports','0000:00:03.1','--save',str(self.base/'saved.json')]),0)
                self.assertEqual(action.call_args.args[1],'redirect')
        self.assertEqual(acs.MASKS['redirect'],0x2c)

    def test_native_path_survives_descendant_bus_renumbering(self):
        root='0000:00:03.1'
        a={'root_port':root,'bdf':'0000:15:00.0','path':'/sys/devices/pci0000:00/'+root+'/0000:0b:00.0/0000:0c:08.0/0000:14:00.0/0000:15:00.0'}
        b=dict(a,bdf='0000:85:00.0',path=a['path'].replace('0b:','7b:').replace('0c:','7c:').replace('14:','84:').replace('15:','85:'))
        self.assertEqual(acs.native_selector(a),'0000:00:03.1/00.0/08.0/00.0/00.0')
        self.assertEqual(acs.native_selector(a),acs.native_selector(b))

    def test_audit_drift_and_identical_id_replacement_are_read_only(self):
        current={'devices':[{'bdf':'0000:00:03.1','vendor_device':'12345678','acs':{'ctrl':0x1d}}]}
        original={'ports':['0000:00:03.1'],'policy':'redirect','devices':[{'bdf':'0000:00:03.1','vendor_device':'12345678','same_device':False}]}
        f=self.base/'snapshot';f.touch()
        with patch.object(acs,'SNAPSHOT',f),patch.object(acs,'status',return_value=current),patch.object(acs,'kernel_snapshot',return_value=original),patch.object(acs.subprocess,'run')as run:
            result=acs.audit(['0000:00:03.1']);self.assertFalse(result['compliant']);self.assertEqual(result['replaced_devices'],['0000:00:03.1']);run.assert_not_called()
    def test_restore_error_keeps_module_loaded(self):
        module = self.base/'module'
        (module/'parameters').mkdir(parents=True)
        (module/'parameters/restore').write_text('')
        (module/'parameters/remaining').write_text('1')
        with patch.object(acs, 'MODULE', module), patch.object(acs.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                acs.restore()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
