"""Credential-free bounded stream helper. Also executed verbatim by remote Python -I.

No tar extraction: peers may supply only files, directories and confined relative
symlinks. Filesystem operations use no-follow directory descriptors. Callers must
quiesce writers for moves; this is not a snapshot/backup or a hostile-OS boundary.
"""
from __future__ import annotations

from collections import deque
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import signal
import stat
import struct
import sys
import threading
import time
import uuid

CHUNK = 1024 * 1024
MAX_HEADER = 65536
MAX_ENTRIES = 1000000
MAX_METADATA = 64 * 1024 * 1024
DISK_RESERVE = 64 * 1024 * 1024
WORKSPACE = '/home/dev/dev'
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class RecoveryError(ValueError):
    """Only an opaque generated recovery name, never supplied paths/content."""


def need(value, message='Unsafe or inconsistent transfer'):
    if not value:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(records):
    return hashlib.sha256(b'\n'.join(encoded(r) for r in records)).hexdigest()


def identity(info):
    return info.st_dev, info.st_ino, info.st_ctime_ns


def components(parts):
    need(isinstance(parts, list) and len(parts) <= 256)
    need(all(isinstance(p, str) and p not in ('', '.', '..') and '/' not in p and '\0' not in p for p in parts))
    need(sum(len(p) for p in parts) <= 8192)


def validate(record):
    need(isinstance(record, dict))
    parts = record.get('p'); components(parts)
    kind = record.get('t')
    keys = {'p', 't', 'l'} if kind == 'l' else {'p', 't', 'm', 'n'} | ({'s', 'h'} if kind == 'f' else set())
    need(kind in ('d', 'f', 'l') and set(record) == keys)
    if kind == 'l':
        link = record['l']
        need(parts and isinstance(link, str) and 0 < len(link) <= 8192 and '\0' not in link and not link.startswith('/'))
        resolved = posixpath.normpath(posixpath.join(*parts[:-1], link))
        need(resolved != '..' and not resolved.startswith('../'), 'Link escapes transferred tree')
    else:
        need(type(record['m']) is int and 0 <= record['m'] <= 0o777, 'Special permission bits are unsupported')
        need(type(record['n']) is int and 0 <= record['n'] < 2**63)
        if kind == 'f':
            need(type(record['s']) is int and 0 <= record['s'] < 2**63)
            need(isinstance(record['h'], str) and re.fullmatch('[a-f0-9]{64}', record['h']))


def confined_links(records):
    # Lexical normalisation alone is insufficient: a/u -> .. followed by
    # a/u/../outside escapes after the first symlink shrinks the physical path.
    links = {tuple(r['p']): r['l'] for r in records if r['t'] == 'l'}
    for path, target in links.items():
        current = list(path[:-1]); pending = deque(target.split('/'))
        hops = steps = 0
        while pending:
            steps += 1; need(steps <= 8192 and len(pending) <= 8192, 'Link resolution exceeds limit')
            part = pending.popleft()
            if part in ('', '.'): continue
            if part == '..':
                need(current, 'Link chain escapes transferred tree'); current.pop(); continue
            current.append(part)
            link = links.get(tuple(current))
            if link is not None:
                hops += 1; need(hops <= 40, 'Cyclic or excessive symlink chain')
                current.pop(); pending.extendleft(reversed(link.split('/')))


def heartbeat():
    fd = os.open('/tmp/agent-container', DIR_FLAGS)
    temp = '.transfer-' + uuid.uuid4().hex
    try:
        info = os.fstat(fd)
        need(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, 'wb') as stream:
            stream.write(str(int(time.time())).encode() + b'\n')
        os.rename(temp, 'unlock-ts', src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        try: os.unlink(temp, dir_fd=fd)
        except FileNotFoundError: pass
        os.close(fd)


@contextlib.contextmanager
def workspace_hold():
    need(not Path(WORKSPACE).is_symlink() and os.path.ismount(WORKSPACE), 'Workspace must already be unlocked')
    lines = Path('/proc/self/mountinfo').read_text().splitlines()
    need(any(line.split()[4] == WORKSPACE and line.split(' - ')[1].split()[0] == 'fuse.gocryptfs' for line in lines), 'Expected encrypted gocryptfs mount')
    fd = os.open(WORKSPACE, DIR_FLAGS); old = os.open('.', DIR_FLAGS)
    stop = threading.Event(); errors = []
    def refresh():
        while not stop.wait(15):
            try: heartbeat()
            except Exception:
                errors.append(True); return
    try:
        # A held cwd and dirfd prevent normal idle unmount and keep all I/O on
        # this mounted filesystem, never on its bare mountpoint after detachment.
        os.fchdir(fd); heartbeat()
        thread = threading.Thread(target=refresh, daemon=True); thread.start()
        try:
            yield fd
            need(not errors, 'Transfer keepalive failed')
        finally:
            stop.set(); thread.join()
    finally:
        os.fchdir(old); os.close(old); os.close(fd)


def host_path(path):
    path = os.path.abspath(os.path.expanduser(path))
    # Canonical system aliases on macOS, not arbitrary caller symlink ancestry.
    if sys.platform == 'darwin':
        for alias in ('tmp', 'var', 'etc'):
            if path.startswith('/' + alias + '/') and os.path.realpath('/' + alias) == '/private/' + alias:
                path = '/private' + path
                break
    return path


@contextlib.contextmanager
def parent(path, workspace=False, create=False):
    with contextlib.ExitStack() as stack:
        if workspace:
            need(path.startswith(WORKSPACE + '/'))
            parts = path[len(WORKSPACE) + 1:].split('/')
            root = stack.enter_context(workspace_hold())
            device = os.fstat(root).st_dev
        else:
            path = host_path(path); parts = path[1:].split('/')
            root = os.open('/', DIR_FLAGS); stack.callback(os.close, root)
            device = None
        components(parts); need(parts)
        fd = os.dup(root)
        try:
            for part in parts[:-1]:
                if create:
                    try: os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError: pass
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
                os.close(fd); fd = child
                need(device is None or os.fstat(fd).st_dev == device, 'Nested mounts are unsupported')
            yield fd, parts[-1]
        finally: os.close(fd)


@contextlib.contextmanager
def child_parent(fd, parts):
    current = os.dup(fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, DIR_FLAGS, dir_fd=current)
            os.close(current); current = next_fd
        yield current, parts[-1]
    finally: os.close(current)


def regular(fd, name, expected=None):
    result = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        info = os.fstat(result)
        need(stat.S_ISREG(info.st_mode), 'Only ordinary files are supported')
        if expected is not None: need(identity(info) == identity(expected), 'Source changed')
        return result
    except BaseException:
        os.close(result); raise


def snapshot(fd, name):
    records = []; metadata_bytes = 0
    initial = os.stat(name, dir_fd=fd, follow_symlinks=False)
    need(not stat.S_ISLNK(initial.st_mode), 'Source root must not be a symlink')
    def walk(parent_fd, leaf, parts):
        nonlocal metadata_bytes
        info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        need(info.st_dev == initial.st_dev, 'Nested mounts are unsupported')
        item = {'p': parts}
        if stat.S_ISLNK(info.st_mode):
            item.update(t='l', l=os.readlink(leaf, dir_fd=parent_fd))
        elif stat.S_ISREG(info.st_mode):
            source = regular(parent_fd, leaf, info)
            with os.fdopen(source, 'rb') as stream:
                h = hashlib.sha256()
                while True:
                    data = stream.read(CHUNK)
                    if not data: break
                    h.update(data)
                need(identity(os.fstat(stream.fileno())) == identity(info), 'Source changed while reading')
            item.update(t='f', m=stat.S_IMODE(info.st_mode), n=info.st_mtime_ns, s=info.st_size, h=h.hexdigest())
        else:
            need(stat.S_ISDIR(info.st_mode), 'Sockets, pipes and devices are unsupported')
            item.update(t='d', m=stat.S_IMODE(info.st_mode), n=info.st_mtime_ns)
        validate(item); metadata_bytes += len(encoded(item))
        need(metadata_bytes <= MAX_METADATA, 'Tree metadata exceeds transfer limit')
        records.append(item); need(len(records) <= MAX_ENTRIES)
        if item['t'] == 'd':
            directory = os.open(leaf, DIR_FLAGS, dir_fd=parent_fd)
            try:
                need(identity(os.fstat(directory)) == identity(info), 'Source directory changed')
                for child in sorted(os.listdir(directory)):
                    walk(directory, child, parts + [child])
                need(identity(os.fstat(directory)) == identity(info), 'Source directory changed')
            finally: os.close(directory)
    walk(fd, name, [])
    confined_links(records)
    return records, identity(initial)


def path_snapshot(path):
    with parent(path) as (fd, name): return snapshot(fd, name)


def frame(stream, record):
    raw = encoded(record); need(len(raw) <= MAX_HEADER)
    stream.write(struct.pack('!I', len(raw))); stream.write(raw)


def exact(stream, size):
    result = bytearray()
    while len(result) < size:
        chunk = stream.read(size - len(result)); need(chunk, 'Incomplete transfer')
        result.extend(chunk)
    return bytes(result)


def send(path, stream, workspace=False):
    with parent(path, workspace) as (fd, name):
        records, original = snapshot(fd, name)
        for item in records:
            frame(stream, item)
            if item['t'] == 'f':
                with child_parent(fd, [name] + item['p']) as (base, leaf):
                    with os.fdopen(regular(base, leaf), 'rb') as source:
                        left = item['s']; h = hashlib.sha256()
                        while left:
                            data = source.read(min(left, CHUNK)); need(data, 'Source shortened')
                            stream.write(data); h.update(data); left -= len(data)
                        need(not source.read(1) and h.hexdigest() == item['h'], 'Source content changed')
        need(snapshot(fd, name) == (records, original), 'Source changed during transfer')
        frame(stream, {'end': digest(records)}); stream.flush()


def rename_new(fd, source, destination, destination_fd=None):
    """Kernel atomic no-replace, including directories; no exists/rename race."""
    libc = ctypes.CDLL(None, use_errno=True)
    fn = getattr(libc, 'renameatx_np' if sys.platform == 'darwin' else 'renameat2')
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(fd, os.fsencode(source), fd if destination_fd is None else destination_fd, os.fsencode(destination), 4 if sys.platform == 'darwin' else 1):
        error = ctypes.get_errno(); raise OSError(error, os.strerror(error))


def remove_tree(fd, name):
    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        directory = os.open(name, DIR_FLAGS, dir_fd=fd)
        try:
            need(identity(os.fstat(directory)) == identity(info))
            os.fchmod(directory, 0o700)
            for child in os.listdir(directory): remove_tree(directory, child)
        finally: os.close(directory)
        os.rmdir(name, dir_fd=fd)
    else: os.unlink(name, dir_fd=fd)


def receive(path, stream, workspace=False):
    with parent(path, workspace, create=True) as (fd, name):
        try: os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError: pass
        else: raise FileExistsError('Destination must be new; no merge/overwrite')
        stage = '.agent-transfer-' + uuid.uuid4().hex
        os.mkdir(stage, 0o700, dir_fd=fd); stage_fd = os.open(stage, DIR_FLAGS, dir_fd=fd)
        records = []; seen = {}; links = []; metadata_bytes = 0
        try:
            while True:
                length = struct.unpack('!I', exact(stream, 4))[0]
                need(0 < length <= MAX_HEADER)
                item = json.loads(exact(stream, length))
                if isinstance(item, dict) and set(item) == {'end'}:
                    need(records and item['end'] == digest(records))
                    need(not stream.read(1), 'Trailing transfer data')
                    break
                metadata_bytes += length
                need(metadata_bytes <= MAX_METADATA, 'Tree metadata exceeds transfer limit')
                validate(item); parts = item['p']; key = tuple(parts)
                need(key not in seen and (not records and not parts or records and parts))
                need(not parts or seen.get(tuple(parts[:-1])) == 'd', 'Missing directory parent')
                need(parts or item['t'] in ('d', 'f'))
                seen[key] = item['t']; records.append(item); need(len(records) <= MAX_ENTRIES)
                with child_parent(stage_fd, ['data'] + parts) as (base, leaf):
                    if item['t'] == 'd': os.mkdir(leaf, 0o700, dir_fd=base)
                    elif item['t'] == 'l': links.append(item)
                    else:
                        space = os.fstatvfs(base)
                        need(item['s'] <= max(0, space.f_bavail * space.f_frsize - DISK_RESERVE), 'Insufficient destination space')
                        out = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=base)
                        with os.fdopen(out, 'wb') as target:
                            left = item['s']
                            while left:
                                data = exact(stream, min(left, CHUNK)); target.write(data); left -= len(data)
                            target.flush(); os.fsync(target.fileno())
                            os.fchmod(target.fileno(), item['m'])
                            os.utime(target.fileno(), ns=(item['n'], item['n']))
            for item in links:
                with child_parent(stage_fd, ['data'] + item['p']) as (base, leaf):
                    os.symlink(item['l'], leaf, dir_fd=base)
            for item in reversed(records):
                if item['t'] == 'd':
                    with child_parent(stage_fd, ['data'] + item['p']) as (base, leaf):
                        directory = os.open(leaf, DIR_FLAGS, dir_fd=base)
                        try:
                            os.fchmod(directory, item['m']); os.utime(directory, ns=(item['n'], item['n']))
                        finally: os.close(directory)
            actual, _ = snapshot(stage_fd, 'data')
            need(actual == records, 'Written content/metadata verification failed')
            rename_new(stage_fd, 'data', name, destination_fd=fd)
            os.fsync(fd)
            return {'digest': digest(records), 'entries': len(records), 'bytes': sum(r.get('s', 0) for r in records)}
        finally:
            os.close(stage_fd); remove_tree(fd, stage)


def delete_verified(path, records, original):
    """Move only: verify again, quarantine the same inode, recheck, then delete.

A quiescent source is required. Open writers cannot be frozen portably; never use
this to migrate live databases/process state. Failed deletion can leave a partial
source, but a complete verified destination has already been published.
"""
    with parent(path) as (fd, name):
        need(records[0]['t'] == 'd', 'Moves require a directory')
        need(snapshot(fd, name) == (records, original), 'Source changed; retained')
        quarantine = '.agent-move-' + uuid.uuid4().hex
        rename_new(fd, name, quarantine)
        try:
            current, ident = snapshot(fd, quarantine)
            need(current == records and ident[:2] == original[:2], 'Source changed; retained')
            remove_tree(fd, quarantine)
        except BaseException:
            try: rename_new(fd, quarantine, name)
            except OSError:
                raise RecoveryError('Verified destination retained; source recovery directory beside the original source: ' + quarantine) from None
            raise


def main():
    os.umask(0o077)
    def interrupted(signum, frame):
        raise InterruptedError('Transfer cancelled')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    action, scope, path = sys.argv[1:]
    need(scope in ('host', 'workspace'))
    if action == 'describe':
        with parent(path, scope == 'workspace') as (fd, name):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
            print(json.dumps({'kind': 'd' if stat.S_ISDIR(info.st_mode) else 'f'}))
    elif action == 'send': send(path, sys.stdout.buffer, scope == 'workspace')
    else:
        need(action == 'receive')
        print(json.dumps(receive(path, sys.stdin.buffer, scope == 'workspace')))


if __name__ == '__main__':
    try: main()
    except Exception:
        # Never echo supplied paths, stream data or an untrusted exception body.
        print('Transfer refused: ' + type(sys.exc_info()[1]).__name__, file=sys.stderr)
        sys.exit(2)
