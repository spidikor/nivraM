"""Finite source-backed annotation boundary checks, not an exhaustive MOL audit."""
from tests.import_binding_fixtures import prepare_fixture
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from rdkit import Chem
from rdkit.Chem import rdDepictor
from nivram.chemistry import ChemistryError, import_structure, mol_to_graph, graph_to_mol
from nivram.import_preparation import _prepare, SCHEMA, ImportPreparationError
from nivram.batch_export import BatchExportManager
from nivram.image_import import inspect_png_metadata
from nivram.optical_import import optical_candidate, OpticalImportError
from test_image_import import png
from test_optical_import import worker_result


def molecule(smiles='CO'):
    params = Chem.SmilesParserParams(); params.removeHs = False
    mol = Chem.MolFromSmiles(smiles, params)
    rdDepictor.Compute2DCoords(mol)
    return mol


def v2000(record):
    return Chem.MolToMolBlock(molecule()).replace('M  END', record + '\nM  END')


def v3000(value):
    lines = Chem.MolToMolBlock(molecule(), forceV3000=True).splitlines()
    return '\n'.join(line + ' ATTCHPT=' + str(value) if line.startswith('M  V30 2 O ') else line for line in lines) + '\n'


def cases():
    return [('alias', v2000('A    2\nOMe'), 'molFileAlias', 'OMe'),
            ('ordinary-alias', v2000('A    2\nOH'), 'molFileAlias', 'OH'),
            ('value', v2000('V    2 attachment-site-alpha'), 'molFileValue', 'attachment-site-alpha'),
            ('attachment', v2000('M  APO  1   2   1'), 'molAttchpt', '1'),
            ('v3000-attachment', v3000(1), 'molAttchpt', '1'),
            ('v3000-negative-attachment', v3000(-1), 'molAttchpt', '-1'),
            ('space-alias', v2000('A    2\n '), 'molFileAlias', ' '),
            ('space-value', v2000('V    2  '), 'molFileValue', ' ')]


class MolAtomAnnotationTests(unittest.TestCase):
    def test_source_and_direct_conversion_reject_without_mutation(self):
        for name, source, prop, value in cases():
            with self.subTest(case=name):
                mol = Chem.MolFromMolBlock(source, removeHs=False, strictParsing=True)
                self.assertIsNotNone(mol)
                self.assertTrue(mol.GetAtomWithIdx(1).HasProp(prop))
                self.assertEqual(mol.GetAtomWithIdx(1).GetProp(prop), value)
                before = Chem.MolToMolBlock(mol)
                for convert in (lambda: import_structure(source), lambda: mol_to_graph(mol)):
                    with self.assertRaisesRegex(ChemistryError, 'source editor'):
                        convert()
                self.assertEqual(Chem.MolToMolBlock(mol), before)

    def test_direct_properties_and_combinations(self):
        for props in ({'molFileAlias': 'OMe'}, {'molFileValue': 'site'}, {'molAttchpt': '1'},
                      {'molFileAlias': '', 'molFileValue': 'site', 'molAttchpt': '0'},
                      {'molFileAlias': 'OH', 'molFileValue': 'site', 'molAttchpt': '1'}):
            with self.subTest(props=props):
                mol = molecule()
                for key, value in props.items(): mol.GetAtomWithIdx(1).SetProp(key, value)
                with self.assertRaises(ChemistryError): mol_to_graph(mol)

    def test_empty_text_and_default_attachment_controls(self):
        control = import_structure(Chem.MolToMolBlock(molecule()))
        for record, prop in [('A    2\n', 'molFileAlias'), ('V    2 ', 'molFileValue')]:
            source = v2000(record); mol = Chem.MolFromMolBlock(source, removeHs=False)
            self.assertTrue(mol.GetAtomWithIdx(1).HasProp(prop))
            self.assertEqual(mol.GetAtomWithIdx(1).GetProp(prop), '')
            self.assertEqual(import_structure(source), control)
        for source in (v2000('M  APO  1   2   0'), v3000(0)):
            mol = Chem.MolFromMolBlock(source, removeHs=False)
            self.assertFalse(mol.GetAtomWithIdx(1).HasProp('molAttchpt'))
            self.assertEqual(import_structure(source), control)
        mol = molecule(); atom = mol.GetAtomWithIdx(1)
        atom.SetProp('molFileAlias', ''); atom.SetProp('molFileValue', '')
        atom.SetIntProp('molAttchpt', 0); atom.SetProp('_unrelatedInternalCache', 'keep allowed')
        self.assertEqual(mol_to_graph(mol), mol_to_graph(molecule()))

    def test_supported_fields_coordinates_and_identity(self):
        for smiles in ['CO', '[CH3:3][OH:7]', '[13CH3]O', 'C[NH3+]', 'F[C@](Cl)(Br)I',
                       'F/C=C/F', '[CH3]', '[H:1][O:2][H:3]']:
            for v3 in (False, True):
                with self.subTest(smiles=smiles, v3000=v3):
                    source = Chem.MolToMolBlock(molecule(smiles), forceV3000=v3)
                    parsed = Chem.MolFromMolBlock(source, removeHs=False)
                    graph = import_structure(source); rebuilt = graph_to_mol(graph)
                    self.assertEqual(Chem.MolToSmiles(parsed, isomericSmiles=True), Chem.MolToSmiles(rebuilt, isomericSmiles=True))
                    for atom, original, point in zip(graph['atoms'], parsed.GetAtoms(), parsed.GetConformer().GetPositions()):
                        self.assertEqual((atom['x'], atom['y']), tuple(point[:2]))
                        self.assertEqual((atom['charge'], atom['isotope'], atom['mapNum'], atom['radicals']),
                                         (original.GetFormalCharge(), original.GetIsotope(), original.GetAtomMapNum(), original.GetNumRadicalElectrons()))
                        self.assertEqual((atom['explicitHs'], atom['noImplicit'], atom['chiralTag']),
                                         (original.GetNumExplicitHs(), original.GetNoImplicit(), int(original.GetChiralTag())))

    def test_png_reports_unsupported_without_graph(self):
        for name, source, _, _ in cases():
            with self.subTest(case=name):
                data = png([(b'tEXt', b'MOL rdkit 2025.09.4\0' + source.encode())])
                result = inspect_png_metadata(data)
                self.assertEqual(result['candidates'], [])
                self.assertEqual(result['documentStatus'], 'partial_or_unsupported')
                self.assertIn('source editor', str(result['unsupportedSources']))

    def test_optical_alias_contract_and_other_annotations(self):
        for name, source, prop, _ in cases():
            with self.subTest(case=name):
                result = worker_result('CO', molfile=source); before = copy.deepcopy(result)
                with self.assertRaises(OpticalImportError) as raised: optical_candidate(result)
                if prop == 'molFileAlias': self.assertEqual(raised.exception.code, 'unsupported_abbreviation')
                self.assertEqual(result, before)

    def test_worker_preparation_rejects_before_rendering(self):
        for _, source, _, _ in cases():
            for mode in ('import', 'batch'):
                request = {'schema': SCHEMA, 'mode': mode, 'source': source}
                request.update({'style': {}} if mode == 'import' else {'extension': '.mol', 'settings': SETTINGS})
                before = copy.deepcopy(request)
                with patch('nivram.scene.render_scene', side_effect=AssertionError('must reject before render')):
                    with self.assertRaises(ImportPreparationError) as raised: prepare_fixture(request)
                self.assertEqual(raised.exception.code, 'unsupported_atom_annotation')
                self.assertEqual(request, before)

    def test_allowlisted_worker_error_and_cancellation_precedence(self):
        from test_import_preparation import FakeSupervisor
        from nivram.import_preparation import ImportPreparationService
        from nivram.worker_processes import CancellationToken, WorkerCancelled
        envelope = {'schema': SCHEMA, 'status': 'error', 'code': 'unsupported_atom_annotation'}
        service = ImportPreparationService(supervisor=FakeSupervisor(envelope))
        try:
            with self.assertRaises(ImportPreparationError) as raised: service.prepare_import(cases()[0][1])
            self.assertEqual(raised.exception.code, 'unsupported_atom_annotation')
            self.assertEqual(raised.exception.http_status, 422)
            self.assertIn('source editor', str(raised.exception))
            token = CancellationToken(); token.cancel()
            with self.assertRaises(WorkerCancelled): service.prepare_import(cases()[0][1], cancellation_token=token)
        finally: service.shutdown()

    def test_batch_rejection_is_per_file_and_source_is_preserved(self):
        # Controlled same-process worker seam. No claim of Windows isolation or rendering.
        class Service:
            def prepare_batch(self, text, extension, settings, **kwargs):
                return prepare_fixture({'schema': SCHEMA, 'mode': 'batch', 'source': text,
                                 'extension': extension, 'settings': settings})
            def shutdown(self, **kwargs): return True
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); destination = root / 'out'; destination.mkdir()
            sources = []
            for name, source, _, _ in cases()[:5]:
                path = root / (name + '.mol'); path.write_text(source); sources.append(path)
            manager = BatchExportManager(preparation_service=Service())
            try:
                batch = manager._begin_selected(sources, destination, SETTINGS)
                for item, path in zip(batch['items'], sources):
                    before = path.read_bytes(); result = manager.prepare(batch['sessionId'], item['id'])
                    self.assertEqual(result['item']['status'], 'failed')
                    self.assertEqual(result['item']['error']['code'], 'unsupported_atom_annotation')
                    self.assertEqual(path.read_bytes(), before)
                    self.assertNotIn('graph', result)
                self.assertFalse(list(destination.glob('*.png')))
            finally: manager.close()


SETTINGS = {'style': {}, 'export': {'width': 32, 'marginMode': 'pixels', 'margin': 2}}
if __name__ == '__main__': unittest.main()
