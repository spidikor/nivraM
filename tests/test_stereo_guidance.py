"""Pure transport tests are not native RDKit chemistry validation."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nivram.stereo_guidance import map_unresolved, validate_denial, UnresolvedStereoError
from nivram.conformer import run_request

GRAPH = {'atoms': [{'id': 'a7', 'symbol': 'C'}, {'id': 'a2', 'symbol': 'C'}],
         'bonds': [{'id': 'b9', 'begin': 'a7', 'end': 'a2', 'order': 2}]}


def request(graph=GRAPH):
    text = json.dumps(graph)
    return dict(graphText=text, sourceHash=hashlib.sha256(text.encode()).hexdigest(), sourceRevision=7,
                sourceContextText='null', sourceContextHash=hashlib.sha256(b'null').hexdigest(),
                clientId='a'*32, jobId='b'*32, expiresAt=int(time.time()*1000)+30000)


class MappingTests(unittest.TestCase):
    def test_exact_source_ids_and_nonmutation(self):
        before = copy.deepcopy(GRAPH)
        denial = map_unresolved(GRAPH, [('Atom_Tetrahedral', 1), ('Bond_Double', 0)])
        self.assertEqual(denial['targets'], [{'kind': 'atom', 'id': 'a2', 'notation': 'R/S?'},
                                           {'kind': 'bond', 'id': 'b9', 'notation': 'E/Z?'}])
        self.assertEqual(validate_denial(denial, GRAPH), denial)
        self.assertEqual(GRAPH, before)

    def test_unknown_out_of_range_wrong_order_and_duplicate(self):
        graph = copy.deepcopy(GRAPH); graph['bonds'][0]['order'] = 1
        denial = map_unresolved(graph, [('Atom_Tetrahedral', 1), ('Atom_Tetrahedral', 1),
            ('Atom_SquarePlanar', 0), ('Bond_Double', 0), ('Atom_Tetrahedral', -1),
            ('Atom_Tetrahedral', True), ('Atom_Tetrahedral', 99)])
        self.assertEqual(len(denial['targets']), 1)
        self.assertEqual(denial['unsupportedCount'], 5)
        validate_denial(denial, graph)

    def test_malformed_diagnostic(self):
        base = map_unresolved(GRAPH, [('Atom_Tetrahedral', 1)])
        cases = [None, {}, dict(base, unsupportedCount=True), dict(base, unsupportedCount=-1),
                 dict(base, targets=[]), dict(base, unexpected=1), dict(base, targets=[None]),
                 dict(base, targets=base['targets']*2)]
        for field, value in [('id', 'missing'), ('notation', 'cis/trans?'), ('kind', []), ('id', [])]:
            bad = copy.deepcopy(base); bad['targets'][0][field] = value; cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate_denial(bad, GRAPH)

    def test_bound_transport_and_stale_failure(self):
        data = request(); denial = map_unresolved(GRAPH, [('Bond_Double', 0)])
        result = {k: data[k] for k in ['sourceHash', 'sourceRevision', 'sourceContextHash']}
        result['denial'] = denial
        supervisor = Mock(); supervisor.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(result))
        self.assertEqual(run_request(data, supervisor, object()), result)
        for field in ['sourceHash', 'sourceRevision', 'sourceContextHash']:
            bad = dict(result, **{field: 'wrong'})
            supervisor.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(bad))
            with self.subTest(field=field), self.assertRaises(ValueError): run_request(data, supervisor, object())
        for bad in [None, [], dict(result, scene={}), dict(result, denial={})]:
            supervisor.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(bad))
            with self.assertRaises(ValueError): run_request(data, supervisor, object())
        supervisor.run.return_value = SimpleNamespace(returncode=2, stdout=json.dumps({'error': 'malformed chemistry'}))
        with self.assertRaisesRegex(ValueError, 'malformed chemistry'): run_request(data, supervisor, object())


@unittest.skipUnless(importlib.util.find_spec('rdkit'), 'RDKit unavailable; native chemistry checks not run')
class NativeChemistryTests(unittest.TestCase):
    def test_crossed_double_from_mol_is_denied_before_embedding(self):
        from rdkit import Chem
        from rdkit.Chem import rdDistGeom
        from nivram.chemistry import graph_to_mol, import_structure
        from nivram.conformer import generate
        from tests.test_mol_stereo_roundtrip import mol_fixture
        for v3000 in (False, True):
            for cis in (False, True):
                with self.subTest(v3000=v3000, cis_geometry=cis):
                    graph = import_structure(mol_fixture(v3000=v3000, unknown=True, cis=cis))
                    bond = graph['bonds'][1]
                    self.assertEqual(bond['direction'], int(Chem.BondDir.EITHERDOUBLE))
                    info, = Chem.FindPotentialStereo(graph_to_mol(graph))
                    self.assertEqual((info.type, info.specified, info.centeredOn),
                                     (Chem.StereoType.Bond_Double, Chem.StereoSpecified.Unknown, 1))
                    data = request(graph)
                    before = copy.deepcopy(data)
                    with patch.object(rdDistGeom, 'EmbedMolecule', wraps=rdDistGeom.EmbedMolecule) as embed:
                        with self.assertRaises(UnresolvedStereoError) as caught:
                            generate(data)
                        embed.assert_not_called()
                    denial = validate_denial(caught.exception.denial, graph)
                    self.assertEqual(denial['targets'], [{'kind': 'bond', 'id': bond['id'], 'notation': 'E/Z?'}])
                    self.assertEqual(denial['unsupportedCount'], 0)
                    self.assertEqual(data, before)

    def test_specified_e_and_z_still_generate_the_requested_isomer(self):
        from rdkit import Chem
        from nivram.chemistry import import_structure
        from nivram.conformer import generate
        for smiles in ('F/C=C/F', 'F/C=C\\F'):
            with self.subTest(smiles=smiles):
                result = generate(request(import_structure(smiles)))
                self.assertEqual(result['scene']['kind'], 'generated_coordinates')
                self.assertEqual(Chem.MolToSmiles(Chem.MolFromMolBlock(result['scene']['sourceText'])), smiles)

    def test_real_unresolved_atom_and_bond(self):
        from nivram.chemistry import import_structure
        from nivram.conformer import generate
        for smiles, kind in [('CC(O)C(=O)O', 'atom'), ('FC=CF', 'bond')]:
            graph = import_structure(smiles)
            with self.assertRaises(UnresolvedStereoError) as caught: generate(request(graph))
            denial = caught.exception.denial
            self.assertEqual([x['kind'] for x in denial['targets']], [kind])
            validate_denial(denial, graph)

    def test_actual_worker_transport(self):
        from nivram.chemistry import import_structure
        from nivram.worker_processes import WorkerSupervisor, CancellationToken
        from tests.test_mol_stereo_roundtrip import mol_fixture
        supervisor = WorkerSupervisor(max_workers=1)
        try:
            for source in ('FC=CF', mol_fixture(v3000=True, unknown=True)):
                graph = import_structure(source)
                result = run_request(request(graph), supervisor, CancellationToken())
                self.assertNotIn('scene', result)
                self.assertEqual(result['denial']['targets'],
                                 [{'kind': 'bond', 'id': graph['bonds'][1]['id'], 'notation': 'E/Z?'}])
        finally: supervisor.shutdown()

    def test_malformed_chemistry_is_not_stereo_diagnostic(self):
        from nivram.chemistry import import_structure
        from nivram.conformer import generate
        graph = import_structure('CC'); graph['bonds'][0]['order'] = 3
        graph['atoms'][0]['explicitHs'] = 4
        with self.assertRaises(ValueError) as caught: generate(request(graph))
        self.assertNotIsInstance(caught.exception, UnresolvedStereoError)

if __name__ == '__main__': unittest.main()
