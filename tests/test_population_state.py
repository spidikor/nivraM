"""Supplied-state preparation contracts; synthetic thermodynamics only."""
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from nivram import population_state as states
from nivram.import_preparation import ImportPreparationService, ImportPreparationError
from nivram.import_protocol import validate_prepared_result
from nivram.worker_processes import WorkerCancelled
from nivram.population_jobs import _complete_model


def request(request_id='state_preparation_1', *, sources=None):
    model_text = (Path(__file__).parents[1] / 'examples/population-analytical-pka7.json').read_text(encoding='utf-8')
    model = json.loads(model_text)
    model_hash = hashlib.sha256(model_text.encode()).hexdigest()
    rows = []
    for state, source in zip(model['states'], sources or ('[CH3:1][OH:2]', '[CH3:1][O-:2]')):
        rows.append({'stateId': state['state_id'], 'structureId': state['structure_id'], 'format': 'smiles',
                     'source': source, 'coreAtomMaps': [{'atomId': 'a0', 'mapNum': 1}, {'atomId': 'a1', 'mapNum': 2}]})
    text = json.dumps({'schemaVersion': 1, 'fileKind': 'nivram-population-state-structures',
                      'modelInputHash': model_hash, 'structures': rows}, ensure_ascii=False)
    return {'schemaVersion': 1, 'requestId': request_id, 'documentRevision': 4, 'graphHash': 'a' * 64,
            'modelText': model_text, 'modelInputHash': model_hash, 'structuresText': text,
            'structuresInputHash': hashlib.sha256(text.encode()).hexdigest(), 'stateId': 'A', 'style': {}}


def change_structures(value, mutate):
    value = copy.deepcopy(value)
    document = json.loads(value['structuresText'])
    mutate(document)
    value['structuresText'] = json.dumps(document)
    value['structuresInputHash'] = hashlib.sha256(value['structuresText'].encode()).hexdigest()
    return value


def change_model(value, mutate):
    value = copy.deepcopy(value)
    model = json.loads(value['modelText'])
    mutate(model)
    value['modelText'] = json.dumps(model)
    value['modelInputHash'] = hashlib.sha256(value['modelText'].encode()).hexdigest()
    return change_structures(value, lambda sidecar: sidecar.update(modelInputHash=value['modelInputHash']))


class PopulationStateValidationTests(unittest.TestCase):
    def test_prepare_preserves_mapped_graph_and_bound_receipt(self):
        value = request()
        original = copy.deepcopy(value)
        result = states.prepare_state(value)
        self.assertEqual(set(result), {'graph', 'scene', 'populationState'})
        receipt = result['populationState']
        self.assertEqual(set(receipt), states.RECEIPT_KEYS)
        self.assertEqual(receipt['observedHydrogenDifference'], -1)
        self.assertEqual(receipt['formalCharge'], -1)
        self.assertEqual(receipt['identity']['canonicalIsomericSmiles'], 'C[O-]')
        self.assertEqual([a['mapNum'] for a in result['graph']['atoms']], [1, 2])
        self.assertEqual(receipt['modelSignature'], _complete_model(value['modelText']).signature)
        self.assertIs(validate_prepared_result(result, mode='population-state'), result)
        self.assertEqual(value, original)

    def test_hash_fields_types_and_transport_bounds_reject_before_chemistry(self):
        base = request()
        variants = [dict(base, modelInputHash='0' * 64), dict(base, structuresInputHash='0' * 64),
                    dict(base, documentRevision=True), dict(base, graphHash='bad'), dict(base, extra=True),
                    dict(base, stateId='\ud800'), dict(base, style={'notStyle': 1}),
                    dict(base, structuresText='x' * (states.MAX_FILE_BYTES + 1)),
                    dict(base, requestId='short')]
        with patch.object(states, '_prepare_structure', side_effect=AssertionError('No chemistry')):
            for index, variant in enumerate(variants):
                with self.subTest(index=index), self.assertRaises((ValueError, UnicodeError)):
                    states.prepare_state(variant)

    def test_sidecar_model_state_and_reference_bindings(self):
        changes = [lambda d: d.update(modelInputHash='b' * 64),
                   lambda d: d.update(fileKind='SMILES'),
                   lambda d: d['structures'].pop(0),
                   lambda d: d['structures'][1].update(structureId='another'),
                   lambda d: d['structures'][1].update(stateId='missing'),
                   lambda d: d['structures'].append(copy.deepcopy(d['structures'][0])),
                   lambda d: d['structures'][1].update(format='auto'),
                   lambda d: d['structures'][1].update(format='mol')]
        for index, change in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(states.PopulationStateError):
                states.prepare_state(change_structures(request(), change))

    def test_map_elements_isotopes_missing_duplicate_and_extra_atoms_reject(self):
        changes = [lambda d: d['structures'][1]['coreAtomMaps'][1].update(atomId='wrong'),
                   lambda d: d['structures'][1]['coreAtomMaps'][1].update(mapNum=1),
                   lambda d: d['structures'][1]['coreAtomMaps'][1].update(mapNum=True),
                   lambda d: d['structures'][1].update(source='[CH3:1][O-]'),
                   lambda d: d['structures'][1].update(source='[CH3:1][O-:1]'),
                   lambda d: d['structures'][1].update(source='[13CH3:1][O-:2]'),
                   lambda d: d['structures'][1].update(source='[CH3:1][S-:2]'),
                   lambda d: d['structures'][1].update(source='[CH3:1][O-:2].[Cl-]'),
                   lambda d: d['structures'][1].update(source='[CH2:1]([2H])[O-:2]')]
        for index, change in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(states.PopulationStateError) as caught:
                states.prepare_state(change_structures(request(), change))
            self.assertEqual(caught.exception.code, 'state_mapping')

    def test_observed_charge_and_relative_hydrogen_counts_must_match(self):
        for sources in (('[CH3:1][OH:2]', '[CH3:1][OH:2]'),
                        ('[CH3:1][OH:2]', '[CH2:1][O-:2]')):
            with self.subTest(sources=sources), self.assertRaises(states.PopulationStateError) as caught:
                states.prepare_state(request(sources=sources))
            self.assertEqual(caught.exception.code, 'state_stoichiometry')

    def test_explicit_mapped_hydrogen_is_retained(self):
        value = request(sources=('[H:3][CH2:1][OH:2]', '[H:3][CH2:1][O-:2]'))
        value = change_model(value, lambda model: [state.update(atom_ids=['a0', 'a1', 'fixedH']) for state in model['states']])
        value = change_structures(value, lambda d: [row['coreAtomMaps'].append({'atomId': 'fixedH', 'mapNum': 3}) for row in d['structures']])
        result = states.prepare_state(value)
        self.assertEqual(len(result['graph']['atoms']), 3)
        self.assertTrue(any(a['symbol'] == 'H' and a['mapNum'] == 3 for a in result['graph']['atoms']))
        self.assertEqual(result['populationState']['observedHydrogenDifference'], -1)

    def test_mol_coordinates_and_stereo_survive_explicit_format_preparation(self):
        from nivram.chemistry import export_mol, import_structure
        from rdkit import Chem
        value = request(sources=('[13CH3:1][C@@H:2]([OH:3])[C:4](=[O:5])[OH:6]',
                                 '[13CH3:1][C@@H:2]([OH:3])[C:4](=[O:5])[O-:6]'))
        value = change_model(value, lambda model: [state.update(atom_ids=[f'core{i}' for i in range(6)]) for state in model['states']])
        value = change_structures(value, lambda d: [row.update(coreAtomMaps=[{'atomId': f'core{i}', 'mapNum': i+1} for i in range(6)]) for row in d['structures']])
        initial = states.prepare_state(value)
        block = export_mol(initial['graph'])
        mol_value = change_structures(value, lambda d: d['structures'][1].update(format='mol', source=block))
        actual = states.prepare_state(mol_value)
        self.assertEqual(actual['graph'], import_structure(block))
        self.assertEqual(actual['populationState']['identity'], initial['populationState']['identity'])
        self.assertIn('@', actual['populationState']['identity']['canonicalIsomericSmiles'])
        self.assertIn('13', actual['populationState']['identity']['canonicalIsomericSmiles'])
        mol = Chem.MolFromMolBlock(block, removeHs=False)
        mol.GetConformer().Set3D(True)
        three_d = Chem.MolToMolBlock(mol, forceV3000=True)
        with self.assertRaises(states.PopulationStateError):
            states.prepare_state(change_structures(value, lambda d: d['structures'][1].update(format='mol', source=three_d)))

    def test_supported_identity_roundtrip_failure_cannot_be_adopted(self):
        with patch('nivram.chemistry.export_mol', return_value='invalid'):
            with self.assertRaises(states.PopulationStateError):
                states.prepare_state(request())

    def test_parent_receipt_validation_rejects_unbounded_or_mismatched_fields(self):
        original = states.prepare_state(request())
        changes = [lambda r: r.update(thermodynamics='verified'),
                   lambda r: r.update(stateId='x' * 8193),
                   lambda r: r['source'].update(sha256='wrong'),
                   lambda r: r['coreAtomMaps'][0].update(element='S'),
                   lambda r: r['coreAtomMaps'][0].update(mapNum=2),
                   lambda r: r['identity'].update(structureHash='b' * 64),
                   lambda r: r.update(formalCharge=0), lambda r: r.update(limitations=[])]
        for index, change in enumerate(changes):
            value = copy.deepcopy(original)
            change(value['populationState'])
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_prepared_result(value, mode='population-state')


class PopulationStateJobTests(unittest.TestCase):
    def test_cancel_before_registration_reuse_and_history_bounds(self):
        prep = Mock()
        owner = states.PopulationStateJobs(prep)
        self.addCleanup(owner.close)
        value = request()
        cancel = {'schemaVersion': 1, 'requestId': value['requestId']}
        self.assertEqual(owner.cancel(cancel)['status'], 'cancelled_before_registration')
        with self.assertRaises(WorkerCancelled):
            owner.prepare(value)
        prep.prepare_population_state.assert_not_called()
        with patch.object(states, 'MAX_HISTORY', 1), self.assertRaises(states.PopulationStateError) as caught:
            owner.cancel({'schemaVersion': 1, 'requestId': 'other_request'})
        self.assertEqual(caught.exception.code, 'history_full')

    def test_active_cancel_stale_result_and_closed_owner(self):
        value = request()
        prepared = states.prepare_state(value)
        prep = Mock()
        owner = states.PopulationStateJobs(prep)
        def prepare(_request, cancellation_token):
            self.assertEqual(owner.snapshot()['activeJobs'], 1)
            with self.assertRaises(states.PopulationStateError) as caught:
                owner.prepare(request('another_request'))
            self.assertEqual(caught.exception.code, 'busy')
            self.assertEqual(owner.cancel({'schemaVersion': 1, 'requestId': value['requestId']})['status'], 'cancellation_requested')
            self.assertTrue(cancellation_token.cancelled)
            return prepared
        prep.prepare_population_state.side_effect = prepare
        with self.assertRaises(WorkerCancelled):
            owner.prepare(value)
        self.assertEqual(owner.snapshot()['activeJobs'], 0)
        owner.close()
        with self.assertRaises(WorkerCancelled):
            owner.prepare(request('after_close'))

    def test_worker_binding_mismatch_and_reused_identifiers_are_rejected(self):
        value = request()
        prepared = states.prepare_state(value)
        prepared['populationState']['requestId'] = 'different_request'
        owner = states.PopulationStateJobs(Mock(prepare_population_state=Mock(return_value=prepared)))
        self.addCleanup(owner.close)
        with self.assertRaises(states.PopulationStateError) as caught:
            owner.prepare(value)
        self.assertEqual(caught.exception.code, 'worker_failed')
        with self.assertRaises(states.PopulationStateError) as caught:
            owner.prepare(value)
        self.assertEqual(caught.exception.code, 'reused_request')


@unittest.skipUnless(os.name == 'nt', 'Protected worker quotas require Windows.')
class PopulationStateWorkerTests(unittest.TestCase):
    def test_real_frozen_entry_dispatch_quota_receipt_and_rejection_recovery(self):
        root = Path(__file__).parents[1]
        service = ImportPreparationService(command_factory=lambda: [sys.executable, '-B', '-s', str(root / 'run_worker.py'), '--prepare-import'])
        try:
            actual = service.prepare_population_state(request())
            self.assertEqual(actual['populationState']['identity']['canonicalIsomericSmiles'], 'C[O-]')
            self.assertTrue(service.snapshot()['lastResourceLimits']['verified'])
            invalid = change_structures(request(), lambda d: d['structures'][1].update(source='[CH3:1][OH:2]'))
            with self.assertRaises(ImportPreparationError) as caught:
                service.prepare_population_state(invalid)
            self.assertEqual(caught.exception.code, 'state_stoichiometry')
            self.assertEqual(service.prepare_population_state(request('another_valid'))['populationState']['stateId'], 'A')
        finally:
            self.assertTrue(service.shutdown())

    def test_http_requires_authentication_and_returns_bound_real_worker_preview(self):
        from nivram.server import make_server
        server = make_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def post(route, value, override=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=25)
            headers = {'Content-Type': 'application/json', 'X-Nivram-Token': server.api_token,
                       'Origin': f'http://127.0.0.1:{server.server_port}'}
            headers.update(override or {})
            try:
                connection.request('POST', route, body=json.dumps(value).encode(), headers=headers)
                response = connection.getresponse()
                raw = response.read()
                return response.status, (json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json')
                                         else {'contentType': response.getheader('Content-Type'), 'bodyPrefix': raw[:100].decode(errors='replace')})
            finally:
                connection.close()
        try:
            value = request('actual_http_state')
            for route, payload in (('/api/populations/prepare-state', value),
                                   ('/api/populations/cancel-state', {'schemaVersion': 1, 'requestId': value['requestId']})):
                for override in ({'X-Nivram-Token': 'wrong'}, {'Origin': 'https://example.invalid'}, {'Host': 'attacker.invalid'}):
                    self.assertEqual(post(route, payload, override)[0], 403)
            status, result = post('/api/populations/prepare-state', value)
            self.assertEqual(status, 200, result)
            self.assertEqual(result['populationState']['modelInputHash'], value['modelInputHash'])
            self.assertEqual(result['populationState']['observedHydrogenDifference'], -1)
            cancel = {'schemaVersion': 1, 'requestId': 'http_cancelled_state'}
            self.assertEqual(post('/api/populations/cancel-state', cancel)[1]['status'], 'cancelled_before_registration')
            self.assertEqual(post('/api/populations/prepare-state', request(cancel['requestId']))[0], 409)
            self.assertEqual(server.population_state_jobs.snapshot()['activeJobs'], 0)
        finally:
            server.shutdown(); server.server_close(); thread.join(3)
        self.assertFalse(server.population_state_jobs.snapshot()['acceptingJobs'])
        self.assertTrue(server.worker_shutdown_receipt['ownedProcessesExited'])


if __name__ == '__main__':
    unittest.main()
