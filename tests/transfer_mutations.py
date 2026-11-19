"""Bounded transfer-only mutations. Every mutant must fail an assertion, not import."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
CASES=[
 ('recovery location hidden','transfer.py','except wire.RecoveryError as error: raise c.Error(str(error)) from None',"except wire.RecoveryError as error: raise c.Error('Move failed') from None"),
 ('active-directory alias ignored','transfer.py','(info.st_dev, info.st_ino) != original[:2]','True'),
 ('symlink chain escape','transfer_io.py','    confined_links(records)\n','    # skipped link graph\n'),
 ('symlink ancestor followed','transfer_io.py','DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW','DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY'),
 ('metadata budget ignored','transfer_io.py',"need(metadata_bytes <= MAX_METADATA, 'Tree metadata exceeds transfer limit')","need(True, 'Tree metadata exceeds transfer limit')"),
 ('disk reserve ignored','transfer_io.py',"need(item['s'] <= max(0, space.f_bavail * space.f_frsize - DISK_RESERVE), 'Insufficient destination space')","need(True, 'Insufficient destination space')"),
 ('received bytes not verified','transfer_io.py',"need(actual == records, 'Written content/metadata verification failed')","need(True, 'Written content/metadata verification failed')"),
 ('source mutation ignored','transfer_io.py',"need(snapshot(fd, name) == (records, original), 'Source changed during transfer')","need(True, 'Source changed during transfer')"),
 ('escaping symlink','transfer_io.py',"need(resolved != '..' and not resolved.startswith('../'), 'Link escapes transferred tree')","need(True, 'Link escapes transferred tree')"),
 ('quarantine mutation ignored','transfer_io.py',"need(current == records and ident[:2] == original[:2], 'Source changed; retained')","need(True, 'Source changed; retained')"),
 ('atomic overwrite','transfer_io.py',"4 if sys.platform == 'darwin' else 1","0"),
 ('workspace traversal','transfer.py',"wire.components(path.split('/'))","None"),
 ('runtime drift','transfer.py',"c.require(ready(runtime) == before, 'Runtime identity changed; source retained')","c.require(True, 'Runtime identity changed; source retained')"),
 ('sender failure','transfer.py',"source_code == target_code == 0","target_code == 0"),
]
for label,file,old,new in CASES:
 with tempfile.TemporaryDirectory(prefix='transfer-mutation-') as tmp:
  tmp=Path(tmp)
  for p in ROOT.iterdir():
   if p.name in {'.git','__pycache__','.ruff_cache'}:continue
   if p.is_dir():shutil.copytree(p,tmp/p.name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
   else:shutil.copyfile(p,tmp/p.name)
  path=tmp/file;source=path.read_text();assert source.count(old)==(2 if label=='metadata budget ignored' else 1),label;path.write_text(source.replace(old,new))
  result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests','-p','test_transfer*.py'],cwd=tmp,capture_output=True,timeout=45)
  assert result.returncode and b'FAIL:' in result.stderr and b'ERROR:' not in result.stderr,(label,result.stderr.decode())
  print('CAUGHT:',label)
