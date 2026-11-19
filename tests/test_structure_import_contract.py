"""Ordinary parsed molecules must fit the editor before rendering is attempted."""
import unittest
from unittest.mock import patch

from rdkit import Chem, rdBase
from rdkit.Chem import rdDepictor
from rdkit.Geometry import Point3D

from nivram.chemistry import ChemistryError, graph_to_mol, import_structure
from nivram.import_preparation import SCHEMA, ImportPreparationError
from nivram.optical_import import MODEL_SHA256, OpticalImportError, optical_candidate
from nivram.scene import render_scene
from tests.import_binding_fixtures import prepare_fixture


def translated_drawing(x):
    mol = Chem.MolFromSmiles('CO')
    rdDepictor.Compute2DCoords(mol)
    conf = mol.GetConformer()
    conf.SetAtomPosition(0, Point3D(x, 2.0, 0.0))
    conf.SetAtomPosition(1, Point3D(x - 1.5, 2.0, 0.0))
    return Chem.MolToMolBlock(mol, forceV3000=True)


class StructureImportContractTests(unittest.TestCase):
    def test_parsed_but_unrepresentable_input_is_rejected_before_render(self):
        examples = {'square planar platinum': 'F[Pt@SP1](Cl)(Br)I',
                    'translated drawing': translated_drawing(10001)}
        for name, source in examples.items():
            with self.subTest(example=name, route='direct'), self.assertRaises(ChemistryError):
                import_structure(source)
            with self.subTest(example=name, route='worker'):
                with patch('nivram.scene.render_scene', wraps=render_scene) as render:
                    with self.assertRaises(ImportPreparationError) as caught:
                        prepare_fixture({'schema': SCHEMA, 'mode': 'import', 'source': source, 'style': {}})
                    self.assertEqual(caught.exception.code, 'invalid_input')
                    render.assert_not_called()

    def test_coordinate_limit_is_inclusive_and_not_rescaled(self):
        graph = import_structure(translated_drawing(10000))
        self.assertEqual([(a['x'], a['y']) for a in graph['atoms']], [(10000, 2), (9998.5, 2)])
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(graph)), 'CO')

    def test_planar_mol_with_3d_declaration_is_not_a_2d_import(self):
        mol = Chem.MolFromSmiles('C[C@H](F)Cl')
        rdDepictor.Compute2DCoords(mol)
        for v3000 in (False, True):
            with self.subTest(v3000=v3000):
                lines = Chem.MolToMolBlock(mol, forceV3000=v3000).splitlines()
                self.assertEqual(lines[1][20:22], '2D')
                # A title containing 3D is not the dimensional declaration.
                lines[0] = '3D study, 2D drawing'
                graph = import_structure('\n'.join(lines) + '\n')
                self.assertEqual(Chem.MolToSmiles(graph_to_mol(graph)), Chem.MolToSmiles(mol))
                lines[1] = lines[1][:20] + '3D' + lines[1][22:]
                block = '\r\n'.join(lines) + '\r\n'
                with rdBase.BlockLogs(), self.assertRaisesRegex(ChemistryError, '3D'):
                    import_structure(block)
                optical = {'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': MODEL_SHA256,
                           'inputSha256': 'a' * 64, 'smiles': 'C[C@H](F)Cl',
                           'molfile': block, 'confidence': 0.8}
                with self.assertRaises(OpticalImportError):
                    optical_candidate(optical)

    def test_actual_depiction_failure_remains_a_render_error(self):
        with patch('nivram.scene.render_scene', side_effect=RuntimeError('depiction unavailable')) as render:
            with self.assertRaises(ImportPreparationError) as caught:
                prepare_fixture({'schema': SCHEMA, 'mode': 'import', 'source': 'CO', 'style': {}})
        self.assertEqual(caught.exception.code, 'render_failed')
        render.assert_called_once()
