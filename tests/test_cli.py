import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli as c


def config():
    return {'version': 1, 'name': 'test', 'machine': 'agent-container', 'proxyConfig': '/example/client.json',
            'git': {'name': 'Example Engineer', 'email': 'engineer@example.com', 'signingKey': ''},
            'shell': 'zsh', 'image': 'localhost/agent-container:dev', 'unlock': {'method': 'manual'},
            'buildCA': '', 'githubSecret': 'GITHUB_TOKEN'}


def module():
    m = Mock()
    m.private_read.return_value = b'a'*48
    return m


class ConfigTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(c.validate_config(config()), config())

    def test_invalid_config(self):
        for key, value in [('name', '../escape'), ('machine', 'machine; rm'), ('image', '--privileged'),
                           ('shell', 'zsh;whoami'), ('proxyConfig', 'relative'), ('version', 2),
                           ('githubSecret', 'raw-key-value'), ('buildCA', 'relative')]:
            with self.subTest(key=key):
                cfg = config(); cfg[key] = value
                with self.assertRaises(c.Error): c.validate_config(cfg)
        cfg=config(); cfg['mounts']=['/Users:/host']
        with self.assertRaises(c.Error): c.validate_config(cfg)

    def test_no_raw_unlock_password(self):
        cfg=config(); cfg['unlock']['password']='example'
        with self.assertRaises(c.Error): c.validate_config(cfg)

    def test_1password_explicit_account(self):
        cfg=config(); cfg['unlock']={'method':'1password','reference':'op://Private/Workspace/password','account':'example.1password.com'}
        c.validate_config(cfg)
        del cfg['unlock']['account']
        with self.assertRaises(c.Error): c.validate_config(cfg)

    def test_identity_no_config_injection(self):
        cfg=config(); cfg['git']['name']='Example\n[credential]\nhelper=evil'
        with self.assertRaises(c.Error): c.validate_config(cfg)
        cfg=config(); cfg['git']['signingKey']='-----BEGIN PRIVATE KEY-----'
        with self.assertRaises(c.Error): c.validate_config(cfg)

    def test_private_read_refuses_symlinks_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config'
            c.save_new(path,b'{}')
            self.assertEqual(c.private_read(path),b'{}')
            path.chmod(0o644)
            with self.assertRaises(c.Error): c.private_read(path)
            link=Path(temp)/'link'; link.symlink_to(path)
            with self.assertRaises(OSError): c.private_read(link)

    def test_exclusive_save(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config'; c.save_new(path,b'original')
            with self.assertRaises(FileExistsError): c.save_new(path,b'new')
            self.assertEqual(path.read_bytes(),b'original')

    def test_environment_does_not_inherit_host_secrets(self):
        with patch.dict(os.environ,{'MY_PASSWORD':'not-forwarded','AWS_ACCESS_KEY_ID':'not-forwarded', 'HTTPS_PROXY':'stale'}):
            env=c.container_environment(config(),{'sharedDir':'/unused'},module())
        self.assertNotIn('MY_PASSWORD',env); self.assertNotIn('AWS_ACCESS_KEY_ID',env)
        self.assertEqual(env['GH_TOKEN'],c.REF+'GITHUB_TOKEN')
        self.assertEqual(env['GIT_CONFIG_COUNT'],'4')
        self.assertEqual(env['HTTPS_PROXY'],'http://secret-proxy:'+'a'*48+'@secret-proxy:10256')

    def test_raw_credential_or_loader_refused(self):
        for key,value in [('API_KEY','not-a-reference'), ('AWS_SESSION_TOKEN','real-token'),
                          ('BASH_ENV','/bad'), ('PI_CODING_AGENT_DIR','/bad'), ('AGENT_CONTAINER_TEST','1')]:
            with self.subTest(key=key), self.assertRaises(c.Error):
                c.container_environment(config(),{'sharedDir':'/unused','environment':{key:value}},module())

    def test_dummy_aws_and_refs_accepted(self):
        env={'AWS_ACCESS_KEY_ID':'proxy-dummy-id','AWS_SECRET_ACCESS_KEY':'proxy-dummy-secret',
             'AWS_SESSION_TOKEN':'secretrole:123456789012/readonly','OPENAI_API_KEY':c.REF+'OPENAI_API_KEY'}
        result=c.container_environment(config(),{'sharedDir':'/unused','environment':env},module())
        for key,value in env.items(): self.assertEqual(result[key],value)

    def test_trust_overrides_client_settings(self):
        result=c.container_environment(config(),{'sharedDir':'/unused','environment':{'SSL_CERT_FILE':'/bad'}},module())
        self.assertEqual(result['SSL_CERT_FILE'],c.BUNDLE)

    def test_proxy_capability_validation(self):
        m=module(); m.private_read.return_value=b'wrong'
        with self.assertRaises(c.Error): c.container_environment(config(),{'sharedDir':'/unused'},m)

    def test_signing_is_explicit_and_proxy_only(self):
        cfg=config(); cfg['git']['signingKey']='ssh-ed25519 '+('A'*68)
        env=c.container_environment(cfg,{'sharedDir':'/unused'},module())
        settings=dict((env['GIT_CONFIG_KEY_'+str(i)],env['GIT_CONFIG_VALUE_'+str(i)]) for i in range(int(env['GIT_CONFIG_COUNT'])))
        self.assertEqual(settings['commit.gpgsign'],'true')
        self.assertEqual(settings['gpg.ssh.program'],'/usr/local/bin/git-ssh-sign-proxy.sh')

    def test_env_file_is_private_and_ephemeral(self):
        with c.env_file({'EXAMPLE':'value'}) as path:
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(path.read_text(),'EXAMPLE=value\n')
            saved=path
        self.assertFalse(saved.exists())
        with self.assertRaises(c.Error):
            with c.env_file({'EXAMPLE':'value\nEXTRA=bad'}): pass

    def test_proxy_snapshot_checks_copied_nonce(self):
        with tempfile.TemporaryDirectory() as temp:
            shared=Path(temp)/'shared'; shared.mkdir()
            data={'proxy-token':b'a'*48, 'ca.pem':b'public-ca', 'ready.json':b'{"status":"ready","instance":"boot"}', 'bundle.pem':b'public-bundle'}
            for filename,value in data.items(): (shared/filename).write_bytes(value)
            selected=Path(temp)/'client.json'; selected.write_text(json.dumps({'sharedDir':str(shared),'port':10443}))
            m=Mock(); m.private_read.side_effect=lambda p,limit: Path(p).read_bytes()
            m.validate_config.side_effect=lambda cfg:cfg
            with c.proxy_snapshot(selected,m) as (_,snapshot,_):
                self.assertNotEqual(snapshot['sharedDir'],str(shared))
                self.assertEqual((Path(snapshot['sharedDir'])/'bundle.pem').read_bytes(),b'public-bundle')
                self.assertEqual(m.ProxyClient.return_value.check_ready.call_count,2)
            self.assertFalse(Path(snapshot['sharedDir']).exists())

    def test_stale_snapshot_refused(self):
        m=Mock(); m.private_read.return_value=b'{"sharedDir":"/unused","port":10443}'
        m.validate_config.side_effect=lambda cfg:cfg
        m.ProxyClient.return_value.check_ready.side_effect=RuntimeError('stale')
        with self.assertRaises(RuntimeError):
            with c.proxy_snapshot('/unused',m): pass

    def test_op_uses_stdin_only_for_mount_and_no_ambient_service_token(self):
        with patch('cli.shutil.which',return_value='/bin/op'), patch('cli.run',return_value=Mock(stdout=b'example-password\n')) as command:
            value=c.read_passphrase({'method':'1password','account':'example.1password.com','reference':'op://Private/Workspace/password'})
        self.assertEqual(value,b'example-password\n')
        self.assertIn('--account',command.call_args.args[0])
        self.assertNotIn('example-password',repr(command.call_args))
        with patch.dict(os.environ,{'OP_SERVICE_ACCOUNT_TOKEN':'not-forwarded','LD_PRELOAD':'bad'}):
            env=c.clean_host_env()
        self.assertNotIn('OP_SERVICE_ACCOUNT_TOKEN',env); self.assertNotIn('LD_PRELOAD',env)


class RuntimeTests(unittest.TestCase):
    def runtime(self):
        return c.Runtime(config(),sys.executable)

    def test_source_hash_uses_git_executable_bit_not_checkout_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); image=root/'image'; image.mkdir(); file=image/'example'
            file.write_text('public code'); file.chmod(0o644)
            with patch.object(c,'ROOT',root):
                original=c.source_hash(); file.chmod(0o600)
                self.assertEqual(c.source_hash(),original)
                file.chmod(0o700)
                self.assertNotEqual(c.source_hash(),original)

    def test_public_defaults_are_readable_independent_of_host_umask(self):
        dockerfile=(c.ROOT/'image/Dockerfile').read_text()
        self.assertIn('chmod -R a+rX,go-w /opt/agent-container-home',dockerfile)
        self.assertIn('chmod 0644 package.json package-lock.json',dockerfile)

    def test_creation_contract(self):
        runtime=self.runtime(); args=runtime.create_command()
        self.assertEqual(args[args.index('--network')+1],runtime.network)
        self.assertIn('--cap-drop',args); self.assertIn('ALL',args)
        self.assertIn('no-new-privileges',args)
        self.assertIn('--userns=keep-id:uid=1000,gid=1000',args)
        self.assertNotIn('--privileged',args); self.assertNotIn('--net=host',args)
        self.assertEqual(args.count('-v'),1)
        self.assertEqual(args[args.index('-v')+1],'ac-test-crypt:/home/dev/.devcipher')
        self.assertNotIn('secret-proxy:',repr(args))

    def topology(self):
        r=self.runtime()
        return {'Config':{'User':'dev','Labels':{c.LABEL+'.instance':'test'}},'HostConfig':{'Privileged':False},
                'NetworkSettings':{'Networks':{r.network:{}}},
                'Mounts':[{'Type':'volume','Name':r.volume,'Destination':'/home/dev/.devcipher'}]}

    def test_topology_refuses_extra_network_and_host_mount(self):
        r=self.runtime(); info=self.topology(); r.check_topology(info)
        info['NetworkSettings']['Networks']['external']={}
        with self.assertRaises(c.Error): r.check_topology(info)
        info=self.topology(); info['Mounts'].append({'Type':'bind','Source':'/Users'})
        with self.assertRaises(c.Error): r.check_topology(info)
        info=self.topology(); info['Config']['User']='root'
        with self.assertRaises(c.Error): r.check_topology(info)

    def test_foreign_resource_refused(self):
        with self.assertRaises(c.Error): self.runtime().owned({'Labels':{}})

    def test_engine_failure_not_treated_as_absent(self):
        r=self.runtime()
        with patch.object(r,'call',return_value=Mock(returncode=125,stderr=b'engine failed')):
            with self.assertRaises(c.Error): r.inspect('container','example')

    def test_network_internal_checked(self):
        r=self.runtime()
        with patch.object(r,'inspect',return_value={'Labels':{c.LABEL+'.instance':'test'},'internal':False}):
            with self.assertRaises(c.Error): r.ensure_network(r.network,True)

    def test_no_implicit_init(self):
        r=self.runtime()
        with patch.object(r,'call',return_value=Mock(returncode=1)), patch('cli.read_passphrase') as password:
            with self.assertRaises(c.Error): r.unlock()
            password.assert_not_called()

    def test_mount_passphrase_in_stdin_not_argv(self):
        r=self.runtime()
        with patch.object(r,'call',side_effect=[Mock(returncode=1),Mock(returncode=0),Mock(returncode=0)]) as call, patch('cli.read_passphrase',return_value=b'example-password\n'):
            r.unlock()
        self.assertEqual(call.call_args.kwargs['data'],b'example-password\n')
        self.assertNotIn('example-password',repr(call.call_args.args))

    def test_relay_is_only_transport_no_capability_in_argv(self):
        r=self.runtime()
        with patch.object(r,'ensure_network'), patch.object(r,'inspect',return_value=None), patch.object(r,'call') as call:
            r.ensure_relay({'port':10443})
        args=call.call_args.args
        self.assertIn('TCP:host.containers.internal:10443',args)
        self.assertIn('--read-only',args); self.assertIn('--cap-drop',args)
        self.assertNotIn('--privileged',args)
        self.assertNotIn('-p',args)

    def test_stale_relay_image_requires_explicit_recreation(self):
        r=self.runtime()
        info={'Config':{'Labels':{c.LABEL+'.instance':'test'}},'Image':'old'}
        with patch.object(r,'ensure_network'), patch.object(r,'inspect',side_effect=[info,{'Id':'new'}]), patch.object(r,'call') as call:
            with self.assertRaisesRegex(c.Error,'Relay image changed'):
                r.ensure_relay({'port':10443})
            call.assert_not_called()

    def test_recreation_removes_only_owned_container_and_relay_not_volume(self):
        r=self.runtime()
        relay={'Config':{'Labels':{c.LABEL+'.instance':'test'}},'NetworkSettings':{'Networks':{r.network:{},r.egress:{}}}}
        with patch.object(r,'inspect',side_effect=[self.topology(),relay]), patch('builtins.input',return_value='recreate'), patch.object(r,'stop'), patch.object(r,'call') as call:
            r.recreate()
        self.assertEqual([x.args for x in call.call_args_list],[('rm',r.container),('rm','-f',r.relay)])
        relay['Config']['Labels']={}
        with patch.object(r,'inspect',side_effect=[self.topology(),relay]), patch('builtins.input',return_value='recreate'), patch.object(r,'stop') as stop, patch.object(r,'call') as call:
            with self.assertRaises(c.Error): r.recreate()
            stop.assert_not_called(); call.assert_not_called()

    def test_helper_restricts_destination(self):
        script=c.ROOT/'image/runtime/git-credential-proxy.py'
        env={'AGENT_CONTAINER_GITHUB_SECRET':'GITHUB_TOKEN'}
        good=subprocess.run([sys.executable,str(script),'get'],input=b'protocol=https\nhost=github.com\n',capture_output=True,env=env)
        self.assertIn((c.REF+'GITHUB_TOKEN').encode(),good.stdout)
        bad=subprocess.run([sys.executable,str(script),'get'],input=b'protocol=https\nhost=example.com\n',capture_output=True,env=env)
        self.assertEqual(bad.stdout,b'')
        store=subprocess.run([sys.executable,str(script),'store'],input=b'password=do-not-store\n',capture_output=True,env=env)
        self.assertEqual(store.stdout,b'')

    def test_sealed_auth_only(self):
        r=self.runtime(); m=Mock()
        m.private_read.return_value=b'{"openai-codex":{"type":"oauth"},"other":{"key":"never-copy"}}'
        m.validate_credential.return_value={'type':'oauth','access':'sealed-fixture'}
        with patch.object(r,'call') as call:
            c.import_codex(r,{'agentDir':'/example'},m)
        self.assertNotIn(b'never-copy',call.call_args.kwargs['data'])
        m.validate_credential.assert_called_once_with({'type':'oauth'})
        m.validate_credential.side_effect=ValueError('unsealed')
        with patch.object(r,'call') as call:
            with self.assertRaises(ValueError): c.import_codex(r,{'agentDir':'/example'},m)
            call.assert_not_called()


if __name__ == '__main__': unittest.main()
