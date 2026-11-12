"""S2.3: explicit unknown double stereo is not ordinary unspecified stereo.

Small hand-authored 2D MOL fixtures; no renderer, worker, or font required.
"""
import copy
import unittest

from rdkit import Chem

from nivram.chemistry import export_mol, graph_to_mol, import_structure


def mol_fixture(*, v3000, unknown, cis=False):
    # The last F is above the double bond for Z, below for E. An explicit
    # unknown marker must suppress either coordinate-derived assignment.
    last_y = 1.0 if cis else -1.0
    if v3000:
        marker = ' CFG=2' if unknown else ''
        return (
            'S2.3 stereo fixture\n  nivraM          2D\n\n'
            '  0  0  0     0  0            999 V3000\n'
            'M  V30 BEGIN CTAB\nM  V30 COUNTS 4 3 0 0 0\n'
            'M  V30 BEGIN ATOM\n'
            'M  V30 1 F -1 1 0 0\nM  V30 2 C 0 0 0 0\n'
            f'M  V30 3 C 1.5 0 0 0\nM  V30 4 F 2.5 {last_y} 0 0\n'
            'M  V30 END ATOM\nM  V30 BEGIN BOND\n'
            f'M  V30 1 1 1 2\nM  V30 2 2 2 3{marker}\nM  V30 3 1 3 4\n'
            'M  V30 END BOND\nM  V30 END CTAB\nM  END\n'
        )
    return (
        'S2.3 stereo fixture\n  nivraM          2D\n\n'
        '  4  3  0  0  0  0            999 V2000\n'
        '   -1.0000    1.0000    0.0000 F   0  0  0  0  0  0  0  0  0  0  0  0\n'
        '    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n'
        '    1.5000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n'
        f'    2.5000{last_y:10.4f}    0.0000 F   0  0  0  0  0  0  0  0  0  0  0  0\n'
        f'  1  2  1  0\n  2  3  2  {3 if unknown else 0}\n  3  4  1  0\nM  END\n'
    )


class MolStereoRoundTripTests(unittest.TestCase):
    def assert_coordinates(self, graph, *, cis):
        self.assertEqual([(a['x'], a['y']) for a in graph['atoms']],
                         [(-1.0, 1.0), (0.0, 0.0), (1.5, 0.0),
                          (2.5, 1.0 if cis else -1.0)])

    def test_explicit_unknown_double_survives_both_mol_formats(self):
        for v3000 in (False, True):
            for cis in (False, True):
                with self.subTest(v3000=v3000, cis_geometry=cis):
                    source = mol_fixture(v3000=v3000, unknown=True, cis=cis)
                    graph = import_structure(source)
                    before = copy.deepcopy(graph)
                    self.assert_coordinates(graph, cis=cis)
                    self.assertEqual(graph['bonds'][1]['stereo'], int(Chem.BondStereo.STEREOANY))
                    self.assertEqual(graph['bonds'][1]['direction'], int(Chem.BondDir.EITHERDOUBLE))
                    self.assertEqual(Chem.MolToSmiles(graph_to_mol(graph), isomericSmiles=True), 'FC=CF')
                    exported = export_mol(graph)
                    self.assertIn('M  V30 2 2 2 3 CFG=2\n', exported)
                    restored = import_structure(exported)
                    self.assertEqual(restored['bonds'][1]['stereo'], int(Chem.BondStereo.STEREOANY))
                    self.assertEqual(restored['bonds'][1]['direction'], int(Chem.BondDir.EITHERDOUBLE))
                    self.assert_coordinates(restored, cis=cis)
                    self.assertEqual(graph, before, 'Export must not modify the input graph.')

    def test_defined_double_stereo_remains_distinct_from_unknown(self):
        for v3000 in (False, True):
            for cis in (False, True):
                with self.subTest(v3000=v3000, cis=cis):
                    graph = import_structure(mol_fixture(v3000=v3000, unknown=False, cis=cis))
                    before = copy.deepcopy(graph)
                    expected = Chem.BondStereo.STEREOZ if cis else Chem.BondStereo.STEREOE
                    self.assertEqual(graph['bonds'][1]['stereo'], int(expected))
                    restored = import_structure(export_mol(graph))
                    self.assertEqual(restored['bonds'][1]['stereo'], int(expected))
                    self.assertNotEqual(restored['bonds'][1]['direction'], int(Chem.BondDir.EITHERDOUBLE))
                    self.assert_coordinates(restored, cis=cis)
                    self.assertEqual(graph, before)


if __name__ == '__main__':
    unittest.main()
