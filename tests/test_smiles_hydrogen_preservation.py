"""Supplied-H fidelity, not automatic hydrogen expansion or visual acceptance."""
import copy
import json
import unittest
from unittest.mock import patch

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors
from nivram.chemistry import ChemistryError, clean_graph, export_mol, graph_to_mol, import_structure
from nivram.import_limits import check_smiles, ImportLimitError
from nivram.properties import calculate_properties
from nivram.scene import hydrogen_carbon_labels
from nivram.image_import import inspect_png_metadata
from tests.test_image_import import png


MAPPED = (
    '[H:7][O:8][H:9]', '[H:7][O:8][CH3:9]',
    '[2H:7][O:8][3H:9]', '[H:7][NH2+:8][CH3:9]',
    '[H-:7].[Na+:8]', '[H+:7].[Cl-:8]', '[H:7]',
    '[H:7][H:8]', '[H:7][C:8]([H:9])([H:10])[H:11]',
    '[H:7][C@H:8]([F:9])[Cl:10]', '[H:7][C@@:8]([F:9])([Cl:10])[Br:11]',
    '[H:7]/[C:8]([F:9])=[C:10](/[H:11])[Cl:12]',
    '[H:7]/[C:8]([F:9])=[C:10](\\[H:11])[Cl:12]',
    '[H:7][c:8]1[cH:9][cH:10][cH:11][cH:12][cH:13]1',
    '[H:7][C:8]([H:9])[CH3:10]',
)


def reference(source):
    # Deliberately independent parser configuration, not import_structure.
    params = Chem.SmilesParserParams()
    params.removeHs = False
    params.allowCXSMILES = False
    params.parseName = False
    mol = Chem.MolFromSmiles(source, params)
    if mol is None:
        raise AssertionError('Invalid test fixture: ' + source)
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    return mol


def mapped_identity(mol):
    """Map-keyed topology and chemical stereo, independent of atom ordering.

    CIP labels and E/Z assignments survive reordered neighbors where raw chiral
    tag or directional slash integers need not. H counts exclude H vertices.
    """
    mol = Chem.Mol(mol)
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    maps = [a.GetAtomMapNum() for a in mol.GetAtoms()]
    assert all(maps) and len(set(maps)) == len(maps)
    atoms = {a.GetAtomMapNum(): (a.GetAtomicNum(), a.GetIsotope(), a.GetFormalCharge(),
             a.GetNumRadicalElectrons(), a.GetTotalNumHs(), a.GetIsAromatic(),
             a.GetProp('_CIPCode') if a.HasProp('_CIPCode') else None)
             for a in mol.GetAtoms()}
    bonds = {tuple(sorted((maps[b.GetBeginAtomIdx()], maps[b.GetEndAtomIdx()]))):
             (b.GetBondTypeAsDouble(), int(b.GetStereo())) for b in mol.GetBonds()}
    return atoms, bonds


class SmilesHydrogenPreservationTests(unittest.TestCase):
    def test_mapped_atoms_annotations_and_topology_match_independent_parser(self):
        for source in MAPPED:
            with self.subTest(source=source):
                expected = reference(source)
                graph = import_structure(source)
                actual = graph_to_mol(graph)
                self.assertEqual(mapped_identity(actual), mapped_identity(expected))
                self.assertEqual(actual.GetNumAtoms(), expected.GetNumAtoms())
                # Exact direct parser representation, before any MOL conversion.
                for supplied, observed in zip(expected.GetAtoms(), graph['atoms']):
                    self.assertEqual(observed['explicitHs'], supplied.GetNumExplicitHs())
                    self.assertEqual(observed['noImplicit'], supplied.GetNoImplicit())

    def test_mol_reimport_and_cleanup_preserve_mapped_identity(self):
        for source in MAPPED:
            with self.subTest(source=source):
                graph = import_structure(source)
                before = copy.deepcopy(graph)
                expected = mapped_identity(reference(source))
                for result in (import_structure(export_mol(graph)), clean_graph(graph)):
                    self.assertEqual(mapped_identity(graph_to_mol(result)), expected)
                self.assertEqual(graph, before)

    def test_selective_cleanup_keeps_unselected_coordinates_and_all_atoms(self):
        graph = import_structure('[H:7][O:8][CH2:9][CH2:10][OH:11]')
        original = copy.deepcopy(graph)
        selected = [a['id'] for a in graph['atoms'] if a['mapNum'] in (7, 8)]
        result = clean_graph(graph, selected)
        self.assertEqual(mapped_identity(graph_to_mol(result)), mapped_identity(graph_to_mol(graph)))
        for before, after in zip(graph['atoms'], result['atoms']):
            if before['id'] not in selected:
                self.assertEqual((before['x'], before['y']), (after['x'], after['y']))
        self.assertEqual(graph, original)

    def test_bracket_counts_are_not_hydrogen_atoms(self):
        for source, atoms, hs, counts in [('O', 1, 0, [0]), ('[OH2]', 1, 0, [2]),
                ('[H]O', 2, 1, [0, 0]), ('[H][O][H]', 3, 2, [0, 0, 0]),
                ('[NH4+]', 1, 0, [4]), ('[nH]1cccc1', 5, 0, [0, 0, 0, 0, 0])]:
            with self.subTest(source=source):
                graph = import_structure(source)
                self.assertEqual(len(graph['atoms']), atoms)
                self.assertEqual(sum(a['symbol'] == 'H' for a in graph['atoms']), hs)
                self.assertEqual([a['explicitHs'] for a in graph['atoms']], counts)
                # Existing kekulization can turn aromatic [nH] into implicit N-H;
                # the chemical count survives without creating an H vertex.
                if source == '[nH]1cccc1':
                    self.assertEqual(graph_to_mol(graph).GetAtomWithIdx(0).GetTotalNumHs(), 1)

    def test_existing_implicit_hydrogen_inputs_keep_parser_semantics(self):
        for source in ('O', 'CCO', '[NH4+]', '[CH3]', 'c1cc[nH]c1',
                       'C[C@H](O)F', 'F/C=C/F', 'F/C=C\\F', '[13CH3-:8]'):
            with self.subTest(source=source):
                expected = Chem.MolFromSmiles(source)
                actual = graph_to_mol(import_structure(source))
                self.assertEqual(actual.GetNumAtoms(), expected.GetNumAtoms())
                self.assertEqual(Chem.MolToSmiles(actual), Chem.MolToSmiles(expected))

    def test_stereoisomers_are_not_collapsed(self):
        for left, right in ((MAPPED[11], MAPPED[12]),
                ('[H:7][C@:8]([F:9])([Cl:10])[Br:11]',
                 '[H:7][C@@:8]([F:9])([Cl:10])[Br:11]')):
            identities = []
            for source in (left, right):
                graph = import_structure(source)
                expected = mapped_identity(reference(source))
                self.assertEqual(mapped_identity(graph_to_mol(import_structure(export_mol(graph)))), expected)
                identities.append(expected)
            self.assertNotEqual(*identities)

    def test_properties_match_retained_hydrogen_reference_without_mutating(self):
        for source in MAPPED:
            with self.subTest(source=source):
                graph = import_structure(source); before = copy.deepcopy(graph)
                mol = reference(source); result = calculate_properties(graph)
                self.assertEqual(result['formula'], rdMolDescriptors.CalcMolFormula(mol, separateIsotopes=True, abbreviateHIsotopes=False))
                self.assertAlmostEqual(result['exactMass']['value'], rdMolDescriptors.CalcExactMolWt(mol), places=10)
                self.assertAlmostEqual(result['molecularWeight']['value'], Descriptors.MolWt(mol), places=10)
                self.assertEqual(result['formalCharge'], Chem.GetFormalCharge(mol))
                self.assertEqual(graph, before)

    def test_separate_h_atoms_are_not_double_counted_in_carbon_labels(self):
        for source, expected in (('[H]C([H])([H])[H]', {}), ('[H]C', {1: 'CH<sub>3</sub>'})):
            self.assertEqual(hydrogen_carbon_labels(graph_to_mol(import_structure(source))), expected)
        a, b = (calculate_properties(import_structure(s)) for s in ('[H]O[H]', 'O'))
        self.assertEqual(a['formula'], 'H2O')
        for key in ('exactMass', 'molecularWeight', 'logP'):
            self.assertAlmostEqual(a[key]['value'], b[key]['value'], places=10)

    def test_atom_boundary_retains_every_supplied_h(self):
        # 256 isolated H2 components = exactly 512 real atoms, not 256 counts.
        graph = import_structure('.'.join(['[H][H]'] * 256))
        self.assertEqual(len(graph['atoms']), 512)
        self.assertEqual(len(graph['bonds']), 256)
        with patch('nivram.chemistry.Chem.MolFromSmiles') as parser:
            with self.assertRaises(ChemistryError):
                import_structure('.'.join(['[H][H]'] * 256) + '.[H]')
            parser.assert_not_called()

    def test_bond_boundary_includes_h_bonds_before_parser(self):
        # A reduced budget exercises exact/over boundary without impossible H valence.
        source = '[H]C([H])([H])[H]'
        check_smiles(source, 5, 4)
        with self.assertRaises(ImportLimitError): check_smiles(source, 5, 3)
        with patch('nivram.chemistry.MAX_BONDS', 3), patch('nivram.chemistry.Chem.MolFromSmiles') as parser:
            with self.assertRaises(ChemistryError): import_structure(source)
            parser.assert_not_called()
        # Exact real configured 1024-bond lexical boundary; chemistry parsing is
        # deliberately a sentinel because this artificial graph is not valid chemistry.
        exact = '[H]C' + '1C1' * 511
        with patch('nivram.chemistry.MAX_ATOMS', 2000), patch('nivram.chemistry.Chem.MolFromSmiles', return_value=None) as parser:
            with self.assertRaises(ChemistryError): import_structure(exact + 'C')
            parser.assert_called_once()
        with patch('nivram.chemistry.MAX_ATOMS', 2000), patch('nivram.chemistry.Chem.MolFromSmiles') as parser:
            with self.assertRaises(ChemistryError): import_structure(exact + 'CC')
            parser.assert_not_called()

    def test_graph_order_changes_preserve_chemical_stereo(self):
        for source in (MAPPED[10], MAPPED[11], MAPPED[12]):
            with self.subTest(source=source):
                # RDKit rewrites chiral tags to account for reordered neighbors.
                expected = reference(source)
                from rdkit.Chem import rdDepictor
                from nivram.chemistry import mol_to_graph
                reordered = Chem.RenumberAtoms(expected, list(reversed(range(expected.GetNumAtoms()))))
                rdDepictor.Compute2DCoords(reordered)
                graph = mol_to_graph(reordered)
                self.assertEqual(mapped_identity(graph_to_mol(graph)), mapped_identity(expected))
                self.assertEqual(mapped_identity(graph_to_mol(import_structure(export_mol(graph)))), mapped_identity(expected))

    def test_project_json_preserves_h_atoms_and_distinguishes_map_changes(self):
        from nivram.persistence import validate_project
        graphs = [import_structure(s) for s in ('[H:7][O:8][H:9]', '[H:70][O:8][H:9]')]
        hashes = []
        for graph in graphs:
            before = copy.deepcopy(graph)
            project = validate_project(json.loads(json.dumps({'schema': 'nivram/1', 'graph': graph})))
            self.assertEqual(project['graph'], before)
            hashes.append(calculate_properties(graph)['inputIdentity']['structureHash'])
        self.assertNotEqual(*hashes)

    def test_annotation_rejection_stays_fail_closed(self):
        for source in ('[H:7]O name', '[H:7]O |atomProp:0.foo.bar|', '[H:7]O\nCC', '[H:7]*', '[H:7]O>>O'):
            with self.subTest(source=source), self.assertRaises(ChemistryError): import_structure(source)

    def test_synthetic_png_smiles_metadata_retains_mapped_hydrogens(self):
        source = MAPPED[0]
        result = inspect_png_metadata(png([(b'tEXt', b'SMILES rdkit 2025.09.4\0' + source.encode())]))
        self.assertEqual(result['status'], 'metadata_available')
        self.assertEqual(len(result['candidates']), 1)
        actual = graph_to_mol(result['candidates'][0]['graph'])
        self.assertEqual(mapped_identity(actual), mapped_identity(reference(source)))


if __name__ == '__main__':
    unittest.main()
