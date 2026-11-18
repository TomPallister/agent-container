#!/usr/bin/env python3
"""Disposable smoke on an existing no-share libkrun machine and live proxy.

Never starts/stops the proxy or touches another development container. Uses a
synthetic workspace passphrase. Optional read-only GitHub/model/signing probes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli as c


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine',required=True)
    parser.add_argument('--proxy-config',required=True)
    parser.add_argument('--github-secret',default='')
    parser.add_argument('--signing-key-file',type=Path)
    parser.add_argument('--model',help='Optional provider/model for a single tiny model request')
    args=parser.parse_args()
    cfg={'version':1,'name':'smoke-'+uuid.uuid4().hex[:10], 'machine':args.machine,
         'proxyConfig':args.proxy_config,'git':{'name':'Smoke Test','email':'smoke@example.com',
         'signingKey':args.signing_key_file.read_text().strip() if args.signing_key_file else ''},
         'shell':'zsh','image':'localhost/agent-container:dev','unlock':{'method':'manual'},
         'buildCA':'','githubSecret':args.github_secret}
    runtime=c.Runtime(cfg)
    original_call=runtime.call
    def diagnostic_call(*command, **kwargs):
        if any(tool in command for tool in ('setup-home', '/usr/local/bin/nested-podman-setup.sh')):
            requested=kwargs.pop('check', True)
            result=original_call(*command, check=False, **kwargs)
            if requested and result.returncode:
                print(result.stderr.decode(errors='replace')[-4000:], file=sys.stderr)
                raise AssertionError('Runtime setup failed')
            return result
        return original_call(*command, **kwargs)
    runtime.call=diagnostic_call
    ready=[]
    extra_runtimes=[]
    import_directory=tempfile.TemporaryDirectory(prefix='encrypted-import-smoke-')
    def passed(message):
        ready.append(message); print('PASS '+message,flush=True)
    def exec_with(env,*command,timeout=60,check=True,data=None):
        with c.env_file(env) as path:
            return runtime.call('exec','-i','--env-file',path,'--workdir','/home/dev/dev',runtime.container,
                                *command,timeout=timeout,check=check,data=data)
    try:
        runtime.ensure_machine()
        with c.proxy_snapshot(args.proxy_config) as (module, proxy, client):
            with patch('cli.read_passphrase',return_value=b'disposable-smoke-passphrase\n'):
                env=runtime.prepare(proxy,module,initialise=True)
            passed('create, authenticated TLS/nonce, encrypted init/mount, nested runtime setup')
            # Fail closed on wrong capability and stale boot nonce, without modifying live state.
            path=Path(proxy['sharedDir'])
            payload={'capability':'0'*48, 'ca':(path/'ca.pem').read_text(), 'ready':json.loads((path/'ready.json').read_bytes())}
            assert runtime.call('exec','-i',runtime.container,'python3','-c',c.PROBE,data=json.dumps(payload).encode(),check=False).returncode != 0
            payload['capability']=(path/'proxy-token').read_text().strip(); payload['ready']['instance']='stale-boot'
            assert runtime.call('exec','-i',runtime.container,'python3','-c',c.PROBE,data=json.dumps(payload).encode(),check=False).returncode != 0
            passed('wrong capability and stale nonce rejected')
            result=exec_with(env,'python3','-c',"import socket,sys\ns=socket.socket(); s.settimeout(3)\ntry: s.connect(('1.1.1.1',443))\nexcept OSError: sys.exit(0)\nsys.exit(1)")
            assert result.returncode==0
            passed('direct external TCP egress blocked')
            exec_with(env,'sh','-c','pi --version && node --version && uv --version && nvim --headless +qa && gh --version && aws --version && zsh -ic true')
            passed('bundled tools and interactive shell start')
            exec_with(env,'sh','-c','printf persistent > persistence.txt; printf "# user override\n" > .agent-container/zshrc.local')
            # Build a tiny nested image without network or external registry dependencies.
            exec_with(env,'sh','-c','mkdir -p /tmp/smoke-build; cp /bin/busybox /tmp/smoke-build/busybox; printf \'FROM scratch\nCOPY busybox /busybox\nRUN ["/busybox","true"]\n\' > /tmp/smoke-build/Dockerfile; podman build -t localhost/smoke:fixture /tmp/smoke-build',timeout=120)
            exec_with(env,'podman','run','--rm','localhost/smoke:fixture','/busybox','true')
            blocked=exec_with(env,'podman','run','--rm','localhost/smoke:fixture','/busybox','wget','-T','3','-Y','off','-O','/dev/null','http://1.1.1.1',check=False)
            assert blocked.returncode != 0
            passed('nested build/run and nested direct-egress isolation')
            sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]; sock.close()
            exec_with(env,'podman','run','-d','--name','web','-p',str(port)+':8080','localhost/smoke:fixture','/busybox','httpd','-f','-p','8080')
            runtime.reconcile_ports()
            for _ in range(30):
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=1) as conn:
                        conn.sendall(b'GET / HTTP/1.0\r\n\r\n'); reply=conn.recv(1024)
                    if b'HTTP/' in reply: break
                except OSError: pass
                time.sleep(0.5)
            else: raise AssertionError('nested port not forwarded')
            passed('nested published TCP port reaches host loopback')
            if args.github_secret:
                exec_with(env,'gh','api','user','--jq','.login')
                exec_with(env,'git','ls-remote','https://github.com/TomPallister/secret-proxy.git','HEAD')
                passed('GitHub authenticated read and Git HTTPS')
            if cfg['git']['signingKey']:
                exec_with(env,'sh','-c','mkdir signing; cd signing; git init -q; git commit --allow-empty -m "test: disposable signing"',timeout=90)
                # Verify signature cryptographically using only the configured public key.
                command='cd signing; printf "%s %s\\n" "smoke@example.com" "$1" > /tmp/allowed; git -c gpg.ssh.allowedSignersFile=/tmp/allowed verify-commit HEAD'
                verified=exec_with(env,'sh','-c',command,'signing-check',cfg['git']['signingKey'],check=False)
                if verified.returncode:
                    raise AssertionError('Signature verification: '+verified.stderr.decode(errors='replace'))
                passed('proxy-backed signing and independent verification')
            if args.model:
                c.import_codex(runtime,proxy,module)
                provider,model=args.model.split('/',1)
                response=exec_with(env,'pi','--provider',provider,'--model',model,'--thinking','minimal','-p','--no-session','-ne','-ns','-np','-nc','-nt','Reply with exactly AGENT_CONTAINER_OK',timeout=120)
                assert b'AGENT_CONTAINER_OK' in response.stdout
                passed('sealed Pi auth and real model response')
            # Lock/unlock and recreate must keep encrypted data and user customisation.
            exec_with(env,'/usr/local/bin/nested-podman-shutdown.sh')
            exec_with(env,'sh','-c','cd /; fusermount -u /home/dev/dev')
            assert runtime.call('exec',runtime.container,'mountpoint','-q','/home/dev/dev',check=False).returncode != 0
            passed('explicit encrypted unmount')
            runtime.stop()
            runtime.owned(runtime.inspect('container',runtime.container))
            runtime.call('rm',runtime.container)
            with patch('cli.read_passphrase',return_value=b'disposable-smoke-passphrase\n'):
                env=runtime.prepare(proxy,module)
            assert exec_with(env,'cat','persistence.txt').stdout==b'persistent'
            assert exec_with(env,'cat','.agent-container/zshrc.local').stdout==b'# user override\n'
            passed('recreation preserves encrypted files and customisation')
            # Offline ciphertext import into another owned volume, never adoption.
            from volume_import import import_volume
            copied=c.Runtime(dict(cfg,name=cfg['name']+'-copy'),config_path=Path(import_directory.name)/'config.json')
            extra_runtimes.append(copied)
            try:
                import_volume(copied,runtime.volume,copied.config_path)
            except c.Error:
                assert copied.inspect('volume',copied.volume) is None
            else:
                raise AssertionError('Running source was accepted for import')
            runtime.stop()
            receipt=import_volume(copied,runtime.volume,copied.config_path)
            with patch('cli.read_passphrase',return_value=b'disposable-smoke-passphrase\n'):
                copied_env=copied.prepare(proxy,module)
            with c.env_file(copied_env) as env_path:
                result=copied.call('exec','--env-file',env_path,copied.container,'sh','-c',
                    'test "$(cat /home/dev/dev/persistence.txt)" = persistent && printf changed > /home/dev/dev/persistence.txt')
                assert result.returncode==0
            copied.stop()
            with patch('cli.read_passphrase',return_value=b'disposable-smoke-passphrase\n'):
                env=runtime.prepare(proxy,module)
            assert exec_with(env,'cat','persistence.txt').stdout==b'persistent'
            assert receipt['verification']['entries']>2
            passed('verified offline ciphertext import, unlock and independent original rollback data')
            # Shut off only this transport: no direct fallback must emerge.
            runtime.owned(runtime.inspect('container',runtime.relay)); runtime.call('stop',runtime.relay)
            assert exec_with(env,'curl','-fsS','--max-time','4','https://example.com',check=False).returncode != 0
            passed('proxy transport loss fails closed')
    finally:
        # All resources are unique to this test; never remove a mismatched owner.
        try: runtime.reconcile_ports(start=False)
        except Exception: pass
        for owned in [runtime,*extra_runtimes]:
            for kind,target in [('container',owned.container),('container',owned.relay),
                                ('volume',owned.volume),('network',owned.network),('network',owned.egress)]:
                info=owned.inspect(kind,target)
                if info:
                    owned.owned(info)
                    owned.call(kind,'rm',*(['-f'] if kind=='container' else []),target)
        import_directory.cleanup()
    print(json.dumps({'passed':ready,'scope':'disposable resources; no proxy lifecycle or existing workspace changes'}))


if __name__=='__main__': main()
