"""Pure transport tests are not native RDKit chemistry validation."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

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
        supervisor = WorkerSupervisor(max_workers=1)
        try:
            result = run_request(request(import_structure('FC=CF')), supervisor, CancellationToken())
            self.assertIn('denial', result); self.assertNotIn('scene', result)
        finally: supervisor.shutdown()

    def test_malformed_chemistry_is_not_stereo_diagnostic(self):
        from nivram.chemistry import import_structure
        from nivram.conformer import generate
        graph = import_structure('CC'); graph['bonds'][0]['order'] = 3
        graph['atoms'][0]['explicitHs'] = 4
        with self.assertRaises(ValueError) as caught: generate(request(graph))
        self.assertNotIsInstance(caught.exception, UnresolvedStereoError)

if __name__ == '__main__': unittest.main()
