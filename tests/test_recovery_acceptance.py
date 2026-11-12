"""Preparing recovery cannot detach a saved destination before page acceptance."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from nivram.desktop import DesktopApi
from test_persistence_crash import project


class RecoveryAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.api=DesktopApi(self.root/'profile')
        for name in ('_images','_batch','_reference_exports','_population_exports'):
            close=getattr(getattr(self.api,name),'close',None)
            if close is not None:self.addCleanup(close)
        self.api._begin_recovery_page()
        self.path=self.root/'known.nivram'
        self.saved=self.api._store.save(self.path,project(1000));self.api._path=self.path
        self.original=self.path.read_bytes()
        context=self.api.recovery_context()
        self.recovery=self.api.autosave_project(project(1200),context['generation'],1)

    def prepare(self):
        result=self.api.recover_project(self.recovery['id'])
        self.assertEqual(result['status'],'recovered')
        self.assertEqual(result['document'],project(1200))
        self.assertRegex(result['recovery_id'],r'^[a-f0-9]{32}$')
        self.assertEqual(self.api._path,self.path)
        return result['recovery_id']

    def test_read_only_preparation_then_one_acceptance_detaches_destination(self):
        token=self.prepare()
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual(self.api.accept_recovery(token),{'status':'accepted'})
        self.assertIsNone(self.api._path)
        with self.assertRaises(ValueError):self.api.accept_recovery(token)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_failed_read_preserves_existing_destination(self):
        with self.assertRaises((ValueError,OSError)):self.api.recover_project('999-'+'f'*32)
        self.assertEqual(self.api._path,self.path)
        self.assertIsNone(self.api._pending_recovery)

    def test_newer_preparation_revokes_earlier_token(self):
        old=self.prepare();new=self.prepare()
        self.assertNotEqual(old,new)
        with self.assertRaises(ValueError):self.api.accept_recovery(old)
        self.assertEqual(self.api._path,self.path)
        self.api.accept_recovery(new);self.assertIsNone(self.api._path)

    def test_new_host_page_revokes_unaccepted_recovery(self):
        token=self.prepare();self.api._begin_recovery_page()
        with self.assertRaises(ValueError):self.api.accept_recovery(token)
        self.assertEqual(self.api._path,self.path)

    def test_generation_mismatch_rejects_even_a_matching_token(self):
        token=self.prepare();self.api._store.begin_recovery_generation()
        with self.assertRaises(ValueError):self.api.accept_recovery(token)
        self.assertEqual(self.api._path,self.path)

    def test_successful_save_invalidates_prepared_recovery(self):
        token=self.prepare()
        self.api._store.save=Mock(return_value={'status':'saved','name':self.path.name,'disk_hash':'a'*64})
        self.api.save_project(project(1300),self.saved['disk_hash'])
        with self.assertRaises(ValueError):self.api.accept_recovery(token)
        self.assertEqual(self.api._path,self.path)

    def test_accepted_open_invalidates_prepared_recovery(self):
        token=self.prepare()
        other=self.root/'other.nivram';self.api._pending_open=('open',other)
        self.api.accept_open('open')
        with self.assertRaises(ValueError):self.api.accept_recovery(token)
        self.assertEqual(self.api._path,other)

    def test_inventory_reports_through_bridge_without_changing_destination(self):
        inventory=self.api.recovery_inventory()
        self.assertEqual(inventory['records'],self.api.list_recovery())
        self.assertEqual(inventory['status'],'ready')
        self.assertEqual(self.api._path,self.path)


if __name__=='__main__':unittest.main()

