"""Opinionated macOS development container. No proxy lifecycle or vault policy here."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path.home() / '.config/agent-container/config.json'
LABEL = 'io.github.agent-container'
REF = 'secret' + 'ref:'
CA_PATH = '/usr/local/share/ca-certificates/secret-proxy-ca.crt'
BUNDLE = '/etc/ssl/certs/ca-certificates.crt'
CAPS = ('SYS_ADMIN', 'SYS_CHROOT', 'SETUID', 'SETGID', 'CHOWN', 'DAC_OVERRIDE', 'FOWNER')


class Error(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Error(message)


def text(value, name, limit=512):
    require(isinstance(value, str) and 0 < len(value) <= limit and
            not any(ord(c) < 32 or ord(c) == 127 for c in value), 'Invalid ' + name)
    return value


def name(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,39}', value), 'Invalid instance name')
    return value


def private_read(path, limit=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                info.st_mode & 0o077 == 0 and info.st_size <= limit, 'Settings must be an owner-only regular file')
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def save_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def validate_config(value):
    keys = {'version', 'name', 'machine', 'proxyConfig', 'git', 'shell', 'image', 'unlock', 'buildCA', 'githubSecret'}
    optional = {'environment', 'githubOwners', 'githubCopilotSecret'}
    require(isinstance(value, dict) and keys <= set(value) and set(value) <= keys | optional and value['version'] == 1, 'Invalid settings schema')
    environment = value.get('environment', {})
    require(isinstance(environment, dict) and len(environment) <= 128, 'Invalid client environment')
    for key, setting in environment.items():
        require(isinstance(key, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', key), 'Invalid environment key')
        text(setting, 'environment value', 8192)
    owners = value.get('githubOwners', {})
    require(isinstance(owners, dict) and len(owners) <= 64, 'Invalid GitHub owner routing')
    for owner, canonical in owners.items():
        require(isinstance(owner, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,38}', owner), 'Invalid GitHub owner')
        require(isinstance(canonical, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', canonical), 'Invalid GitHub reference name')
    copilot = value.get('githubCopilotSecret', '')
    require(isinstance(copilot, str) and (not copilot or re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', copilot)), 'Invalid Copilot reference name')
    name(value['name']); name(value['machine'])
    require(value['shell'] in ('zsh', 'bash'), 'Choose zsh or bash')
    text(value['proxyConfig'], 'proxyConfig')
    require(Path(value['proxyConfig']).is_absolute(), 'proxyConfig must be absolute')
    require(isinstance(value['git'], dict) and set(value['git']) == {'name', 'email', 'signingKey'}, 'Invalid Git settings')
    text(value['git']['name'], 'Git name'); text(value['git']['email'], 'Git email')
    require(re.fullmatch(r'[^\s<>@]+@[^\s<>@]+', value['git']['email']), 'Invalid Git email')
    key = value['git']['signingKey']
    require(isinstance(key, str) and (not key or re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,2}(?: [\w.@-]+)?', key)), 'Expected an Ed25519 public signing key, not a private key')
    require(isinstance(value['image'], str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9./:@_-]{0,255}', value['image']), 'Invalid image name')
    require(isinstance(value['buildCA'], str) and (not value['buildCA'] or Path(text(value['buildCA'], 'build CA')).is_absolute()), 'buildCA must be an absolute public certificate path')
    secret = value['githubSecret']
    require(isinstance(secret, str) and (not secret or re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', secret)), 'Invalid GitHub secret reference name')
    unlock = value['unlock']
    require(isinstance(unlock, dict) and set(unlock) in ({'method'}, {'method', 'account', 'reference'}), 'Invalid unlock settings')
    if unlock.get('method') == 'manual':
        require(set(unlock) == {'method'}, 'Manual unlock must not contain credentials')
    else:
        require(unlock.get('method') == '1password' and set(unlock) == {'method', 'account', 'reference'}, 'Unknown unlock method')
        require(re.fullmatch(r'[a-zA-Z0-9.-]+\.1password\.(com|eu|ca)', text(unlock['account'], '1Password account')), 'Invalid 1Password account')
        require(re.fullmatch(r'op://[^/\r\n]+/[^/\r\n]+/[^/\r\n]+', text(unlock['reference'], '1Password reference')), 'Invalid op reference')
    return value


def clean_host_env():
    # Keep CLI prerequisites, not ambient model/API credentials or loader hooks.
    allowed = ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'TERM', 'SSH_AUTH_SOCK')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env['CONTAINERS_MACHINE_PROVIDER'] = 'libkrun'
    return env


def run(command, *, data=None, capture=True, timeout=60, env=None, check=True):
    result = subprocess.run([str(x) for x in command], input=data,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None,
                            env=clean_host_env() if env is None else env, timeout=timeout)
    if check and result.returncode:
        # Captured output can contain proxy URLs or decrypted init diagnostics.
        raise Error('Command failed: ' + Path(str(command[0])).name + ' (exit ' + str(result.returncode) + ')')
    return result


def installed_client():
    path = Path('/opt/secret-proxy/current/client.py')
    require(path.is_file(), 'Install secret-proxy first: https://github.com/TomPallister/secret-proxy')
    for entry in (path, path.parent, path.parent.resolve().parent):
        info = entry.stat()
        require(info.st_uid == 0 and not info.st_mode & 0o022, 'Installed proxy client must be root-owned and not group/world-writable')
    spec = importlib.util.spec_from_file_location('agent_container_proxy_client', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def proxy_snapshot(config_path, module=None):
    module = module or installed_client()
    selected = module.validate_config(json.loads(module.private_read(Path(config_path), 65536)))
    module.ProxyClient(selected).check_ready()
    with tempfile.TemporaryDirectory(prefix='agent-container-proxy-') as directory:
        path = Path(directory)
        for filename, limit in [('proxy-token', 49), ('ca.pem', 131072), ('ready.json', 4096)]:
            save_new(path / filename, module.private_read(Path(selected['sharedDir']) / filename, limit))
        bundle = Path(selected['sharedDir']) / 'bundle.pem'
        if bundle.exists():
            save_new(path / 'bundle.pem', module.private_read(bundle, 2 * 1024 * 1024))
        snapshot = dict(selected, sharedDir=str(path))
        client = module.ProxyClient(snapshot)
        client.check_ready()  # authenticate this exact snapshot, not just its source
        yield module, snapshot, client


PROBE = '''import base64,http.client,json,ssl,sys
try:
 s=json.load(sys.stdin)
 c=http.client.HTTPSConnection('secret-proxy',10256,context=ssl.create_default_context(cadata=s['ca']),timeout=15)
 a=base64.b64encode(('secret-proxy:'+s['capability']).encode()).decode()
 c.set_tunnel('secret'+'ref.invalid',443,headers={'Proxy-Authorization':'Basic '+a})
 c.request('GET','/health'); r=c.getresponse(); raw=r.read(4097)
 ok=r.status==200 and len(raw)<=4096 and json.loads(raw)==s['ready']; c.close()
 sys.exit(0 if ok else 2)
except Exception: sys.exit(2)
'''


def container_environment(config, proxy, module):
    env = dict(proxy.get('environment', {}))  # explicitly configured references/settings only
    env.update(config.get('environment', {}))
    env.pop('ALL_PROXY', None)
    env.pop('all_proxy', None)
    # Never inherit ambient host environment. Protect trust, identity and loader settings.
    denied = {'HOME', 'PATH', 'ENV', 'BASH_ENV', 'ZDOTDIR', 'NODE_OPTIONS', 'PYTHONPATH', 'PYTHONHOME', 'SSLKEYLOGFILE',
              'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE',
              'NODE_EXTRA_CA_CERTS', 'NODE_USE_ENV_PROXY', 'GIT_SSL_CAINFO', 'AWS_CA_BUNDLE'}
    for key in config.get('environment', {}):
        require(key not in denied, 'Unsafe client environment setting: ' + key)
    transport = {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE',
                 'NODE_EXTRA_CA_CERTS', 'NODE_USE_ENV_PROXY', 'GIT_SSL_CAINFO', 'AWS_CA_BUNDLE'}
    for key, value in env.items():
        require(key not in denied - transport and not key.startswith(('LD_', 'DYLD_', 'OP_', 'PI_', 'GIT_CONFIG', 'AGENT_CONTAINER_')),
                'Unsafe proxy client environment setting: ' + key)
        require('\n' not in value and '\r' not in value, 'Multiline client environment value')
        # Credential-shaped names must use references or explicit AWS dummy selectors.
        if re.search(r'(TOKEN|SECRET|PASSWORD|API_?KEY|ACCESS_KEY)', key):
            require(re.fullmatch(re.escape(REF) + r'[A-Z][A-Z0-9_]{0,127}', value) or (key == 'AWS_SESSION_TOKEN' and re.fullmatch(r'secretrole:[0-9]{12}/[\w+=,.@-]+', value))
                    or (key in {'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'} and 'dummy' in value.lower()),
                    'Raw credential configuration is not accepted: ' + key)
    capability = module.private_read(Path(proxy['sharedDir']) / 'proxy-token', 49).decode().strip()
    require(re.fullmatch(r'[a-f0-9]{48}', capability), 'Invalid proxy capability')
    url = 'http://secret-proxy:' + capability + '@secret-proxy:10256'
    env.update(HTTP_PROXY=url, HTTPS_PROXY=url, http_proxy=url, https_proxy=url,
               NO_PROXY='localhost,127.0.0.1,::1', no_proxy='localhost,127.0.0.1,::1',
               NODE_USE_ENV_PROXY='1', NODE_EXTRA_CA_CERTS=BUNDLE, NODE_USE_SYSTEM_CA='1',
               SSL_CERT_FILE=BUNDLE, REQUESTS_CA_BUNDLE=BUNDLE, CURL_CA_BUNDLE=BUNDLE,
               AWS_CA_BUNDLE=BUNDLE, GIT_SSL_CAINFO=BUNDLE,
               XDG_RUNTIME_DIR='/tmp/agent-container-podman',
               PI_CODING_AGENT_DIR='/home/dev/dev/.agent-container/pi',
               PI_CODING_AGENT_SESSION_DIR='/home/dev/dev/.agent-container/pi/sessions',
               PI_TELEMETRY='0', PI_SKIP_VERSION_CHECK='1',
               GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='/bin/true')
    settings = [('core.hooksPath', '/dev/null')]
    if config['githubSecret'] or config.get('githubOwners') or config.get('githubCopilotSecret'):
        env['AGENT_CONTAINER_GITHUB_SECRET'] = config['githubSecret']
        if config.get('githubOwners') or config.get('githubCopilotSecret'):
            env.pop('GH_TOKEN', None)
            env['AGENT_CONTAINER_GITHUB_OWNERS'] = json.dumps(config.get('githubOwners', {}))
            env['AGENT_CONTAINER_GITHUB_COPILOT'] = config.get('githubCopilotSecret', '')
        else:
            env['GH_TOKEN'] = REF + config['githubSecret']
        env.pop('GITHUB_TOKEN', None)
        settings.extend([('credential.https://github.com.helper', ''),
                         ('credential.https://github.com.helper', 'proxy'),
                         ('credential.https://github.com.useHttpPath', 'true')])
    if config['git']['signingKey']:
        settings.extend([('user.signingkey', 'key::' + config['git']['signingKey']), ('gpg.format', 'ssh'),
                         ('gpg.ssh.program', '/usr/local/bin/git-ssh-sign-proxy.sh'),
                         ('commit.gpgsign', 'true'), ('tag.gpgsign', 'true')])
    env['GIT_CONFIG_COUNT'] = str(len(settings))
    for index, (key, value) in enumerate(settings):
        env['GIT_CONFIG_KEY_' + str(index)] = key
        env['GIT_CONFIG_VALUE_' + str(index)] = value
    return env


@contextlib.contextmanager
def env_file(env):
    with tempfile.TemporaryDirectory(prefix='agent-container-env-') as directory:
        path = Path(directory) / 'environment'
        require(all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) and '\n' not in v and '\r' not in v for k, v in env.items()), 'Invalid environment file')
        save_new(path, ''.join(k + '=' + v + '\n' for k, v in env.items()).encode())
        yield path


class Runtime:
    def __init__(self, config, executable=None, config_path=None):
        self.config = validate_config(config)
        self.config_path = Path(config_path or DEFAULT_CONFIG)
        self.engine = executable or shutil.which('podman') or '/opt/podman/bin/podman'
        require(Path(self.engine).is_absolute() and os.access(self.engine, os.X_OK), 'Install Podman with libkrun/krunkit first')
        self.container = 'ac-' + config['name']
        self.network = self.container + '-internal'
        self.egress = self.container + '-transport'
        self.relay = self.container + '-proxy-relay'
        self.volume = self.container + '-crypt'
        self.label = LABEL + '.instance=' + config['name']

    def command(self, *args):
        return [self.engine, '--connection', self.config['machine'], *args]

    def call(self, *args, **kwargs):
        return run(self.command(*args), **kwargs)

    def inspect(self, kind, target):
        result = self.call(kind, 'exists', target, check=False)
        if result.returncode == 1 and not result.stderr:
            return None
        require(result.returncode == 0, 'Cannot check ' + kind + ' state')
        value = json.loads(self.call(kind, 'inspect', target).stdout)
        require(isinstance(value, list) and len(value) == 1, 'Ambiguous runtime state')
        return value[0]

    def owned(self, info):
        labels = info.get('Labels') or info.get('labels') or info.get('Config', {}).get('Labels') or {}
        require(labels.get(LABEL + '.instance') == self.config['name'], 'Existing resource is not owned by this instance; refusing to change it')

    def ensure_machine(self):
        require(sys.platform == 'darwin', 'This preview supports macOS only; Linux/WSL host support is not yet validated')
        result = run([self.engine, 'machine', 'list', '--format', 'json'])
        machines = json.loads(result.stdout)
        selected = [m for m in machines if m['Name'] == self.config['machine']]
        if not selected:
            run([self.engine, 'machine', 'init', '--cpus', '4', '--memory', '6144', '--disk-size', '100', '--volume', '', self.config['machine']], capture=False, timeout=600)
        # machine inspect does not expose shares. Fail closed if schema/location changes.
        machine_file = Path.home() / '.config/containers/podman/machine/libkrun' / (self.config['machine'] + '.json')
        state = json.loads(machine_file.read_bytes())
        require('Mounts' in state and not state['Mounts'], 'Development machine must have no host directory shares; never recreate it automatically')
        info = json.loads(run([self.engine, 'machine', 'inspect', self.config['machine']]).stdout)[0]
        if info['State'] != 'running':
            run([self.engine, 'machine', 'start', self.config['machine']], capture=False, timeout=180)
        rootless = self.call('info', '--format', '{{.Host.Security.Rootless}}').stdout.strip()
        require(rootless == b'true', 'A rootless Podman connection is required')
        # Do not change the user's default connection.

    def build(self):
        self.ensure_machine()
        require(self.config['image'] == 'localhost/agent-container:dev', 'Custom images must be built explicitly; build never overwrites them')
        args = ['build', '--http-proxy=false', '--label', LABEL + '.source=' + source_hash(), '-t', self.config['image']]
        if self.config['buildCA']:
            path = Path(self.config['buildCA'])
            require(path.is_file(), 'Configured build CA is missing')
            args += ['--secret', 'id=upstream_ca,src=' + str(path)]
        # Remote Podman stages --secret beside the context; use a private copy.
        with tempfile.TemporaryDirectory(prefix='agent-container-build-') as directory:
            shutil.copytree(ROOT / 'image', Path(directory) / 'image')
            self.call(*args, str(Path(directory) / 'image'), capture=False, timeout=1800)

    def ensure_network(self, target, internal):
        info = self.inspect('network', target)
        if info is None:
            self.call('network', 'create', '--label', self.label, *(['--internal'] if internal else []), target)
            info = self.inspect('network', target)
        self.owned(info)
        require(info.get('internal') is internal, 'Network isolation differs; refusing to continue')

    def create_command(self):
        args = ['run', '-d', '--name', self.container, '--hostname', self.container, '--init',
                '--label', self.label, '--label', LABEL + '.contract=1',
                '--userns=keep-id:uid=1000,gid=1000', '--network', self.network,
                '--device', '/dev/fuse', '--device', '/dev/net/tun', '--cap-drop', 'ALL']
        for cap in CAPS:
            args += ['--cap-add', cap]
        args += ['--security-opt', 'no-new-privileges', '--security-opt', 'label=disable',
                 '--security-opt', 'apparmor=unconfined', '--security-opt', 'unmask=/proc/*',
                 '--tmpfs', '/tmp:rw,nosuid,nodev,size=512m,mode=1777,notmpcopyup',
                 '-v', self.volume + ':/home/dev/.devcipher', self.config['image'],
                 '/usr/local/bin/nested-podman-keeper.sh']
        return args

    def check_topology(self, info):
        self.owned(info)
        require(set(info.get('NetworkSettings', {}).get('Networks', {})) == {self.network}, 'Container has unexpected networks')
        mounts = [m for m in info.get('Mounts', []) if m.get('Type') != 'tmpfs']
        require(len(mounts) == 1 and mounts[0].get('Type') == 'volume' and mounts[0].get('Name') == self.volume
                and mounts[0].get('Destination') == '/home/dev/.devcipher', 'Container has unexpected mounts')
        require(not info['HostConfig'].get('Privileged') and info['Config']['User'] in ('dev', '1000:1000'), 'Unexpected container privilege/user')

    def ensure_container(self):
        image = self.inspect('image', self.config['image'])
        require(image is not None, 'Image not built; run agent-container build')
        if self.config['image'] == 'localhost/agent-container:dev':
            require(image.get('Labels', {}).get(LABEL + '.source') == source_hash(), 'Image does not match this source; run agent-container build, then recreate explicitly')
        self.ensure_network(self.network, True)
        volume = self.inspect('volume', self.volume)
        if volume is None:
            self.call('volume', 'create', '--label', self.label, self.volume)
        else:
            self.owned(volume)
            from volume_import import check_import
            check_import(self, volume, self.config_path)
        info = self.inspect('container', self.container)
        if info is None:
            self.call(*self.create_command())
            self.call('exec', '--user', 'root', self.container, 'sh', '-c',
                      'mkdir -p /home/dev/dev /home/dev/.devcipher && chown dev:dev /home/dev/dev /home/dev/.devcipher')
        else:
            self.check_topology(info)
            require(info['Image'].removeprefix('sha256:') == image['Id'].removeprefix('sha256:'), 'New image available: run agent-container recreate explicitly')
            if not info['State']['Running']:
                self.call('start', self.container)
        self.call('exec', '--user', 'root', self.container, 'mount', '--make-rshared', '/')
        self.call('exec', self.container, 'sh', '-c',
                  'for i in $(seq 1 50); do pgrep -fx "/bin/sleep infinity" >/dev/null && exit 0; sleep 0.2; done; exit 1')
        self.check_topology(self.inspect('container', self.container))

    def ensure_relay(self, proxy):
        self.ensure_network(self.egress, False)
        target = 'host.containers.internal:' + str(proxy['port'])
        info = self.inspect('container', self.relay)
        if info:
            self.owned(info)
            image = self.inspect('image', self.config['image'])
            require(info['Image'].removeprefix('sha256:') == image['Id'].removeprefix('sha256:'), 'Relay image changed; run agent-container recreate explicitly')
            require(info['Config']['Labels'].get(LABEL + '.target') == target and
                    set(info['NetworkSettings']['Networks']) == {self.network, self.egress}, 'Relay configuration changed; remove this instance relay explicitly before reconnecting')
            if not info['State']['Running']:
                self.call('start', self.relay)
            return
        self.call('run', '-d', '--name', self.relay, '--label', self.label,
                  '--label', LABEL + '.target=' + target, '--network', self.egress,
                  '--network', self.network + ':alias=secret-proxy', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges', '--read-only', '--pids-limit', '64',
                  '--memory', '64m', '--cpus', '0.5',
                  '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=1m,notmpcopyup',
                  '--entrypoint', '/usr/bin/socat', self.config['image'],
                  'TCP-LISTEN:10256,fork,reuseaddr', 'TCP:' + target)

    def connect(self, proxy, module):
        self.ensure_relay(proxy)
        path = Path(proxy['sharedDir'])
        payload = {'capability': module.private_read(path/'proxy-token', 49).decode().strip(),
                   'ca': module.private_read(path/'ca.pem', 131072).decode(),
                   'ready': json.loads(module.private_read(path/'ready.json', 4096))}
        self.call('exec', '-i', self.container, 'python3', '-c', PROBE, data=json.dumps(payload).encode(), timeout=25)
        # A public trust bundle may include corporate roots for passthrough hosts.
        trust = module.private_read(path/'bundle.pem', 2 * 1024 * 1024) if (path/'bundle.pem').exists() else payload['ca'].encode()
        self.call('exec', '-i', '--user', 'root', self.container, 'sh', '-c',
                  'umask 022; cat > ' + CA_PATH + ' && update-ca-certificates >/dev/null', data=trust)

    def unlock(self, initialise=False):
        if self.call('exec', self.container, 'mountpoint', '-q', '/home/dev/dev', check=False).returncode == 0:
            return
        existing = self.call('exec', self.container, 'test', '-f', '/home/dev/.devcipher/gocryptfs.conf', check=False)
        require(existing.returncode in (0, 1), 'Cannot inspect encrypted volume')
        require(existing.returncode == 0 or initialise, 'Workspace is not initialised; run agent-container init')
        passphrase = read_passphrase(self.config['unlock'], confirm=existing.returncode != 0)
        try:
            if existing.returncode != 0:
                self.call('exec', '-i', self.container, '/usr/local/bin/mount-devcrypt.sh', 'init', data=passphrase, timeout=90)
            self.call('exec', '-i', self.container, '/usr/local/bin/mount-devcrypt.sh', 'mount', data=passphrase, timeout=90)
        finally:
            passphrase = b''

    def prepare(self, proxy, module, initialise=False):
        self.ensure_container()
        self.connect(proxy, module)
        self.unlock(initialise)
        self.call('exec', '-i', self.container, 'setup-home', data=json.dumps(self.config['git']).encode())
        self.call('exec', self.container, 'sh', '-c', 'mkdir -p /tmp/agent-container; chmod 700 /tmp/agent-container; date +%s > /tmp/agent-container/unlock-ts')
        env = container_environment(self.config, proxy, module)
        with env_file(env) as path:
            self.call('exec', '--env-file', path, self.container, '/usr/local/bin/nested-podman-setup.sh', timeout=90)
        return env

    def stop(self):
        info = self.inspect('container', self.container)
        if info:
            self.check_topology(info)
            self.call('stop', '--time', '30', self.container, timeout=45)
        self.reconcile_ports(start=False)
        print('Stopped. Encrypted volume retained; secret-proxy untouched.')

    def recreate(self):
        info = self.inspect('container', self.container)
        if not info:
            print('No container to recreate; run agent-container.'); return
        self.check_topology(info)
        require(input('Stops all processes and discards files OUTSIDE ~/dev. Type recreate: ') == 'recreate', 'Cancelled')
        relay = self.inspect('container', self.relay)
        if relay:
            self.owned(relay)
            require(set(relay['NetworkSettings']['Networks']) == {self.network, self.egress}, 'Relay has unexpected networks; refusing recreation')
        self.stop()
        self.call('rm', self.container)
        if relay:
            self.call('rm', '-f', self.relay)
        print('Container and transport relay removed; encrypted volume retained. Run agent-container to recreate them.')

    def reconcile_ports(self, start=True, status=False):
        env = clean_host_env()
        env.update(CONTAINER_CONNECTION=self.config['machine'],
                   AGENT_CONTAINER_PROFILE=self.config['name'], AGENT_CONTAINER_CONTAINER=self.container,
                   AGENT_CONTAINER_INTERNAL_NETWORK=self.network, AGENT_CONTAINER_IMAGE=self.config['image'])
        action = 'once' if status else ('start' if start else 'stop')
        return run([sys.executable, ROOT/'port_reconciler.py', action, '--engine', self.engine], env=env)

    def execute(self, env, command):
        with env_file(env) as path:
            args = ['exec', '-i']
            if sys.stdin.isatty() and sys.stdout.isatty(): args += ['-t']
            args += ['--env-file', path, '--workdir', '/home/dev/dev', self.container, *command]
            # Keep the protected env file until exec ends; Ctrl-C belongs to child.
            child = subprocess.Popen(self.command(*args), env=clean_host_env())
            previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                return child.wait()
            finally:
                signal.signal(signal.SIGINT, previous)
                if child.poll() is None:
                    child.terminate(); child.wait(timeout=10)


def source_hash():
    digest = hashlib.sha256()
    for path in sorted((ROOT/'image').rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and 'node_modules' not in path.parts:
            require(not path.is_symlink(), 'Image inputs must not be symlinks')
            data = path.read_bytes()
            header = json.dumps([str(path.relative_to(ROOT)), bool(path.stat().st_mode & stat.S_IXUSR), len(data)]).encode()
            digest.update(len(header).to_bytes(8, 'big') + header + data)
    return digest.hexdigest()


def read_passphrase(unlock, confirm=False):
    if unlock['method'] == 'manual':
        require(sys.stdin.isatty(), 'Unlock needs an interactive terminal')
        value = getpass.getpass('Workspace passphrase: ')
        require(not confirm or value == getpass.getpass('Repeat passphrase: '), 'Passphrases differ')
        data = value.encode()
    else:
        executable = shutil.which('op')
        require(executable, 'Install the 1Password CLI and enable desktop integration')
        # Desktop interaction, explicit account. Never use an ambient service token.
        data = run([executable, 'read', '--account', unlock['account'], unlock['reference']], timeout=120).stdout.rstrip(b'\r\n')
    require(data and len(data) <= 4096 and b'\n' not in data and b'\r' not in data and b'\0' not in data, 'Invalid workspace passphrase')
    return data + b'\n'


def discover_proxy():
    settings = json.loads(private_read(Path.home()/'.config/secret-proxy/cli.json'))
    instance = settings.get('instance')
    name(instance)
    return str(Path('/private/etc/secret-proxy') / instance / 'client.json')


def ask(prompt, default=''):
    value = input(prompt + (f' [{default}]' if default else '') + ': ').strip()
    return value or default


def setup(path):
    require(not path.resolve().is_relative_to(ROOT), 'Keep private configuration outside the source checkout')
    require(not path.exists() and not path.is_symlink(), 'Already configured; edit your private config instead of rerunning setup')
    require(sys.stdin.isatty(), 'Setup needs an interactive terminal')
    require(sys.platform == 'darwin', 'This preview supports macOS only')
    proxy_path = discover_proxy()
    with proxy_snapshot(proxy_path) as (module, proxy, client):
        print('Connected to installed secret-proxy. No vault API credentials will enter the container.')
        username = ask('Git author name')
        email = ask('Git author email')
        ref = ask('GitHub secret name configured in your proxy (blank to skip)', infer_github(proxy))
        signing = ask('Git signing PUBLIC key file (blank to skip)')
        key = Path(signing).expanduser().read_text().strip() if signing else ''
        print('Workspace unlock: Enter for a typed passphrase, or provide an existing 1Password op://vault/item/field reference.')
        reference = ask('Workspace passphrase reference (never the password itself)')
        unlock = {'method': 'manual'}
        if reference:
            unlock = {'method': '1password', 'reference': reference, 'account': ask('1Password account hostname')}
        build_ca = ask('Corporate public CA PEM path for image downloads (blank if unnecessary)')
        cfg = validate_config({'version': 1, 'name': 'dev', 'machine': 'agent-container', 'proxyConfig': proxy_path,
              'git': {'name': username, 'email': email, 'signingKey': key}, 'shell': 'zsh',
              'image': 'localhost/agent-container:dev', 'unlock': unlock, 'githubSecret': ref,
              'buildCA': str(Path(build_ca).expanduser().absolute()) if build_ca else ''})
        # Validate client env before creating resources. No files written on validation errors.
        container_environment(cfg, proxy, module)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not path.parent.is_symlink(), 'Settings directory must not be a symlink')
        save_new(path, (json.dumps(cfg, indent=2)+'\n').encode())
    print('Saved ' + str(path))
    print('Building the first image; this can take several minutes. No host files are shared.')
    runtime = Runtime(cfg, config_path=path)
    runtime.build()
    with proxy_snapshot(cfg['proxyConfig']) as (module, proxy, client):
        runtime.prepare(proxy, module, initialise=True)
    print('Ready. Run agent-container to enter. No global shell files or default Podman connection were changed.')


def infer_github(proxy):
    env = proxy.get('environment', {})
    for key in ('GH_TOKEN', 'GITHUB_TOKEN'):
        value = env.get(key, '')
        if value.startswith(REF): return value[len(REF):]
    return ''


def import_codex(runtime, proxy, module):
    require(proxy.get('agentDir'), 'Proxy has no Pi agent directory; run secret-proxy login first')
    # Extract only the sealed Codex record; never copy host settings, skills, memory or other credentials.
    auth = json.loads(module.private_read(Path(proxy['agentDir'])/'auth.json', 1024*1024))
    record = module.validate_credential(auth.get('openai-codex'))
    script = '''import json,os,pathlib,sys,tempfile
p=pathlib.Path.home()/'dev/.agent-container/pi/auth.json'
v=json.load(sys.stdin)
old=json.loads(p.read_text()) if p.exists() else {}
old['openai-codex']=v
fd,tmp=tempfile.mkstemp(dir=p.parent); os.fchmod(fd,0o600)
with os.fdopen(fd,'w') as f: json.dump(old,f)
os.replace(tmp,p)
'''
    runtime.call('exec', '-i', runtime.container, 'python3', '-c', script, data=json.dumps(record).encode())
    print('Imported sealed Codex login only. Real OAuth tokens remain in secret-proxy. Select a model in Pi.')


@contextlib.contextmanager
def operation_lock(config_path):
    path = config_path.with_suffix('.lock')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        require(os.fstat(fd).st_uid == os.getuid(), 'Lock file owner differs')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Error('Another container setup/operation is running; retry afterward') from None
        yield
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('action', nargs='?', default='shell', choices=['setup', 'build', 'init', 'shell', 'exec', 'status', 'stop', 'recreate', 'login', 'ports', 'import-volume'])
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.action == 'setup':
        setup(args.config); return 0
    cfg = validate_config(json.loads(private_read(args.config)))
    runtime = Runtime(cfg, config_path=args.config)
    if args.action == 'status':
        info = runtime.inspect('container', runtime.container)
        print('Not created' if not info else ('Running' if info['State']['Running'] else 'Stopped'))
        return 0
    with operation_lock(args.config):
        if args.action == 'build': runtime.build(); return 0
        if args.action == 'stop': runtime.stop(); return 0
        if args.action == 'recreate': runtime.recreate(); return 0
        if args.action == 'ports':
            print(runtime.reconcile_ports(status=True).stdout.decode()); return 0
        runtime.ensure_machine()
        if args.action == 'import-volume':
            from volume_import import import_volume
            source = args.command[1:] if args.command[:1] == ['--'] else args.command
            require(len(source) == 1, 'import-volume needs one offline encrypted source volume name')
            import_volume(runtime, source[0], args.config)
            return 0
        with proxy_snapshot(cfg['proxyConfig']) as (module, proxy, client):
            env = runtime.prepare(proxy, module, initialise=args.action == 'init')
            if args.action == 'init': print('Workspace ready.'); return 0
            if args.action == 'login': import_codex(runtime, proxy, module); return 0
            runtime.reconcile_ports()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if args.action == 'exec': require(command, 'exec needs a command')
    else: command = ['/bin/' + cfg['shell']]
    return runtime.execute(env, command)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Cancelled.', file=sys.stderr); sys.exit(130)
    except Exception:
        # Never include subprocess args, proxy credentials or unexpected JSON in errors.
        error = sys.exc_info()[1]
        print('agent-container: ' + (str(error) if isinstance(error, Error) else type(error).__name__ + '; check prerequisites/configuration'), file=sys.stderr)
        sys.exit(2)
