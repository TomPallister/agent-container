import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class InstallerTests(unittest.TestCase):
    def test_only_command_link_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"bin"
            env=dict(os.environ, AGENT_CONTAINER_BIN_DIR=str(target))
            for _ in range(2):
                result=subprocess.run(["bash",str(ROOT/"install.sh")],env=env,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(list(target.iterdir()),[target/"agent-container"])
            self.assertEqual((target/"agent-container").resolve(),ROOT/"agent-container")
    def test_foreign_command_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp); command=target/"agent-container"; command.write_text("keep")
            result=subprocess.run(["bash",str(ROOT/"install.sh")],env=dict(os.environ,AGENT_CONTAINER_BIN_DIR=tmp),capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(command.read_text(),"keep")

if __name__=="__main__": unittest.main()
