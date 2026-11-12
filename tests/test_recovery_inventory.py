"""Recovery evidence inventory: real retained files and bounded failure cases."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nivram import persistence
from nivram.persistence import ProjectStore
from test_persistence_crash import project


class RecoveryInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.store=ProjectStore(self.root/'recovery')
        self.generation=self.store.recovery_context()['generation']

    def write(self, number, data):
        self.store.recovery_dir.mkdir(exist_ok=True)
        path=self.store.recovery_dir/(str(number)+'-'+'a'*32+'.json')
        path.write_bytes(data if isinstance(data,bytes) else json.dumps(data).encode())
        return path

    def record(self, schema='nivram-recovery/1', document=None, stamp=100):
        return dict(schema=schema,created_ns=stamp,document=project(1200) if document is None else document)

    def test_empty_new_profile_is_ready_without_creating_directories(self):
        self.assertEqual(self.store.recovery_inventory(),dict(schemaVersion=1,status='ready',records=[],
            issues=dict(invalid=0,unsupported=0,unreadable=0,interrupted=0)))
        self.assertFalse(self.store.recovery_dir.exists())

    def test_valid_legacy_records_and_all_failed_formats_are_retained(self):
        older=self.store.autosave(project(1000),self.generation,0)
        newer=self.store.autosave(project(1200),self.generation,1)
        self.write(1,b'{"partial":')
        self.write(2,self.record(schema='nivram-recovery/2'))
        self.write(3,self.record(document=project(900)|{'schema':'nivram/2'}))
        tmp=self.store.recovery_dir/'.nv-interrupted.tmp';tmp.write_bytes(b'flushed fragment')
        unrelated=self.store.recovery_dir/'notes.txt';unrelated.write_bytes(b'unrelated')
        before={p.name:p.read_bytes() for p in self.store.recovery_dir.iterdir()}
        value=self.store.recovery_inventory()
        self.assertEqual(value['status'],'ready')
        self.assertEqual([r['id'] for r in value['records']],[newer['id'],older['id']])
        self.assertEqual(value['issues'],dict(invalid=1,unsupported=2,unreadable=0,interrupted=1))
        self.assertEqual(self.store.list_recovery(),value['records'])
        self.assertEqual({p.name:p.read_bytes() for p in self.store.recovery_dir.iterdir()},before)

    def test_bad_timestamps_graph_and_oversize_are_unavailable_records_not_empty_success(self):
        rows=[self.record(stamp=True),self.record(stamp=10**20),
              self.record(document=project(900)|{'graph':None}),b'x'*(persistence.MAX_RECOVERY_BYTES+1)]
        for n,row in enumerate(rows,1):self.write(n,row)
        value=self.store.recovery_inventory()
        self.assertEqual(value['records'],[])
        self.assertEqual(value['issues']['invalid'],4)
        self.assertEqual(len(list(self.store.recovery_dir.iterdir())),4)

    def test_unreadable_record_is_reported_and_its_bytes_are_not_changed(self):
        path=self.write(1,self.record());before=path.read_bytes()
        with patch.object(self.store,'recover',side_effect=PermissionError('private path')):
            value=self.store.recovery_inventory()
        self.assertEqual(value['issues']['unreadable'],1)
        self.assertNotIn('private',json.dumps(value))
        self.assertEqual(path.read_bytes(),before)

    def test_inaccessible_inventory_is_distinct_from_no_records_and_blocks_publication(self):
        saved=self.store.autosave(project(1000),self.generation,0)
        before={p.name:p.read_bytes() for p in self.store.recovery_dir.iterdir()}
        with patch.object(persistence.os,'scandir',side_effect=PermissionError('private path')):
            value=self.store.recovery_inventory()
            self.assertEqual(value['status'],'unavailable')
            with self.assertRaises(OSError):self.store.list_recovery()
            with self.assertRaises(OSError):self.store.autosave(project(1200),self.generation,1)
        self.assertEqual({p.name:p.read_bytes() for p in self.store.recovery_dir.iterdir()},before)
        self.assertEqual(self.store.recover(saved['id'])['document'],project(1000))

    def test_failure_during_enumeration_is_not_an_empty_directory(self):
        class Vanished:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __iter__(self):raise FileNotFoundError('private disappeared')
        with patch.object(persistence.os,'scandir',return_value=Vanished()):
            self.assertEqual(self.store.recovery_inventory()['status'],'unavailable')

    def test_scan_limit_is_inclusive_and_does_not_consume_unbounded_tail(self):
        seen=[]
        class Entries:
            def __init__(self,count):self.count=count
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __iter__(self):
                for i in range(self.count):
                    seen.append(i)
                    if i>2:raise AssertionError('Unbounded enumeration')
                    yield SimpleNamespace(name='unrelated-'+str(i))
        with patch.object(persistence,'MAX_RECOVERY_ENTRIES',2):
            with patch.object(persistence.os,'scandir',return_value=Entries(2)):
                self.assertEqual(self.store.recovery_inventory()['status'],'ready')
            seen.clear()
            with patch.object(persistence.os,'scandir',return_value=Entries(100)):
                self.assertEqual(self.store.recovery_inventory()['status'],'unavailable')
            self.assertEqual(seen,[0,1,2])

    def test_known_nonordinary_entry_is_not_followed_or_decoded(self):
        path=self.write(1,self.record());info=path.stat()
        entry=SimpleNamespace(name=path.name,stat=lambda **kw:SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0x400))
        class Entries:
            def __enter__(self):return iter([entry])
            def __exit__(self,*args):pass
        with patch.object(persistence.os,'scandir',return_value=Entries()),patch.object(self.store,'recover',side_effect=AssertionError('Followed redirect')):
            self.assertEqual(self.store.recovery_inventory()['issues']['unreadable'],1)

    def test_rotation_keeps_failed_records_and_interrupted_bytes(self):
        self.store.history_limit=2
        corrupt=self.write(1,b'broken')
        future=self.write(2,self.record(schema='nivram-recovery/9'))
        fragment=self.store.recovery_dir/'.nv-fragment.tmp';fragment.write_bytes(b'partial')
        before={p.name:p.read_bytes() for p in (corrupt,future,fragment)}
        for rev in range(4):self.store.autosave(project(1000+rev),self.generation,rev)
        value=self.store.recovery_inventory()
        self.assertEqual(len(value['records']),2)
        self.assertEqual(value['issues'],dict(invalid=1,unsupported=1,unreadable=0,interrupted=1))
        self.assertEqual({p.name:p.read_bytes() for p in (corrupt,future,fragment)},before)


if __name__=='__main__':unittest.main()

