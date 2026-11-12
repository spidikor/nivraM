import copy
import math
import unittest
from rdkit import Chem
from nivram.chemistry import ChemistryError,import_structure,graph_to_mol,export_mol,clean_graph
from nivram.scene import render_scene


def reflect_reference(graph,collinear=False):
    bond=next(b for b in graph['bonds'] if b['stereo']>1)
    atoms={a['id']:a for a in graph['atoms']}
    begin,end=atoms[bond['begin']],atoms[bond['end']]
    target=atoms[bond['stereoAtoms'][0]]
    dx,dy=end['x']-begin['x'],end['y']-begin['y']
    t=((target['x']-begin['x'])*dx+(target['y']-begin['y'])*dy)/(dx*dx+dy*dy)
    x,y=begin['x']+t*dx,begin['y']+t*dy
    target['x'],target['y']=(x,y) if collinear else (2*x-target['x'],2*y-target['y'])
    return graph


class ChemicalIntegrityTests(unittest.TestCase):
    def test_inverted_coordinate_move_is_rejected_before_render_or_mol_export(self):
        graph=reflect_reference(import_structure('F/C=C/F'))
        before=copy.deepcopy(graph)
        for operation in (graph_to_mol,render_scene,export_mol):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ChemistryError,'stereo|configuration'):
                    operation(graph)
                self.assertEqual(graph,before)

    def test_collinear_specified_double_bond_is_rejected(self):
        graph=reflect_reference(import_structure('F/C=C/F'),collinear=True)
        with self.assertRaisesRegex(ChemistryError,'stereo|configuration'):
            graph_to_mol(graph)

    def test_mol_coordinate_rounding_cannot_silently_erase_defined_stereo(self):
        graph=import_structure('F/C=C/F')
        for atom in graph['atoms']:
            atom['x']*=1e-9
            atom['y']*=1e-9
        mol=graph_to_mol(graph)
        unguarded=Chem.MolFromMolBlock(Chem.MolToMolBlock(mol,includeStereo=True,forceV3000=True),removeHs=False)
        self.assertNotEqual(Chem.MolToSmiles(unguarded,isomericSmiles=True),Chem.MolToSmiles(mol,isomericSmiles=True))
        with self.assertRaisesRegex(ChemistryError,'precision|stereochemistry'):
            export_mol(graph)

    def test_supported_double_bond_geometries_remain_exportable_after_rigid_moves(self):
        for smiles in ['F/C=C/F','F/C=C\\F','F/C(Cl)=C(/Br)I','F/C(Cl)=C(\\Br)I','C/C=C/C=C/C','C/C=N/O','C/C=N\\O','FC=CF']:
            with self.subTest(smiles=smiles):
                graph=import_structure(smiles)
                expected=Chem.MolToSmiles(graph_to_mol(graph),isomericSmiles=True)
                for atom in graph['atoms']:
                    x,y=atom['x'],atom['y']
                    atom['x']=x*math.cos(.7)-y*math.sin(.7)+4
                    atom['y']=x*math.sin(.7)+y*math.cos(.7)-2
                result=Chem.MolFromMolBlock(export_mol(graph),removeHs=False)
                self.assertEqual(Chem.MolToSmiles(result,isomericSmiles=True),expected)
                cleaned=clean_graph(graph)
                self.assertEqual(Chem.MolToSmiles(graph_to_mol(cleaned),isomericSmiles=True),expected)

    def test_multiple_smiles_records_are_rejected(self):
        with self.assertRaisesRegex(ChemistryError,'record|single|multiple|one'):
            import_structure('CCO\nCCN')

    def test_multiple_mol_records_and_sdf_metadata_are_rejected(self):
        first=export_mol(import_structure('CCO'))
        second=export_mol(import_structure('CCN'))
        for source in [first+'\n$$$$\n'+second+'\n$$$$\n',first+'\n> <annotation>\nnot represented\n',first+'\n'+second]:
            with self.subTest(source_length=len(source)):
                with self.assertRaisesRegex(ChemistryError,'record|SDF|trailing|metadata'):
                    import_structure(source)

    def test_cxsmiles_annotations_are_not_silently_discarded(self):
        for source in ['CCO |$first;second;third$|','CCO |(0,0,;1,0,;2,0,)|','CCO |atomProp:0.note.example|']:
            with self.subTest(source=source):
                with self.assertRaisesRegex(ChemistryError,'CXSMILES|annotation'):
                    import_structure(source)

    def test_plain_disconnected_smiles_and_one_mol_record_still_preserve_every_component(self):
        graph=import_structure('CCO.CCN')
        self.assertEqual(len(graph['atoms']),6)
        self.assertEqual(len(Chem.GetMolFrags(graph_to_mol(graph))),2)
        self.assertEqual(len(import_structure(export_mol(graph)+'\n\n')['atoms']),6)

    def test_unrepresented_smiles_record_name_is_rejected(self):
        with self.assertRaises(ChemistryError):
            import_structure('CCO unrepresented-record-name')


if __name__=='__main__':
    unittest.main()
