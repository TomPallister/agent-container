"""Disposable real encrypted transfer acceptance. No proxy, production data or rebuild."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli as c
import transfer_io as wire


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--machine',default='agent-container');args=parser.parse_args()
    name='transfer-'+uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='agent-transfer-smoke-') as tmp:
        root=Path(tmp).resolve(); config=root/'config.json'
        cfg={'version':1,'name':name,'machine':args.machine,'proxyConfig':'/unused/client.json',
             'git':{'name':'Example Engineer','email':'engineer@example.com','signingKey':''},
             'shell':'zsh','image':'localhost/agent-container:dev','unlock':{'method':'manual'},'buildCA':'','githubSecret':''}
        c.save_new(config,(json.dumps(cfg)+'\n').encode());r=c.Runtime(cfg,config_path=config)
        def command(action,*paths,ok=True):
            result=c.run([sys.executable,'-B',c.ROOT/'cli.py','--config',config,action,'--',*paths],timeout=180,check=False)
            if (result.returncode==0)!=ok:
                raise AssertionError(result.stdout.decode()+result.stderr.decode())
            return result
        try:
            r.ensure_machine();r.ensure_container()
            for action in ['init','mount']:
                r.call('exec','-i',r.container,'/usr/local/bin/mount-devcrypt.sh',action,data=b'disposable-transfer-fixture\n')
            started=r.inspect('container',r.container)['State']['StartedAt']
            src=root/'source';src.mkdir();(src/'empty').mkdir()
            file=src/'literal "$(name)"\n.txt';file.write_bytes(b'roundtrip\0'*1024*1024);file.chmod(0o751)
            os.utime(file,ns=(1700000000123456789,1700000000123456789));(src/'link').symlink_to(file.name)
            command('copy',str(src),'dev:transfer/source')
            command('copy','dev:transfer/source',str(root/'roundtrip'))
            assert wire.path_snapshot(str(src))[0]==wire.path_snapshot(str(root/'roundtrip'))[0]
            command('copy',str(file),'dev:transfer/single')
            command('copy','dev:transfer/single',str(root/'single'))
            assert (root/'single').read_bytes()==file.read_bytes()
            command('copy',str(src),'dev:transfer/source',ok=False)
            command('copy',str(src),'dev:../escape',ok=False)
            r.call('exec',r.container,'ln','-s','/tmp','/home/dev/dev/escape')
            command('copy',str(src),'dev:escape/out',ok=False)
            r.call('exec',r.container,'ln','-s','/etc/passwd','/home/dev/dev/transfer/source/unsafe')
            command('copy','dev:transfer/source',str(root/'unsafe-download'),ok=False)
            assert not (root/'unsafe-download').exists()
            move=root/'move';move.mkdir();(move/'keep').write_text('verified move')
            expected=wire.path_snapshot(str(move))[0]
            command('move',str(move),'dev:moved');assert not move.exists()
            command('copy','dev:moved',str(root/'moved-back'))
            assert wire.path_snapshot(str(root/'moved-back'))[0]==expected
            r.call('exec',r.container,'fusermount','-u','/home/dev/dev')
            command('copy',str(src),'dev:locked',ok=False)
            assert r.call('exec',r.container,'test','-e','/home/dev/dev/locked',check=False).returncode!=0
            assert r.inspect('container',r.container)['State']['StartedAt']==started
            r.call('stop',r.container)
            command('copy',str(src),'dev:stopped',ok=False)
            assert not r.inspect('container',r.container)['State']['Running']
            print('PASS: real encrypted bidirectional transfers, metadata/content, verified move, path/link guards, no overwrite, locked/stopped refusal; no proxy or production lifecycle changes')
        finally:
            info=r.inspect('container',r.container)
            if info:
                r.check_topology(info);r.call('rm','-f',info['Id'])
            for kind,name in [('volume',r.volume),('network',r.network)]:
                info=r.inspect(kind,name)
                if info:r.owned(info);r.call(kind,'rm',name)


if __name__=='__main__':main()
