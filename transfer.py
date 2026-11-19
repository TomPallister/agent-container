"""Host/workspace transfer orchestration; never starts, unlocks or rebuilds anything."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import subprocess
import sys

import cli as c
import transfer_io as wire

HELPER = Path(__file__).with_name('transfer_io.py')


def dev_path(value):
    c.require(value.startswith('dev:'), 'Expected dev: path')
    path = value[4:]
    for prefix in ('~/dev', wire.WORKSPACE):
        if path == prefix: path = ''
        elif path.startswith(prefix + '/'): path = path[len(prefix) + 1:]
    if path.startswith('/'): path = path[1:]
    if path.endswith('/'): path = path[:-1]
    try: wire.components(path.split('/'))
    except ValueError: raise c.Error('dev: path must be nonempty, literal and confined') from None
    return wire.WORKSPACE + '/' + path


def ready(runtime):
    info = runtime.inspect('container', runtime.container)
    c.require(info and info['State']['Running'] and not info['State'].get('Paused'), 'Workspace must already be running and unlocked')
    runtime.check_topology(info)
    network = runtime.inspect('network', runtime.network)
    c.require(network and network.get('internal') is True, 'Expected isolated workspace network')
    runtime.owned(network)
    volume = runtime.inspect('volume', runtime.volume)
    c.require(volume is not None, 'Workspace volume missing')
    runtime.owned(volume)
    from volume_import import check_import
    check_import(runtime, volume, runtime.config_path)
    return info['Id'], info['State']['StartedAt']


def remote(runtime, container_id, action, path):
    # No credentials, shell interpolation, persistent helper install or image change.
    return runtime.command('exec', '-i', container_id, 'python3', '-I', '-c', HELPER.read_text(), action, 'workspace', path)


def local(action, path):
    return [sys.executable, '-I', str(HELPER), action, 'host', path]


def pipeline(sender, receiver):
    children = []
    try:
        source = subprocess.Popen(sender, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=c.clean_host_env())
        children.append(source)
        target = subprocess.Popen(receiver, stdin=source.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=c.clean_host_env())
        children.append(target); source.stdout.close()
        with target.stdout:
            report = target.stdout.read(4097)
        c.require(len(report) <= 4096, 'Invalid transfer receipt')
        target_code = target.wait(); source_code = source.wait()
        c.require(source_code == target_code == 0, 'Transfer refused or interrupted; source retained. No existing destination replaced; inspect any new destination before retrying')
        result = json.loads(report)
        c.require(isinstance(result, dict) and set(result) == {'digest', 'entries', 'bytes'}, 'Invalid transfer receipt')
        c.require(isinstance(result['digest'], str) and re.fullmatch('[a-f0-9]{64}', result['digest']) and
                  type(result['entries']) is int and 1 <= result['entries'] <= wire.MAX_ENTRIES and
                  type(result['bytes']) is int and 0 <= result['bytes'] < 2**63, 'Invalid transfer receipt')
        return result
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait()


def perform(runtime, action, args):
    c.require(len(args) == 2, action + ' needs SOURCE DESTINATION (exactly one dev: path)')
    src, dst = args
    src_dev, dst_dev = src.startswith('dev:'), dst.startswith('dev:')
    c.require(src_dev != dst_dev, 'Exactly one path must start with dev:')
    c.require(action != 'move' or not src_dev, 'Move supports host directory -> workspace only')
    src = dev_path(src) if src_dev else wire.host_path(src)
    trailing = dst.endswith('/')
    dst = dev_path(dst) if dst_dev else wire.host_path(dst)
    before = ready(runtime); container_id = before[0]
    original = records = None
    if src_dev:
        info = c.run(remote(runtime, container_id, 'describe', src))
        c.require(len(info.stdout) <= 4096, 'Invalid source description')
        kind = json.loads(info.stdout)['kind']
    else:
        # This also rejects symlink ancestry and unsupported source entry types.
        records, original = wire.path_snapshot(src)
        kind = records[0]['t']
    if action == 'move':
        c.require(kind == 'd', 'Move requires a host directory; use copy for files')
        # Inodes, not spelling: macOS accepts case/normalisation path aliases.
        protected = [Path.home(), Path.cwd(), HELPER.parent, runtime.config_path.resolve().parent]
        for path in protected:
            path = path.resolve()
            for ancestor in (path, *path.parents):
                info = ancestor.stat()
                c.require((info.st_dev, info.st_ino) != original[:2],
                          'Refusing to move an active home/working/configuration directory')
    if kind == 'f' and (trailing or not dst_dev and os.path.isdir(dst)):
        dst += '/' + os.path.basename(src)
    send = remote(runtime, container_id, 'send', src) if src_dev else local('send', src)
    receive = remote(runtime, container_id, 'receive', dst) if dst_dev else local('receive', dst)
    result = pipeline(send, receive)
    c.require(ready(runtime) == before, 'Runtime identity changed; source retained')
    if records is not None:
        c.require(result['digest'] == wire.digest(records), 'Destination differs from original source; source retained')
    if action == 'move':
        try: wire.delete_verified(src, records, original)
        except wire.RecoveryError as error: raise c.Error(str(error)) from None
        except (OSError, ValueError):
            raise c.Error('Move cleanup refused/failed; verified destination retained. Inspect the original source before retrying') from None
    print(action + ': verified ' + str(result['entries']) + ' entries, ' + str(result['bytes']) + ' bytes' + ('; host source removed' if action == 'move' else '; source retained'))
