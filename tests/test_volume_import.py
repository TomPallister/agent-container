import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cli
import volume_import as v
from test_cli import config

class VolumeImportTests(unittest.TestCase):
    def test_live_source_refused(self):
        r=Mock()
        r.call.side_effect=[Mock(stdout=b'old'),Mock(stdout=json.dumps([{'State':{'Running':True},'Mounts':[{'Type':'volume','Name':'source'}]}]).encode())]
        with self.assertRaises(cli.Error): v.offline(r,'source')

    def test_incomplete_receipt_never_mounts(self):
        r=cli.Runtime(config(),sys.executable)
        info={'Labels':{cli.LABEL+'.instance':'test',cli.LABEL+'.import-source':'old'},'CreatedAt':'created'}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            receipt=v.receipt_path(path,r.volume)
            data={'source':'old','target':r.volume,'createdAt':'created','status':'incomplete'}
            cli.save_new(receipt,json.dumps(data).encode())
            with self.assertRaises(cli.Error): v.check_import(r,info,path)
            data['status']='complete'; receipt.write_text(json.dumps(data))
            v.check_import(r,info,path)
            info['CreatedAt']='different'
            with self.assertRaises(cli.Error): v.check_import(r,info,path)

    def test_existing_destination_never_overwritten(self):
        r=cli.Runtime(config(),sys.executable)
        with patch.object(r,'inspect',side_effect=[None,{'Labels':{cli.LABEL+'.instance':'test'}}]),patch.object(r,'call') as call:
            with self.assertRaises(cli.Error): v.import_volume(r,'old',Path('/unused/settings.json'))
            call.assert_not_called()

    def test_copy_command_has_ro_source_no_network_and_verification_receipt(self):
        r=cli.Runtime(config(),sys.executable); r.config['image']='localhost/custom:reviewed'
        info={'Labels':{cli.LABEL+'.instance':'test'},'CreatedAt':'new'}
        inspections=[None,None,{'CreatedAt':'old'}, {'Id':'image'},info,{'CreatedAt':'old'}]
        evidence={'digest':'a'*64,'entries':2,'bytes':20}
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'inspect',side_effect=inspections),patch.object(v,'offline') as offline,patch.object(r,'call',side_effect=[Mock(),Mock(stdout=json.dumps(evidence).encode())]) as call:
            result=v.import_volume(r,'source',Path(tmp)/'config.json')
            self.assertEqual(result['status'],'complete')
            args=call.call_args.args
            self.assertIn('none',args); self.assertIn('source:/source:ro',args)
            self.assertNotIn('--privileged',args)
            self.assertEqual(offline.call_count,2)
            receipt=json.loads(v.receipt_path(Path(tmp)/'config.json',r.volume).read_text())
            self.assertEqual(receipt['verification'],evidence)

    def test_failure_leaves_original_and_incomplete_destination(self):
        r=cli.Runtime(config(),sys.executable); r.config['image']='localhost/custom:reviewed'
        info={'Labels':{cli.LABEL+'.instance':'test'},'CreatedAt':'new'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'inspect',side_effect=[None,None,{'CreatedAt':'old'},{'Id':'image'},info]),patch.object(v,'offline'),patch.object(r,'call',side_effect=[Mock(),cli.Error('copy failure')]) as call:
            path=Path(tmp)/'config.json'
            with self.assertRaises(cli.Error): v.import_volume(r,'source',path)
            self.assertEqual(json.loads(v.receipt_path(path,r.volume).read_text())['status'],'incomplete')
            self.assertFalse(any('rm' in x.args for x in call.call_args_list))

if __name__=='__main__': unittest.main()
