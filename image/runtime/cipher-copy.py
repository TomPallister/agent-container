#!/usr/bin/env python3
"""Offline encrypted-volume copy. No decrypt/passphrase handling or network access."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


def manifest(root):
    digest=hashlib.sha256(); count=0; total=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs.sort(); files.sort()
        for path in [Path(directory)]+[Path(directory)/name for name in files]+[Path(directory)/name for name in dirs if (Path(directory)/name).is_symlink()]:
            info=path.lstat()
            mode=info.st_mode
            data=''
            if stat.S_ISREG(mode):
                h=hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''): h.update(chunk)
                data=h.hexdigest(); total+=info.st_size
            elif stat.S_ISLNK(mode): data=os.readlink(path)
            elif not (stat.S_ISDIR(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)
                      or (stat.S_ISCHR(mode) and info.st_rdev == 0)):  # inert overlay whiteout (0,0)
                raise ValueError('Device filesystem entries cannot be migrated')
            attrs={k:base64.b64encode(os.getxattr(path,k,follow_symlinks=False)).decode() for k in sorted(os.listxattr(path,follow_symlinks=False))}
            row=[str(path.relative_to(root)),mode,info.st_uid,info.st_gid,info.st_mtime_ns,info.st_size if stat.S_ISREG(mode) else 0,info.st_nlink if stat.S_ISREG(mode) else 0,info.st_rdev,data,attrs]
            encoded=json.dumps(row,sort_keys=True).encode(); digest.update(len(encoded).to_bytes(8,'big')+encoded); count+=1
    return {'digest':digest.hexdigest(),'entries':count,'bytes':total}


def copy(source,destination):
    source=Path(source); destination=Path(destination)
    conf=source/'gocryptfs.conf'
    if not conf.is_file() or conf.is_symlink(): raise ValueError('Source is not an initialized encrypted volume')
    if any(destination.iterdir()): raise ValueError('Destination must be empty')
    before=manifest(source)
    subprocess.run(['cp','-a','--reflink=auto',str(source)+'/.',str(destination)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    after=manifest(source); copied=manifest(destination)
    if before!=after or before!=copied: raise ValueError('Encrypted copy verification failed')
    return before

if __name__=='__main__':
    try: print(json.dumps(copy('/source','/destination')))
    except Exception:
        print('Encrypted copy failed; original untouched, incomplete destination retained for inspection',file=sys.stderr)
        sys.exit(2)
