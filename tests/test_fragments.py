import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nivram.fragments import FragmentStore, validate_graph, validate_record, MAX_BYTES, MAX_DIRECTORY_ENTRIES
from nivram.persistence import atomic_write

GRAPH = {'atoms': [{'id': 'a', 'symbol': 'C', 'x': 0, 'y': 0, 'mapNum': 42}], 'bonds': []}


class FragmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'shelf'
        self.store = FragmentStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_save_reopen_immutable_exact_annotations_and_no_chemistry_import(self):
        before = copy.deepcopy(GRAPH)
        one = self.store.save('Named <copy>', GRAPH)
        self.assertEqual(one['status'], 'saved')
        path = self.root / (one['fragment']['id'] + '.json')
        original = path.read_bytes()
        two = self.store.save('Named <copy>', GRAPH)
        self.assertEqual(two['status'], 'saved')
        self.assertNotEqual(one['fragment']['id'], two['fragment']['id'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(FragmentStore(self.root).list()['fragments']), 2)
        self.assertEqual(GRAPH, before)

    def test_structural_only_validation_does_not_claim_chemical_acceptance(self):
        # Deliberately chemically unreasonable but structurally bounded input.
        g = copy.deepcopy(GRAPH); g['atoms'][0]['charge'] = 8
        self.assertEqual(validate_graph(g), g)

    def test_invalid_graphs_fail_before_writes(self):
        for mutate in [lambda g: g['atoms'][0].update(x=float('nan')), lambda g: g['atoms'][0].update(radicals=True), lambda g: g['atoms'][0].update(symbol='Xx'), lambda g: g.update(extra=1), lambda g: g['bonds'].append({'id':'b','begin':'a','end':'missing','order':1}), lambda g: g['atoms'][0].update(unknown=3)]:
            g = copy.deepcopy(GRAPH); mutate(g)
            self.assertEqual(self.store.save('Bad', g), {'status': 'failed'})
        self.assertFalse(self.root.exists())

    def test_symlink_root_file_and_parent_rejected(self):
        outside = Path(self.temp.name) / 'outside'; outside.mkdir()
        self.root.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.store.save('Bad', GRAPH)['status'], 'failed')
        self.assertEqual(list(outside.iterdir()), [])
        with self.assertRaises(ValueError): self.store.list()
        other = Path(self.temp.name) / 'safe'; other.mkdir()
        linked = other / ('a' * 32 + '.json'); linked.symlink_to(outside / 'nothing')
        self.assertEqual(FragmentStore(other).list()['invalidRecords'], 1)
        self.assertEqual(FragmentStore(other).save('Bad', GRAPH)['status'], 'failed')
        self.assertEqual(FragmentStore(self.root / 'nested').save('Bad', GRAPH)['status'], 'failed')

    def test_oversized_corrupt_duplicate_key_and_identity_mismatch_retained(self):
        self.root.mkdir()
        paths = []
        for i, data in enumerate([b' ' * (MAX_BYTES + 1), b'{"schema":0,"schema":1}', b'[]', b'{"schema":"nivram-fragment/1","id":"'+b'f'*32+b'","name":"x","graph":'+json.dumps(GRAPH).encode()+b'}']):
            p = self.root / (f'{i:032x}.json'); p.write_bytes(data); paths.append((p,data))
        self.assertEqual(self.store.list()['invalidRecords'], 4)
        self.assertEqual(self.store.save('new', GRAPH)['status'], 'failed')
        for p,data in paths: self.assertEqual(p.read_bytes(),data)

    def test_publication_then_fault_is_unconfirmed_and_never_retried_or_removed(self):
        calls=[]
        def uncertain(path,data,**kwargs):
            calls.append(path); atomic_write(path,data,**kwargs); raise OSError('after publish')
        with patch('nivram.fragments.atomic_write', side_effect=uncertain):
            result=self.store.save('May exist',GRAPH)
        self.assertEqual(result,{'status':'unconfirmed'}); self.assertEqual(len(calls),1)
        self.assertEqual(len(self.store.list()['fragments']),1)

    def test_readback_fault_retains_published_record(self):
        with patch('nivram.fragments._read', side_effect=OSError('readback failed')):
            result=self.store.save('Saved but unconfirmed',GRAPH)
        self.assertEqual(result,{'status':'unconfirmed'})
        self.assertEqual(len(self.store.list()['fragments']),1)

    def test_exclusive_collision_preserves_old_bytes(self):
        one=self.store.save('one',GRAPH); original=list(self.root.iterdir())[0].read_bytes()
        with patch('nivram.fragments.uuid.uuid4') as uid:
            uid.return_value.hex=one['fragment']['id']
            self.assertEqual(self.store.save('two',GRAPH),{'status':'failed'})
        self.assertEqual(list(self.root.iterdir())[0].read_bytes(),original)

    def test_huge_numeric_record_is_retained_without_hiding_valid_records(self):
        good = self.store.save('Good', GRAPH)['fragment']
        malformed = {'schema': 'nivram-fragment/1', 'id': 'f' * 32,
                     'name': 'Bad', 'graph': copy.deepcopy(GRAPH)}
        malformed['graph']['atoms'][0]['x'] = 10 ** 400
        path = self.root / ('f' * 32 + '.json')
        data = json.dumps(malformed).encode()
        path.write_bytes(data)
        listing = self.store.list()
        self.assertEqual(listing, {'fragments': [good], 'invalidRecords': 1})
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.store.save('Blocked by invalid record', GRAPH), {'status': 'failed'})
        for field in ('x', 'y', 'charge', 'mapNum'):
            for value in (10 ** 400, -(10 ** 400)):
                bad = copy.deepcopy(GRAPH)
                bad['atoms'][0][field] = value
                with self.assertRaises(ValueError):
                    validate_graph(bad)

    def test_every_directory_entry_is_bounded_without_deletion(self):
        good = self.store.save('Good', GRAPH)['fragment']
        record_path = self.root / (good['id'] + '.json')
        original = record_path.read_bytes()
        for i in range(MAX_DIRECTORY_ENTRIES - 1):
            (self.root / f'unrelated-{i}.txt').write_bytes(b'retain')
        self.assertEqual(self.store.list(), {'fragments': [good], 'invalidRecords': 0})
        (self.root / 'over-limit.tmp').write_bytes(b'retain')
        with self.assertRaisesRegex(ValueError, 'entry limit'):
            self.store.list()
        self.assertEqual(self.store.save('Blocked', GRAPH), {'status': 'failed'})
        self.assertEqual(record_path.read_bytes(), original)
        self.assertEqual(len(list(self.root.iterdir())), MAX_DIRECTORY_ENTRIES + 1)

    def test_save_reserves_staging_and_final_entries_before_publication(self):
        for entries in (MAX_DIRECTORY_ENTRIES - 2, MAX_DIRECTORY_ENTRIES - 1,
                        MAX_DIRECTORY_ENTRIES):
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for i in range(entries):
                    (root / f'unrelated-{i}').write_bytes(b'retain')
                store = FragmentStore(root)
                with patch('nivram.fragments.atomic_write', wraps=atomic_write) as publish:
                    result = store.save('New', GRAPH)
                    if entries == MAX_DIRECTORY_ENTRIES - 2:
                        self.assertEqual(result['status'], 'saved')
                        publish.assert_called_once()
                        self.assertEqual(store.list(), {'fragments': [result['fragment']], 'invalidRecords': 0})
                        self.assertEqual(len(list(root.iterdir())), entries + 1)
                    else:
                        self.assertEqual(result, {'status': 'failed'})
                        publish.assert_not_called()
                        self.assertEqual(store.list(), {'fragments': [], 'invalidRecords': 0})
                        self.assertEqual(len(list(root.iterdir())), entries)
                for i in range(entries):
                    self.assertEqual((root / f'unrelated-{i}').read_bytes(), b'retain')

    @unittest.skipIf(os.name == 'nt', 'POSIX hard-link coexistence check')
    def test_uncertain_posix_cleanup_retains_both_entries_within_cap(self):
        self.root.mkdir()
        for i in range(MAX_DIRECTORY_ENTRIES - 2):
            (self.root / f'unrelated-{i}').write_bytes(b'retain')
        # Use the real exclusive writer. Fail cleanup after its real hard-link
        # publication so both staging and final entries remain observable.
        with patch('nivram.persistence.os.unlink', side_effect=OSError('cleanup failed')):
            result = self.store.save('Published but uncertain', GRAPH)
        self.assertEqual(result, {'status': 'unconfirmed'})
        self.assertEqual(len(list(self.root.iterdir())), MAX_DIRECTORY_ENTRIES)
        listing = self.store.list()
        self.assertEqual(len(listing['fragments']), 1)
        self.assertEqual(listing['invalidRecords'], 0)
        self.assertEqual(self.store.save('No capacity', GRAPH), {'status': 'failed'})

    def test_record_and_total_limits(self):
        self.store.save('one',GRAPH)
        with patch('nivram.fragments.MAX_RECORDS',1):self.assertEqual(self.store.save('two',GRAPH)['status'],'failed')
        with patch('nivram.fragments.MAX_TOTAL_BYTES',1):
            with self.assertRaises(OverflowError):self.store.list()
            self.assertEqual(self.store.save('two',GRAPH)['status'],'failed')


if __name__ == '__main__': unittest.main()
