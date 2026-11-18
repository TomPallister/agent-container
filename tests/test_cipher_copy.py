import importlib.util
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('cipher_copy',Path(__file__).resolve().parents[1]/'image/runtime/cipher-copy.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class CipherCopyTests(unittest.TestCase):
    def setUp(self):
        if not hasattr(os,'listxattr'):
            # Linux-only xattr syscalls: portable tests cover the remaining metadata;
            # the real xattr/hardlink copy test below runs in the Linux suite.
            xattrs=patch.object(os,'listxattr',return_value=[],create=True)
            xattrs.start(); self.addCleanup(xattrs.stop)
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.src=Path(self.temp.name)/'source';self.src.mkdir()
        self.dst=Path(self.temp.name)/'destination';self.dst.mkdir()
        (self.src/'gocryptfs.conf').write_text('synthetic configuration')
        (self.src/'file').write_bytes(b'encrypted fixture')

    def test_refuses_nonempty_destination_and_symlink_conf(self):
        (self.dst/'existing').touch()
        with self.assertRaises(ValueError):m.copy(self.src,self.dst)
        (self.dst/'existing').unlink();(self.src/'gocryptfs.conf').unlink();(self.src/'gocryptfs.conf').symlink_to('file')
        with self.assertRaises(ValueError):m.copy(self.src,self.dst)

    def test_manifest_detects_content_mode_and_link_changes(self):
        before=m.manifest(self.src)
        (self.src/'file').write_bytes(b'changed')
        self.assertNotEqual(before,m.manifest(self.src))
        before=m.manifest(self.src);(self.src/'file').chmod(0o600)
        self.assertNotEqual(before,m.manifest(self.src))
        before=m.manifest(self.src);(self.src/'link').symlink_to('file')
        self.assertNotEqual(before,m.manifest(self.src))

    def test_stopped_nested_runtime_fifos_and_socket_nodes_are_not_opened(self):
        os.mkfifo(self.src/'pipe')
        s=socket.socket(socket.AF_UNIX);s.bind(str(self.src/'socket'));s.close()
        self.assertEqual(m.manifest(self.src)['entries'],5)

    def test_changed_source_or_corrupt_copy_refused(self):
        for change_source in (True,False):
            with self.subTest(source=change_source):
                shutil.rmtree(self.dst);self.dst.mkdir()
                (self.src/'file').write_bytes(b'original fixture')
                def bad_copy(*args,**kwargs):
                    shutil.copytree(self.src,self.dst,dirs_exist_ok=True)
                    ((self.src if change_source else self.dst)/'file').write_bytes(b'corruption')
                with patch.object(m.subprocess,'run',side_effect=bad_copy):
                    with self.assertRaises(ValueError):m.copy(self.src,self.dst)

    @unittest.skipUnless(sys.platform=='linux','GNU cp metadata preservation is tested on Linux')
    def test_real_copy_preserves_ciphertext_hardlinks_xattrs_and_fifos(self):
        os.link(self.src/'file',self.src/'hardlink')
        os.setxattr(self.src/'file','user.fixture',b'attribute')
        os.mkfifo(self.src/'pipe')
        before=m.manifest(self.src);copied=m.copy(self.src,self.dst)
        self.assertEqual(copied,before)
        self.assertEqual(m.manifest(self.dst),before)

if __name__=='__main__':unittest.main()
