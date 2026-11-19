import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import transfer_io as t


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'empty').mkdir()
        self.file = self.source / 'a "quote" $(literal)\n.txt'
        self.file.write_bytes(b'payload\0' * 4096)
        self.file.chmod(0o751)
        os.utime(self.file, ns=(1700000000123456789, 1700000000123456789))
        (self.source / 'link').symlink_to(self.file.name)

    def wire(self, path=None):
        data = io.BytesIO()
        t.send(str(path or self.source), data)
        return data.getvalue()

    def receive(self, data, name='target'):
        return t.receive(str(self.root / name), io.BytesIO(data))

    def test_roundtrip_contents_modes_times_links_and_empty_dirs(self):
        result = self.receive(self.wire())
        self.assertEqual(t.path_snapshot(str(self.source))[0],
                         t.path_snapshot(str(self.root / 'target'))[0])
        self.assertEqual(result['bytes'], self.file.stat().st_size)
        self.assertEqual(stat.S_IMODE((self.root / 'target' / self.file.name).stat().st_mode), 0o751)

    def test_single_file_nested_parent(self):
        self.receive(self.wire(self.file), 'nested/file')
        self.assertEqual((self.root / 'nested/file').read_bytes(), self.file.read_bytes())

    def test_no_overwrite_or_directory_merge(self):
        for name in ['existing', 'existing-directory']:
            target = self.root / name
            target.write_text('original') if name == 'existing' else target.mkdir()
            with self.assertRaises(Exception):
                self.receive(self.wire(), name)
            self.assertEqual(target.read_text() if target.is_file() else list(target.iterdir()),
                             'original' if target.is_file() else [])

    def test_symlink_parents_and_final_links_refused(self):
        outside = self.root / 'outside'; outside.mkdir()
        (self.root / 'alias').symlink_to(outside)
        for name in ['alias/file', 'alias']:
            with self.assertRaises(Exception): self.receive(self.wire(), name)
        self.assertEqual(list(outside.iterdir()), [])

    def test_truncated_or_corrupt_stream_never_published(self):
        wire = self.wire()
        offset = wire.index(b'payload\0')
        corrupted = wire[:offset] + b'X' + wire[offset + 1:]
        for data in [wire[:100], wire[:-1], corrupted, wire + b'extra']:
            with self.assertRaises(Exception): self.receive(data)
            self.assertFalse((self.root / 'target').exists())
            self.assertEqual(list(self.root.glob('.agent-transfer-*')), [])

    def frame(self, obj):
        data = json.dumps(obj).encode()
        return struct.pack('!I', len(data)) + data

    def test_malformed_headers(self):
        root = {'p': [], 't': 'd', 'm': 0o755, 'n': 1700000000000000000}
        cases = [
            {'p': ['..', 'escape'], 't': 'd', 'm': 0o755, 'n': 0},
            {'p': ['/absolute'], 't': 'd', 'm': 0o755, 'n': 0},
            {'p': ['a/b'], 't': 'd', 'm': 0o755, 'n': 0},
            {'p': ['fifo'], 't': 'fifo'},
            {'p': ['link'], 't': 'l', 'l': '../../outside'},
            {'p': ['link'], 't': 'l', 'l': '/etc/passwd'},
            {'p': ['file'], 't': 'f', 'm': 0o4755, 'n': 0, 's': 0, 'h': '0' * 64},
            {'p': ['file'], 't': 'f', 'm': 0o600, 'n': 0, 's': -1, 'h': '0' * 64},
            root,
        ]
        for record in cases:
            with self.subTest(record=record), self.assertRaises(Exception):
                self.receive(self.frame(root) + self.frame(record))
            self.assertFalse((self.root / 'target').exists())
        with self.assertRaises(Exception): self.receive(struct.pack('!I', 65537))

    def test_symlink_chains_cannot_escape_or_cycle(self):
        directory=self.source/'adir';directory.mkdir();(directory/'u').symlink_to('..')
        escape=self.source/'escape';escape.symlink_to('adir/u/../outside')
        with self.assertRaises(ValueError):self.wire()
        records=[{'p':[],'t':'d','m':0o700,'n':0},
                 {'p':['adir'],'t':'d','m':0o700,'n':0},
                 {'p':['adir','u'],'t':'l','l':'..'},
                 {'p':['escape'],'t':'l','l':'adir/u/../outside'}]
        malicious=b''.join(self.frame(r) for r in records)+self.frame({'end':t.digest(records)})
        with self.assertRaises(ValueError):self.receive(malicious)
        self.assertFalse((self.root/'target').exists())
        escape.unlink();escape.symlink_to('escape')
        with self.assertRaises(ValueError):self.wire()

    def test_stream_cannot_traverse_an_earlier_symlink(self):
        records=[{'p':[],'t':'d','m':0o700,'n':0},
                 {'p':['alias'],'t':'l','l':'.'},
                 {'p':['alias','file'],'t':'d','m':0o700,'n':0}]
        with self.assertRaises(Exception): self.receive(b''.join(self.frame(r) for r in records))
        self.assertFalse((self.root/'target').exists())

    def test_interrupted_receiver_removes_staging(self):
        process=subprocess.Popen([sys.executable,'-I',t.__file__,'receive','host',str(self.root/'target')],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            header={'p':[],'t':'f','m':0o600,'n':0,'s':100000,'h':'a'*64}
            process.stdin.write(self.frame(header)+b'partial');process.stdin.flush()
            deadline=time.monotonic()+5
            while not list(self.root.glob('.agent-transfer-*')):
                self.assertLess(time.monotonic(),deadline);time.sleep(0.02)
            process.terminate();process.communicate(timeout=5)
            self.assertNotEqual(process.returncode,0)
            self.assertFalse((self.root/'target').exists())
            self.assertEqual(list(self.root.glob('.agent-transfer-*')),[])
        finally:
            if process.poll() is None:process.kill();process.communicate()

    def test_metadata_and_available_space_limits(self):
        wire=self.wire()
        with patch.object(t,'MAX_METADATA',1):
            with self.assertRaises(ValueError):self.wire()
            with self.assertRaises(ValueError):self.receive(wire)
        with patch.object(t.os,'fstatvfs') as space:
            space.return_value.f_bavail=0;space.return_value.f_frsize=4096
            with self.assertRaises(ValueError):self.receive(wire)
        self.assertFalse((self.root/'target').exists())

    def test_source_links_and_special_files_refused(self):
        # Fail at header validation, before creating even a staging symlink.
        with self.assertRaises(ValueError):
            t.validate({'p':['link'],'t':'l','l':'../outside'})
        for target in ['/etc/passwd', '../../outside']:
            link = self.source / 'bad'; link.symlink_to(target)
            with self.assertRaises(Exception): self.wire()
            link.unlink()
        os.mkfifo(self.source / 'pipe')
        with self.assertRaises(Exception): self.wire()

    def test_sender_detects_same_length_source_edit(self):
        class EditingStream(io.BytesIO):
            def write(inner, data):
                if data.startswith(b'payload'):
                    before = self.file.stat()
                    self.file.write_bytes(b'changed\0' * 4096)
                    os.utime(self.file, ns=(before.st_atime_ns, before.st_mtime_ns))
                return super().write(data)
        with self.assertRaises(Exception): t.send(str(self.source), EditingStream())

    def test_source_replacement_not_deleted(self):
        records, identity = t.path_snapshot(str(self.source))
        self.source.rename(self.root / 'old')
        self.source.mkdir(); (self.source / 'new').write_text('keep')
        with self.assertRaises(Exception): t.delete_verified(str(self.source), records, identity)
        self.assertEqual((self.source / 'new').read_text(), 'keep')

    def test_verified_delete_only_matching_tree(self):
        records, identity = t.path_snapshot(str(self.source))
        self.file.write_bytes(b'changed\0' * 4096)
        with self.assertRaises(Exception): t.delete_verified(str(self.source), records, identity)
        self.assertTrue(self.source.is_dir())
        records, identity = t.path_snapshot(str(self.source))
        t.delete_verified(str(self.source), records, identity)
        self.assertFalse(self.source.exists())

    def test_move_change_after_quarantine_is_restored(self):
        records,ident=t.path_snapshot(str(self.source));rename=t.rename_new
        def mutate(fd,old,new,*args,**kwargs):
            rename(fd,old,new,*args,**kwargs)
            if new.startswith('.agent-move-'):
                file=self.root/new/self.file.name;before=file.stat()
                file.write_bytes((b'changed'+bytes([0]))*4096)
                os.utime(file,ns=(before.st_atime_ns,before.st_mtime_ns))
        with patch.object(t,'rename_new',side_effect=mutate),self.assertRaises(ValueError):
            t.delete_verified(str(self.source),records,ident)
        self.assertEqual(self.file.read_bytes(),(b'changed'+bytes([0]))*4096)

    def test_failed_move_restoration_reports_opaque_recovery_name(self):
        contents=self.file.read_bytes()
        records,ident=t.path_snapshot(str(self.source));rename=t.rename_new
        def refuse_restore(fd,old,new,*args,**kwargs):
            if old.startswith('.agent-move-'):raise FileExistsError('collision')
            return rename(fd,old,new,*args,**kwargs)
        with patch.object(t,'rename_new',side_effect=refuse_restore),patch.object(t,'remove_tree',side_effect=OSError('delete refused')):
            with self.assertRaises(t.RecoveryError) as error:t.delete_verified(str(self.source),records,ident)
        recovery=list(self.root.glob('.agent-move-*'));self.assertEqual(len(recovery),1)
        self.assertIn(recovery[0].name,str(error.exception))
        self.assertNotIn(str(self.root),str(error.exception))
        self.assertEqual((recovery[0]/self.file.name).read_bytes(),contents)

    def test_atomic_publication_does_not_clobber(self):
        (self.root / 'one').mkdir(); (self.root / 'two').mkdir()
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(FileExistsError): t.rename_new(fd, 'one', 'two')
        finally: os.close(fd)
        self.assertTrue((self.root / 'one').is_dir())


if __name__ == '__main__': unittest.main()
