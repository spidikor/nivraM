"""Real-engine qualification: no chemistry replacement or mocked chemistry gate.

Requires installed pinned RDKit and Node. Skips are missing qualification, not passes.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

AVAILABLE = importlib.util.find_spec('rdkit') is not None
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(AVAILABLE, 'RDKit is unavailable; stereochemical qualification remains unrun')
class FragmentChemistryTests(unittest.TestCase):
    def test_representative_closed_components_keep_isomeric_identity(self):
        from rdkit import Chem
        from nivram.chemistry import import_structure, graph_to_mol, mol_to_graph
        from nivram.scene import render_scene
        for smiles in ('N[C@@H](C)C(=O)O', 'F/C=C/F', 'F/C=C\\F', '[13CH3:17][C@H:18](O)F.[Na+]'):
            with self.subTest(smiles=smiles):
                original = import_structure(smiles)
                # Match the production render path before capture, not a raw import.
                mol = graph_to_mol(original)
                Chem.Kekulize(mol, clearAromaticFlags=True)
                original = mol_to_graph(mol, [a['id'] for a in original['atoms']], [b['id'] for b in original['bonds']])
                program = "import {extractFragment,insertFragment} from './public/modules/fragments.mjs';let data='';for await (const c of process.stdin)data+=c;const g=JSON.parse(data);let i=0;process.stdout.write(JSON.stringify(insertFragment(g,extractFragment(g),p=>p+'copy'+(++i)).graph));"
                run = subprocess.run(['node','--input-type=module','-e',program],input=json.dumps(original),text=True,capture_output=True,cwd=ROOT,check=True,timeout=15)
                expected = json.loads(run.stdout)
                result_mol = graph_to_mol(expected)
                Chem.Kekulize(result_mol, clearAromaticFlags=True)
                returned = mol_to_graph(result_mol,[a['id'] for a in expected['atoms']],[b['id'] for b in expected['bonds']])
                render_scene(returned, {})  # Actual renderer must accept it too.
                check = "import {assertFragmentRender} from './public/modules/fragments.mjs';let data='';for await (const c of process.stdin)data+=c;const {expected,returned}=JSON.parse(data);assertFragmentRender(expected,{graph:returned});"
                subprocess.run(['node','--input-type=module','-e',check],input=json.dumps({'expected':expected,'returned':returned}),text=True,capture_output=True,cwd=ROOT,check=True,timeout=15)
                before = sorted(Chem.MolToSmiles(graph_to_mol(original),isomericSmiles=True).split('.'))
                after = sorted(Chem.MolToSmiles(result_mol,isomericSmiles=True).split('.'))
                self.assertEqual(after,sorted(before*2))
                self.assertEqual(returned['atoms'][:len(original['atoms'])],original['atoms'])
                self.assertEqual(returned['bonds'][:len(original['bonds'])],original['bonds'])


if __name__ == '__main__': unittest.main()
