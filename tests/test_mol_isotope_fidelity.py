"""Independently frozen isotope/map identity and transient-input regressions.

The oracle predates this repair. Policy corrections are explicit and do not
regenerate or erase original fixture bytes. Finite cases are not exhaustive PBT.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from rdkit import Chem
from nivram.chemistry import import_structure, graph_to_mol
from nivram.import_limits import prepare_mol
from nivram.optical_import import optical_candidate, MODEL_SHA256
from nivram.optical_validation import _command, validate_optical_result
from nivram.worker_processes import WorkerSupervisor, CancellationToken, WorkerCancelled

ROOT = Path(__file__).parent
ORIGINAL = json.loads((ROOT / 'mol_isotope_fixtures.json').read_text())
CORRECTIONS = json.loads((ROOT / 'mol_isotope_fixture_corrections.json').read_text())
CORRECTIONS.update(json.loads((ROOT / 'mol_isotope_authoring_corrections.json').read_text()))
CASES = [dict(case, **CORRECTIONS.get(case['name'], {})) for case in ORIGINAL]
BY_NAME = {case['name']: case for case in CASES}
BASE = BY_NAME['absolute_v2_13']['molfile'].replace('M  ISO  1   1  13\n', '')
V3 = BY_NAME['absolute_v3_+13']['molfile']


def payload(block, smiles):
    return dict(schemaVersion=1, method='MolScribe', modelSha256=MODEL_SHA256,
                inputSha256='0'*64, confidence=None, smiles=smiles, molfile=block)


def descriptors(mol):
    return dict(canonical_isomeric_smiles=Chem.MolToSmiles(mol, True),
        atoms=[dict(atomic_number=a.GetAtomicNum(), isotope=a.GetIsotope(),
                    charge=a.GetFormalCharge(), radicals=a.GetNumRadicalElectrons(),
                    map=a.GetAtomMapNum(), totalHs=a.GetTotalNumHs(),
                    chiral=int(a.GetChiralTag())) for a in mol.GetAtoms()],
        bonds=[dict(ends=[b.GetBeginAtomIdx(), b.GetEndAtomIdx()],
                    order=b.GetBondTypeAsDouble(), stereo=int(b.GetStereo()),
                    stereo_atoms=list(b.GetStereoAtoms())) for b in mol.GetBonds()])


def chemical_bonds(bonds):
    # MOL wedging can reverse bond storage direction without changing topology.
    # Reverse stereo-neighbor order with the endpoints, never discard stereo.
    result = copy.deepcopy(bonds)
    for bond in result:
        if bond['ends'][0] > bond['ends'][1]:
            bond['ends'].reverse()
            bond['stereo_atoms'].reverse()
    return sorted(result, key=lambda bond: bond['ends'])


class MolIsotopeFidelityTests(unittest.TestCase):
    def test_frozen_whole_graph_identity_and_optical_counterparts(self):
        for case in CASES:
            with self.subTest(case=case['name']):
                block = case['molfile']
                self.assertEqual(hashlib.sha256(block.encode()).hexdigest(), case['source_sha256'])
                if case['policy'] == 'reject':
                    with self.assertRaises(ValueError): import_structure(block)
                    with self.assertRaises(ValueError): optical_candidate(payload(block, 'C'))
                    continue
                if not case.get('expected_smiles'):
                    continue  # documented native boundary, not an identity oracle
                graph = import_structure(block)
                actual = descriptors(graph_to_mol(graph))
                expected = case['expected_graph']
                for key, value in expected.items():
                    self.assertEqual(chemical_bonds(actual[key]) if key == 'bonds' else actual[key],
                                     chemical_bonds(value) if key == 'bonds' else value)
                p = payload(block, case['expected_smiles'])
                original = copy.deepcopy(p)
                candidate = optical_candidate(p)
                self.assertEqual(p, original)
                # Optical graph uses its own IDs; compare chemistry rather than IDs.
                result = descriptors(graph_to_mol(candidate['graph']))
                for key, value in expected.items():
                    self.assertEqual(chemical_bonds(result[key]) if key == 'bonds' else result[key],
                                     chemical_bonds(value) if key == 'bonds' else value)
                if case.get('wrong_smiles'):
                    with self.assertRaises(ValueError):
                        optical_candidate(payload(block, case['wrong_smiles']))

    def test_preparation_idempotence_and_line_endings(self):
        for case in CASES:
            if case['policy'] == 'reject': continue
            for ending in ('\n', '\r\n'):
                block = case['molfile'].replace('\n', ending)
                with self.subTest(case=case['name'], ending=repr(ending)):
                    prepared, guards = prepare_mol(block, 512, 1024)
                    self.assertEqual(prepare_mol(prepared, 512, 1024), (prepared, guards))
                    self.assertEqual(prepared.count('\n'), block.count('\n'))
                    self.assertEqual(prepared.count('\r\n'), block.count('\r\n'))

    def test_actual_record_scope_and_exact_transient_copy(self):
        block = BASE.replace('M  END', 'A    1\nM  ISO  1   1 +99\nG    1\nM  ISO  1   1 +88\nS  SKP  1\nM  ISO  1   1 +77\nM  ISO +1  +1 +13\nM  END')
        block = block.replace('\n', '\r\n')
        self.assertEqual(prepare_mol(block, 512, 1024)[0],
                         block.replace('M  ISO +1  +1 +13', 'M  ISO +1  +1  13'))
        for ending in ('M  END', '$$$$'):
            block = BASE.replace('M  END', ending+'\nM  ISO  1   1 +13')
            self.assertEqual(prepare_mol(block, 512, 1024)[0], block)
        for token in ('X="MASS=+77" MASS=+13', 'X="a"MASS=+13'):
            block = V3.replace('MASS=+13', token)
            self.assertEqual(prepare_mol(block, 512, 1024)[0],
                             block.replace('MASS=+13', 'MASS=13'))

    def test_rich_signed_unsigned_exact_graphs(self):
        for case in CASES:
            if not case['name'].startswith('rich_'): continue
            signed = case['molfile']
            lines = signed.split('\n')
            for index, line in enumerate(lines):
                if line.startswith('M  ISO'):
                    # Fixture-only independent spelling control: replace plus
                    # in absolute-mass slots, not arbitrary input numerics.
                    count = int(line[6:9])
                    for start in range(13, 9 + count * 8, 8):
                        line = line[:start] + line[start:start+4].replace('+', ' ') + line[start+4:]
                    lines[index] = line
                elif line.startswith('M  V30 ') and ' MASS=+' in line:
                    lines[index] = line.replace(' MASS=+', ' MASS=')
            unsigned = '\n'.join(lines)
            self.assertNotEqual(signed, unsigned)
            with self.subTest(case=case['name']):
                self.assertEqual(import_structure(signed), import_structure(unsigned))
                self.assertEqual(optical_candidate(payload(signed, case['expected_smiles']))['graph'],
                                 optical_candidate(payload(unsigned, case['expected_smiles']))['graph'])

    def test_native_lifecycles_coordinates_and_source_preserved(self):
        for case in CASES:
            if not case['name'].startswith('rich_'): continue
            block = case['molfile'].replace('\n', '\r\n')
            prepared, _ = prepare_mol(block, 512, 1024)
            native = Chem.MolFromMolBlock
            calls = []
            def capture(text, **kwargs):
                mol = native(text, **kwargs)
                calls.append((text, kwargs, mol.GetConformer().GetPositions().tolist()))
                return mol
            with self.subTest(case=case['name']), patch('nivram.chemistry.Chem.MolFromMolBlock', side_effect=capture):
                ordinary = import_structure(block)
                optical = optical_candidate(payload(block, case['expected_smiles']))['graph']
            self.assertEqual([c[0] for c in calls], [prepared, prepared])
            self.assertEqual([c[1] for c in calls], [
                dict(sanitize=True, removeHs=False, strictParsing=True),
                dict(sanitize=False, removeHs=False, strictParsing=True)])
            for graph, (_, _, positions) in zip((ordinary, optical), calls):
                self.assertEqual([[a['x'], a['y'], 0.0] for a in graph['atoms']], positions)
            self.assertEqual(block, case['molfile'].replace('\n', '\r\n'))

    def test_malformed_integer_fields_reject_before_native(self):
        for field in ('1x', '+', '١', '１', '5', '-4'):
            lines = BASE.split('\n'); lines[4] = lines[4][:34]+field.rjust(2)+lines[4][36:]
            with self.subTest(field=field), patch('nivram.chemistry.Chem.MolFromMolBlock') as native:
                with self.assertRaises(ValueError): import_structure('\n'.join(lines))
                native.assert_not_called()
        for field in ('1x', '1.5', '-', '-1', '١', '１'):
            lines=BASE.split('\n');lines[4]=lines[4][:60]+field.rjust(3)+lines[4][63:]
            with self.subTest(map=field), patch('nivram.chemistry.Chem.MolFromMolBlock') as native:
                with self.assertRaises(ValueError): import_structure('\n'.join(lines))
                native.assert_not_called()
        for value in ('-1','401','+','1x','13.5','"13"','(1 13)','١','１','9999999999'):
            with self.subTest(mass=value), patch('nivram.chemistry.Chem.MolFromMolBlock') as native:
                with self.assertRaises(ValueError): import_structure(V3.replace('MASS=+13','MASS='+value))
                native.assert_not_called()

    def test_real_isolated_validator_success_and_wrong_identity(self):
        names = ['absolute_v2_+13', 'absolute_v3_+13', 'override_other',
                 "v2_map_' +1'", "v2_map_' +0'", 'symbol_other_iso_D']
        for name in names:
            case = BY_NAME[name]
            for smiles, success in ((case['expected_smiles'], True),
                                     (case.get('wrong_smiles') or '[14CH4]', False)):
                with self.subTest(case=name, smiles=smiles):
                    cp = subprocess.run(_command(), input=json.dumps(payload(case['molfile'], smiles)),
                                        text=True, capture_output=True, timeout=15)
                    self.assertEqual(cp.returncode == 0, success, cp.stdout)
                    result=json.loads(cp.stdout)
                    self.assertEqual('candidate' in result, success)

    def test_actual_supervisor_ownership_and_pre_cancelled_isotope(self):
        # Real Linux subprocess supervision, not Windows Job Object qualification.
        case = BY_NAME['override_other']
        raw = json.dumps(payload(case['molfile'], case['expected_smiles']))
        supervisor = WorkerSupervisor(max_workers=1)
        try:
            candidate = validate_optical_result(raw, '0'*64, supervisor, lambda: 10,
                                                CancellationToken())
            self.assertEqual(descriptors(graph_to_mol(candidate['graph']))['canonical_isomeric_smiles'],
                             case['expected_graph']['canonical_isomeric_smiles'])
            self.assertEqual(supervisor.active_count, 0)
            cancelled = CancellationToken()
            cancelled.cancel()
            with self.assertRaises(WorkerCancelled):
                validate_optical_result(raw, '0'*64, supervisor, lambda: 10, cancelled)
            self.assertEqual(supervisor.active_count, 0)
            self.assertEqual(json.loads(raw)['molfile'], case['molfile'])
        finally:
            self.assertTrue(supervisor.shutdown(timeout_seconds=2))


if __name__ == '__main__': unittest.main()
