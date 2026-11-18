"""Selected negative tests must detect intentional regressions; production files untouched."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
source=(root/'cli.py').read_text()
changes=[
 ('allow ambient host credentials', "env = dict(proxy.get('environment', {}))", "env = dict(os.environ); env.update(proxy.get('environment', {}))"),
 ('disable internal-network verification', "require(info.get('internal') is internal,", "require(True,"),
 ('accept raw credentials', "require(REF in value or", "require(True or REF in value or"),
 ('skip exact snapshot readiness', "client.check_ready()  # authenticate this exact snapshot, not just its source", "pass  # deliberately omit snapshot readiness"),
 ('disable extra-network guard', "require(set(info.get('NetworkSettings', {}).get('Networks', {})) == {self.network},", "require(True,"),
 ('skip sealed auth validation', "record = module.validate_credential(auth.get('openai-codex'))", "record = auth.get('openai-codex')"),
 ('reuse stale relay image', "require(info['Image'].removeprefix('sha256:') == image['Id'].removeprefix('sha256:'), 'Relay image changed; run agent-container recreate explicitly')", "require(True, 'Relay image changed; run agent-container recreate explicitly')"),
]
changes=[('cli.py',*entry) for entry in changes]+[
 ('cli.py','hash depends on checkout umask','bool(path.stat().st_mode & stat.S_IXUSR)','stat.S_IMODE(path.stat().st_mode)'),
 ('image/Dockerfile','unreadable copied defaults','chmod -R a+rX,go-w /opt/agent-container-home','true'),
 ('image/Dockerfile','unreadable npm metadata','chmod 0644 package.json package-lock.json','true'),
]
for filename,description,old,new in [('cli.py', 'baseline', None, None)] + changes:
 source=(root/filename).read_text()
 if old is not None:
  assert source.count(old)==1,description
 with tempfile.TemporaryDirectory(prefix='agent-container-mutation-') as temp:
  path=Path(temp)
  shutil.copytree(root,path,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git','.ruff_cache','__pycache__','node_modules'))
  (path/filename).write_text(source if old is None else source.replace(old,new))
  result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests'],cwd=path,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  if old is None:
   if result.returncode:
    raise SystemExit('Baseline failed; mutations would not be meaningful: '+result.stderr.decode())
   print('PASS: unmodified baseline')
   continue
  if result.returncode==0:
   raise SystemExit('MISSED: '+description)
  if b'FAILED (' not in result.stderr:
   raise SystemExit('Infrastructure failure, not a detected mutation: '+description)
  print('CAUGHT: '+description)
