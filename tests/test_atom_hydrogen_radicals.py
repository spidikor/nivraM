"""Chemistry and label-configuration tests; not font/render visual acceptance."""
import copy
import unittest
from rdkit import Chem
from nivram.chemistry import graph_to_mol, import_structure, export_mol, ChemistryError
from nivram.scene import hydrogen_carbon_labels


def isolated(symbol='C', radicals=0, charge=0):
    return {'atoms': [{'id': 'a', 'symbol': symbol, 'x': 0, 'y': 0,
                      'charge': charge, 'radicals': radicals,
                      'explicitHs': 0, 'noImplicit': False}], 'bonds': []}


class AtomHydrogenRadicalTests(unittest.TestCase):
    def test_all_element_defaults_remain_engine_defined(self):
        table = Chem.GetPeriodicTable()
        for z in range(1, 119):
            with self.subTest(z=z):
                g = isolated(table.GetElementSymbol(z)); before = copy.deepcopy(g)
                m = graph_to_mol(g)
                self.assertEqual(m.GetNumAtoms(), 1)
                if table.GetDefaultValence(z) == -1:
                    self.assertEqual(m.GetAtomWithIdx(0).GetNumImplicitHs(), 0)
                self.assertEqual(g, before)

    def test_default_counts_and_radical_edits(self):
        for symbol, count in [('C', 4), ('N', 3), ('O', 2), ('F', 1)]:
            m = graph_to_mol(isolated(symbol))
            self.assertEqual(m.GetAtomWithIdx(0).GetNumImplicitHs(), count)
        for radicals in range(5):
            g = isolated(radicals=radicals); m = graph_to_mol(g)
            self.assertEqual(m.GetAtomWithIdx(0).GetNumRadicalElectrons(), radicals)
            self.assertEqual(m.GetAtomWithIdx(0).GetNumImplicitHs(), 4-radicals)
        self.assertEqual(graph_to_mol(isolated('N', charge=1)).GetAtomWithIdx(0).GetNumImplicitHs(), 4)

    def test_fixed_hydrogen_radical_mismatch_rejects_without_mutation(self):
        for count in [0, 2]:
            g = import_structure('[CH3]'); g['atoms'][0]['radicals'] = count
            before = copy.deepcopy(g)
            with self.assertRaisesRegex(ChemistryError, 'unpaired-electron count'):
                graph_to_mol(g)
            self.assertEqual(g, before)
        g = import_structure('[CH3]')
        self.assertEqual(graph_to_mol(g).GetAtomWithIdx(0).GetNumRadicalElectrons(), 1)
        self.assertTrue(g['atoms'][0]['noImplicit'])
        self.assertEqual(g['atoms'][0]['explicitHs'], 3)

    def test_labels_follow_bonds_charge_and_radicals(self):
        cases = [('C', {0: 'CH<sub>4</sub>'}),
                 ('CC', {0: 'CH<sub>3</sub>', 1: 'CH<sub>3</sub>'}),
                 ('C=C', {0: 'CH<sub>2</sub>', 1: 'CH<sub>2</sub>'}),
                 ('C#C', {0: 'CH', 1: 'CH'}),
                 ('[CH3]', {0: 'CH<sub>3</sub>'}),
                 ('[13CH3-:7]', {0: '<sup>13</sup>CH<sub>3</sub><sup>−</sup>:7'}),
                 ('N', {}), ('[C]', {})]
        for smiles, expected in cases:
            with self.subTest(smiles=smiles):
                g = import_structure(smiles); before = copy.deepcopy(g)
                self.assertEqual(hydrogen_carbon_labels(graph_to_mol(g)), expected)
                self.assertEqual(g, before)

    def test_radical_roundtrip_and_explicit_h_neighbors(self):
        for smiles in ['[CH3]', '[CH2]', '[OH]', '[13CH2]C']:
            g = import_structure(smiles)
            again = import_structure(export_mol(g))
            self.assertEqual(Chem.MolToSmiles(graph_to_mol(again)), Chem.MolToSmiles(graph_to_mol(g)))
        m = Chem.MolFromSmiles('[H]C([H])([H])[H]', sanitize=True,
                               replacements={})
        # Explicit atom Hs are excluded by the count API (default parser removes Hs).
        m = Chem.AddHs(m)
        self.assertEqual(hydrogen_carbon_labels(m), {})

    def test_bad_radical_numbers_reject(self):
        for value in [None, True, -1, 9, 0.5, float('nan')]:
            with self.subTest(value=value), self.assertRaises(ChemistryError):
                graph_to_mol(isolated(radicals=value))
