import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli as c
import transfer as t
import transfer_io as io
from test_cli import config


class TransferCommandTests(unittest.TestCase):
    def test_confined_device_paths(self):
        for arg in ['dev:../out', 'dev:a/../out', 'dev:a//b', 'dev:a/./b', 'dev:', 'dev:/home/dev/dev', 'dev:~/dev']:
            with self.subTest(arg=arg), self.assertRaises(c.Error): t.dev_path(arg)
        self.assertEqual(t.dev_path('dev:~/dev/a b/'), '/home/dev/dev/a b')
        self.assertEqual(t.dev_path('dev:--name'), '/home/dev/dev/--name')

    def runtime(self):
        r = Mock()
        r.container = 'ac-test'; r.volume = 'ac-test-crypt'; r.network = 'ac-test-internal'
        r.config_path = Path('/tmp/config'); r.config = config()
        r.inspect.side_effect = lambda kind, name: {
            'container': {'Id': 'a'*64, 'State': {'Running': True, 'Paused': False, 'StartedAt': 'boot'}},
            'network': {'internal': True}, 'volume': {'Name': r.volume},
        }[kind]
        return r

    def test_copy_does_not_start_or_unlock_runtime_or_require_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)/'config'; c.save_new(cfg, json.dumps(config()).encode())
            with patch.object(c, 'Runtime') as factory, patch('transfer.perform') as perform, patch.object(c, 'proxy_snapshot') as proxy:
                self.assertEqual(c.main(['--config', str(cfg), 'copy', 'file', 'dev:file']), 0)
            perform.assert_called_once()
            factory.return_value.ensure_machine.assert_not_called()
            factory.return_value.prepare.assert_not_called()
            proxy.assert_not_called()

    def test_ownership_running_and_internal_network_gates(self):
        for failure in ['stopped', 'paused', 'external-network', 'ownership', 'missing']:
            r = self.runtime(); original = r.inspect.side_effect
            def inspect(kind, name):
                data = original(kind, name)
                if kind == 'container':
                    if failure == 'missing': return None
                    if failure == 'stopped': data['State']['Running'] = False
                    if failure == 'paused': data['State']['Paused'] = True
                if kind == 'network' and failure == 'external-network': data['internal'] = False
                return data
            r.inspect.side_effect = inspect
            if failure == 'ownership': r.check_topology.side_effect = c.Error('wrong owner')
            with self.subTest(failure=failure), patch('volume_import.check_import'), self.assertRaises(c.Error): t.ready(r)
            r.command.assert_not_called()

    def test_bad_argument_direction_never_invokes_engine(self):
        for action, args in [('copy', ['a','b']), ('copy',['dev:a','dev:b']), ('move',['dev:a','b']), ('copy',['a'])]:
            r=self.runtime()
            with self.assertRaises(c.Error): t.perform(r, action, args)
            r.inspect.assert_not_called()

    def test_real_local_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'src';src.mkdir();(src/'file').write_text('content')
            dst=Path(tmp)/'dst'
            result=t.pipeline([sys.executable,'-I',str(t.HELPER),'send','host',str(src)],
                              [sys.executable,'-I',str(t.HELPER),'receive','host',str(dst)])
            records,_=io.path_snapshot(str(src))
            self.assertEqual(result['digest'],io.digest(records))
            self.assertEqual((dst/'file').read_text(),'content')

    def test_failed_sender_or_receiver_rejected(self):
        good=[sys.executable,'-c',"print("+repr(json.dumps({'digest':'a'*64,'entries':1,'bytes':0}))+')']
        bad=[sys.executable,'-c','raise SystemExit(3)']
        for sender,receiver in [(bad,good),(good,bad)]:
            with self.subTest(),self.assertRaises(c.Error): t.pipeline(sender,receiver)

    def test_cli_reports_useful_errors_without_engine_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cfg=root/'config';c.save_new(cfg,json.dumps(config()).encode())
            engine=root/'podman';engine.write_text('#!/bin/sh\nexit 99\n');engine.chmod(0o755)
            result=subprocess.run([sys.executable,'-B',str(c.ROOT/'cli.py'),'--config',str(cfg),'copy','a','b'],
                                  env={'HOME':str(root),'PATH':str(root)},capture_output=True)
            self.assertEqual(result.returncode,2)
            self.assertIn(b'Exactly one path must start with dev:',result.stderr)

    def test_move_refuses_active_directory_through_path_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source';src.mkdir();(src/'data').write_text('retain')
            alias=root/'home-alias';alias.symlink_to(src,target_is_directory=True)
            aliases=[alias]
            upper=Path(str(src).upper())
            if upper.is_dir():aliases.append(upper) # Real case-insensitive Mac path.
            for alias in aliases:
                with self.subTest(alias=alias),patch.object(t,'ready',return_value=('id','boot')),patch.object(t,'pipeline') as stream,patch.object(Path,'home',return_value=alias):
                    with self.assertRaises(c.Error):t.perform(self.runtime(),'move',[str(src),'dev:destination'])
                    stream.assert_not_called()
            self.assertTrue(src.is_dir())

    def test_recovery_name_is_surfaced_by_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'source';src.mkdir();(src/'file').write_text('kept')
            result={'digest':io.digest(io.path_snapshot(str(src))[0]),'entries':2,'bytes':4}
            name='.agent-move-'+'a'*32
            with patch.object(t,'ready',return_value=('id','boot')),patch.object(t,'pipeline',return_value=result),patch.object(io,'delete_verified',side_effect=io.RecoveryError(name)):
                with self.assertRaisesRegex(c.Error,name):t.perform(self.runtime(),'move',[str(src),'dev:destination'])
            self.assertTrue(src.exists())

    def test_move_changed_source_or_runtime_is_retained(self):
        for change in ['content','runtime','digest']:
            with tempfile.TemporaryDirectory() as tmp:
                src=Path(tmp)/'src';src.mkdir();f=src/'file';f.write_text('AAAA')
                r=self.runtime(); count=[0]
                def ready(runtime):
                    count[0]+=1
                    return ('a'*64 if count[0]==1 or change!='runtime' else 'b'*64,'boot')
                def copy(*args):
                    result={'digest':io.digest(io.path_snapshot(str(src))[0]),'entries':2,'bytes':4}
                    if change=='content': f.write_text('BBBB')
                    if change=='digest': result['digest']='0'*64
                    return result
                with patch.object(t,'ready',side_effect=ready),patch.object(t,'pipeline',side_effect=copy):
                    with self.subTest(change=change),self.assertRaises((c.Error,ValueError)):
                        t.perform(r,'move',[str(src),'dev:destination'])
                self.assertTrue(src.is_dir())


if __name__ == '__main__': unittest.main()
