"""Actual JavaScript deletion to RDKit validation, without renderer or browser.

Requires the existing Node and RDKit test runtimes. These synthetic edit cases
do not qualify native GUI behavior or establish experimental chemistry claims.
"""
import copy
import json
from pathlib import Path
import subprocess
import unittest

from rdkit import Chem
from nivram.chemistry import ChemistryError, export_mol, graph_to_mol, import_structure, mol_to_graph

ROOT = Path(__file__).resolve().parents[1]
NODE = """
import {deleteEntities} from './public/modules/edit-commands.mjs';
let input='';for await(const part of process.stdin)input+=part;
const cases=JSON.parse(input);
process.stdout.write(JSON.stringify(cases.map(({graph,selection})=>deleteEntities(graph,selection))));
"""


def delete(cases):
    run = subprocess.run(['node', '--input-type=module', '-e', NODE],
                         input=json.dumps(cases), text=True, capture_output=True,
                         cwd=ROOT, check=True, timeout=15)
    return json.loads(run.stdout)


def canonical(text):
    return Chem.MolToSmiles(Chem.MolFromSmiles(text), isomericSmiles=True)


def reference_edge(graph, double, side):
    endpoint = double['begin'] if side == 0 else double['end']
    reference = double['stereoAtoms'][side]
    return next(bond for bond in graph['bonds']
                if {bond['begin'], bond['end']} == {endpoint, reference})


class StereoDeletionTests(unittest.TestCase):
    def test_e_and_z_reference_bond_deletion_is_accepted_in_either_orientation(self):
        cases = []
        for smiles in ('C/C=C/C', 'C/C=C\\C'):
            for reverse in (False, True):
                for side in (0, 1):
                    graph = import_structure(smiles)
                    double = next(b for b in graph['bonds'] if b['stereo'] > 1)
                    if reverse:
                        double['begin'], double['end'] = double['end'], double['begin']
                        double['stereoAtoms'].reverse()
                        for bond in graph['bonds']:
                            if bond is not double:
                                bond['begin'], bond['end'] = bond['end'], bond['begin']
                                # This is an orientation test of the reference
                                # pair, not a rewrite of SMILES direction flags.
                                bond['direction'] = 0
                    graph_to_mol(graph)  # The source itself must be valid.
                    cases.append(dict(graph=graph, selection={'bondIds': [reference_edge(graph, double, side)['id']]}))
        before = copy.deepcopy(cases)
        for case, result in zip(cases, delete(cases)):
            with self.subTest(selection=case['selection'], source=case['graph']):
                mol = graph_to_mol(result['graph'])
                self.assertEqual(Chem.MolToSmiles(mol, isomericSmiles=True), canonical('C.C=CC'))
                self.assertEqual(result['graph']['atoms'], case['graph']['atoms'])
                self.assertTrue(result['changed'])
                self.assertTrue(all(b.GetStereo() == Chem.BondStereo.STEREONONE for b in mol.GetBonds()))
                self.assertTrue(all(b.GetBondDir() == Chem.BondDir.NONE for b in mol.GetBonds()))
        self.assertEqual(cases, before)

    def test_reference_atom_deletion_remains_accepted(self):
        graph = import_structure('C/C=C/C')
        double = next(b for b in graph['bonds'] if b['stereo'] > 1)
        cases = [dict(graph=graph, selection={'atomIds': [reference]}) for reference in double['stereoAtoms']]
        for result in delete(cases):
            self.assertEqual(Chem.MolToSmiles(graph_to_mol(result['graph']), isomericSmiles=True), canonical('C=CC'))

    def test_alternative_substituent_does_not_invent_new_stereo(self):
        graph = import_structure('C/C=C(/C)Cl')
        double = next(b for b in graph['bonds'] if b['stereo'] > 1)
        # Exercise both loss of stereogenicity and a same-endpoint alternative
        # that must not be silently adopted as a replacement reference.
        atoms = {a['id']: a for a in graph['atoms']}
        for symbol, expected in [('C', 'C.CC(Cl)=C'), ('Cl', 'CC=CC.Cl')]:
            with self.subTest(removed_reference=symbol):
                side = next(i for i, ref in enumerate(double['stereoAtoms']) if atoms[ref]['symbol'] == symbol)
                edge = reference_edge(graph, double, side)
                result = delete([dict(graph=graph, selection={'bondIds': [edge['id']]})])[0]
                mol = graph_to_mol(result['graph'])
                self.assertEqual(Chem.MolToSmiles(mol, isomericSmiles=True), canonical(expected))
                self.assertTrue(all(b.GetStereo() == Chem.BondStereo.STEREONONE for b in mol.GetBonds()))

    def test_unaffected_stereo_isotope_charge_mapping_and_coordinates_survive(self):
        graph = import_structure('C/C=C/C.F/C=C\\F.N[C@@H](C)C(=O)O.[13CH4:17].[Na+]')
        double = next(b for b in graph['bonds'] if b['stereo'] > 1)
        selected = reference_edge(graph, double, 0)['id']
        result = delete([dict(graph=graph, selection={'bondIds': [selected]})])[0]
        mol = graph_to_mol(result['graph'])
        expected = canonical('C.C=CC.F/C=C\\F.N[C@@H](C)C(=O)O.[13CH4:17].[Na+]')
        self.assertEqual(Chem.MolToSmiles(mol, isomericSmiles=True), expected)
        self.assertEqual(result['graph']['atoms'], graph['atoms'])
        normalized = mol_to_graph(mol, [a['id'] for a in result['graph']['atoms']],
                                  [b['id'] for b in result['graph']['bonds']])
        self.assertEqual(normalized['atoms'], result['graph']['atoms'])
        self.assertEqual(sum(b['stereo'] > 1 for b in normalized['bonds']), 1)

    def test_shared_direction_preserves_neighboring_e_and_z(self):
        for source, expected in [
            ('C/C=C(Cl)/C=C/C', 'CC=C/C=C/C.Cl'),
            ('C/C=C(Cl)/C=C\\C', 'CC=C/C=C\\C.Cl'),
            ('C/C=C(Cl)/C=C/C=C/C', 'CC=C/C=C/C=C/C.Cl'),
        ]:
            graph = import_structure(source)
            chlorine = next(a['id'] for a in graph['atoms'] if a['symbol'] == 'Cl')
            edge = next(b for b in graph['bonds'] if chlorine in (b['begin'], b['end']))
            before = copy.deepcopy(graph)
            result = delete([dict(graph=graph, selection={'bondIds': [edge['id']]})])[0]
            with self.subTest(source=source):
                molecule = graph_to_mol(result['graph'])
                self.assertEqual(Chem.MolToSmiles(molecule), canonical(expected))
                self.assertEqual(sum(b.GetStereo() > 1 for b in molecule.GetBonds()),
                                 sum(b['stereo'] > 1 for b in result['graph']['bonds']))
                self.assertEqual(Chem.MolToSmiles(graph_to_mol(import_structure(export_mol(result['graph'])))),
                                 canonical(expected))
                self.assertEqual(graph, before)

    def test_unrepresentable_middle_polyene_stereo_fails_closed(self):
        graph = import_structure('C/C=C/C(Cl)=C/C=C/C')
        chlorine = next(a['id'] for a in graph['atoms'] if a['symbol'] == 'Cl')
        edge = next(b for b in graph['bonds'] if chlorine in (b['begin'], b['end']))
        before = copy.deepcopy(graph)
        result = delete([dict(graph=graph, selection={'bondIds': [edge['id']]})])[0]
        self.assertEqual(sum(b['stereo'] > 1 for b in result['graph']['bonds']), 2)
        # The pinned writer encodes this particular partial configuration
        # with the middle double defined too. Never accept the wrong isomer
        # or erase either remaining E/Z annotation just to make the edit succeed.
        with self.assertRaisesRegex(ChemistryError, 'SMILES.*stereochemistry'):
            graph_to_mol(result['graph'])
        self.assertEqual(graph, before)

    def test_explicit_graph_stereo_controls_imported_directional_flags(self):
        graph = import_structure('C/C=C/C')
        double = next(b for b in graph['bonds'] if b['order'] == 2)
        double.update(stereo=0, stereoAtoms=[])
        before = copy.deepcopy(graph)
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(graph)), 'CC=CC')
        self.assertEqual(graph, before)


if __name__ == '__main__':
    unittest.main()
