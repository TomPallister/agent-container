import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'image/runtime/setup-home.py'

class SetupHomeTests(unittest.TestCase):
    def test_persistent_state_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'home';workspace=home/'dev';workspace.mkdir(parents=True)
            outside=Path(tmp)/'outside';outside.mkdir()
            (workspace/'.agent-container').symlink_to(outside,target_is_directory=True)
            fake=Path(tmp)/'bin';fake.mkdir();mountpoint=fake/'mountpoint'
            mountpoint.write_text('#!/bin/sh\nexit 0\n');mountpoint.chmod(0o755)
            result=subprocess.run([sys.executable,'-B',SOURCE],env=dict(os.environ,HOME=str(home),PATH=str(fake)+os.pathsep+os.environ['PATH']),input=b'{}',capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn(b'escape the encrypted workspace',result.stderr)
            self.assertEqual(list(outside.iterdir()),[])

if __name__=='__main__':unittest.main()
