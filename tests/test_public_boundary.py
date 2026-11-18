"""Public-source hygiene guards; scanner/review still required before publication."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
SKIP={'.git','.ruff_cache','.venv','__pycache__','node_modules'}

def findings(path,raw):
    bad=[]
    if path.name in {'auth.json','client.json','cli.json','proxy-token','ready.json','manifest.json'} or path.suffix in {'.pem','.p12','.pfx','.key'}:
        bad.append('runtime/credential file')
    if b'\xe2\x80\x8b' in raw: bad.append('invisible zero-width marker')
    text=raw.decode('utf-8')
    if re.search(r'/Users/[^/\s]+/',text): bad.append('host home path')
    key_text=text.replace("'-----BEGIN PRIVATE KEY-----'",'')  # marker-only rejection fixture
    if re.search(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----',key_text): bad.append('private key material')
    for email in re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',text):
        if email != 'git@github.com' and not email.endswith(('@example.com','@example.invalid')) and not (path.name=='.p10k.zsh' and email=='x@y.com'):
            bad.append('non-fixture email')
    for account in re.findall(r'(?<![A-Za-z0-9])[0-9]{12}(?![A-Za-z0-9])',text):
        if account not in {'000000000000','111111111111','123456789012'}:
            bad.append('literal cloud account identifier')
    return bad

class PublicBoundaryTests(unittest.TestCase):
    def test_source_has_no_identity_or_runtime_state(self):
        for path in ROOT.rglob('*'):
            if not path.is_file() or any(x in SKIP for x in path.relative_to(ROOT).parts) or path==Path(__file__): continue
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertEqual(findings(path,path.read_bytes()),[])
    def test_positive_controls(self):
        for filename,raw in [('auth.json',b'{}'),('code.py',b'/Users/example/private'),('code.py',b'person@private.invalid'),('code.py',b'987654321098'),('key.txt',b'-----BEGIN PRIVATE KEY-----'),('code.py',b'secre\xe2\x80\x8btref')]:
            self.assertTrue(findings(Path(filename),raw),filename)
    def test_license(self):
        self.assertIn('Apache License',(ROOT/'LICENSE').read_text())
        self.assertIn('Version 2.0',(ROOT/'LICENSE').read_text())

if __name__=='__main__': unittest.main()
