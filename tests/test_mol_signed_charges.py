"""Frozen signed-charge identity and native-copy regressions (RDKit 2025.09.4)."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from rdkit import Chem
from nivram.chemistry import import_structure, graph_to_mol
from nivram.optical_import import optical_candidate, MODEL_SHA256
from nivram.optical_validation import worker_main
from nivram.import_limits import prepare_mol

CASES = json.loads(Path(__file__).with_name('signed_charge_fixtures.json').read_text())
BASE = CASES[0]['block'].replace('M  CHG +1   1   1\n', '')
V3 = next(c['block'] for c in CASES if c['name'] == 'v3_chg_+1')

def payload(block, smiles):
    return dict(schemaVersion=1, method='MolScribe', modelSha256=MODEL_SHA256,
                inputSha256='0'*64, confidence=None, smiles=smiles, molfile=block)

def identity(graph):
    return Chem.MolToSmiles(graph_to_mol(graph), canonical=True, isomericSmiles=True)

def control(block):
    # These synthetic fixtures contain plus only in known charge fields.
    return block.replace('+', ' ' if 'V2000' in block else '')

class SignedChargeTests(unittest.TestCase):
    def test_frozen_expected_identities_and_optical_worker(self):
        for case in CASES:
            with self.subTest(case=case['name']):
                if case['name'] == 'legacy_plus_4':
                    with self.assertRaises(ValueError): import_structure(case['block'])
                    continue  # fixed valence conflicts with declared radical, including unsigned
                block, expected = case['block'], case['expected_smiles']
                p = Chem.SmilesParserParams(); p.removeHs = False
                expected = Chem.MolToSmiles(Chem.MolFromSmiles(expected, p))
                graph = import_structure(block)
                self.assertEqual(identity(graph), expected)
                self.assertEqual(graph, import_structure(control(block)))
                self.assertEqual(identity(optical_candidate(payload(block, expected))['graph']), expected)
                out = io.BytesIO()
                self.assertEqual(worker_main(stdin=io.BytesIO(json.dumps(payload(block, expected)).encode()), stdout=out), 0)

    def test_wrong_neutral_optical_rejected(self):
        for case in CASES[:3] + [CASES[7], CASES[10]]:
            with self.subTest(case=case['name']):
                with self.assertRaises(ValueError): optical_candidate(payload(case['block'], 'C'))

    def test_fixed_fields_all_signed_and_leading_zeros(self):
        for record in ('M  CHG +1  +1  +1', 'M  CHG+01 +01 +01', 'M  CHG +0',
                       'M  CHG  2   1  +0   1  +1', 'M  CHG +1  +1  -1'):
            block = BASE.replace('M  END', record+'\nM  END')
            self.assertEqual(import_structure(block), import_structure(control(block)))

    def test_native_copy_preserves_payload_and_crlf(self):
        block = BASE.replace('M  END', 'A    1\nCHG=+7\nG    1\nM  CHG +1   1  +8\nS  SKP  1\nM  CHG +1   1  +9\nM  CHG +1  +1  +1\nM  END').replace('\n','\r\n')
        original = block
        native = Chem.MolFromMolBlock
        seen=[]
        def capture(text, **kw):
            seen.append((text,kw)); return native(text, **kw)
        with patch('nivram.chemistry.Chem.MolFromMolBlock', side_effect=capture):
            with self.assertRaisesRegex(ValueError, "aliases"):
                import_structure(block)
        self.assertEqual(block, original)
        self.assertEqual(seen[0][0], block.replace('M  CHG +1  +1  +1','M  CHG  1   1   1'))
        self.assertEqual(seen[0][1], dict(sanitize=True, removeHs=False, strictParsing=True))

    def test_v3000_token_boundaries(self):
        for token in ('CHG=+1','"CHG=+1"','CHG=+-\nM  V30 1', 'CH-\nM  V30 G=+1'):
            block=V3.replace('CHG=+1',token)
            unsigned=block.replace('CHG=+1','CHG=1').replace('CHG=+-\nM  V30 1','CHG=-\nM  V30 1').replace('G=+1','G=1')
            self.assertEqual(import_structure(block),import_structure(unsigned))
            self.assertEqual(identity(import_structure(block)),'[CH3+]')
        # Native rejects these unrelated properties; preparation must leave their
        # quoted bytes intact and still see an adjacent real charge assignment.
        for token in ('X="CHG=+7" CHG=+1', 'X="a"CHG=+1'):
            prepared, _ = prepare_mol(V3.replace('CHG=+1', token),512,1024)
            self.assertIn(token.replace('CHG=+1','CHG=1'),prepared)

    def test_invalid_v3000_charge_is_rejected_before_native(self):
        for value in ('++1','+-1','+','+١','+１','+16','-16','1junk','"+1"','(1 +1)','+ 1'):
            with self.subTest(value=value), patch('nivram.chemistry.Chem.MolFromMolBlock') as parser:
                with self.assertRaises(ValueError): import_structure(V3.replace('CHG=+1','CHG='+value))
                parser.assert_not_called()

    def test_signed_charge_preserves_rich_graphs(self):
        for smiles in ('[13CH3:7][NH3+]', '[H][N+]([H])([H])[H]',
                       '[2H][C@](F)(Cl)[NH3+]', 'F/C=C/[NH3+]', '[CH2+]'):
            params=Chem.SmilesParserParams(); params.removeHs=False
            mol=Chem.MolFromSmiles(smiles,params)
            for v3 in (False,True):
                unsigned=Chem.MolToMolBlock(mol,forceV3000=v3)
                lines=unsigned.split('\n')
                if v3:
                    signed=unsigned.replace('CHG=1','CHG=+1')
                else:
                    for i,line in enumerate(lines):
                        if line.startswith('M  CHG'):
                            count=int(line[6:9]); line=line[:6]+f'+{count}'.rjust(3)+line[9:]
                            for offset in range(9,9+8*count,8):
                                atom=int(line[offset:offset+4]); charge=int(line[offset+4:offset+8])
                                line=line[:offset]+f'+{atom}'.rjust(4)+f'{charge:+}'.rjust(4)+line[offset+8:]
                            lines[i]=line
                    signed='\n'.join(lines)
                with self.subTest(smiles=smiles,v3=v3):
                    self.assertEqual(import_structure(signed),import_structure(unsigned))
                    self.assertEqual(optical_candidate(payload(signed,smiles))['graph'],
                                     optical_candidate(payload(unsigned,smiles))['graph'])

    def test_unsorted_v3000_labels_and_mixed_property_order(self):
        params=Chem.SmilesParserParams();params.removeHs=False
        unsigned=Chem.MolToMolBlock(Chem.MolFromSmiles('[2H][NH2+:7][13CH3]',params),forceV3000=True)
        # Remap IDs in atom and bond references without altering atom order.
        labels={'1':'19','2':'4','3':'11'};section=None;lines=[]
        for line in unsigned.split('\n'):
            if line=='M  V30 BEGIN ATOM':section='atom'
            elif line=='M  V30 BEGIN BOND':section='bond'
            elif line.startswith('M  V30 END'):section=None
            elif line.startswith('M  V30 ') and section:
                fields=line[7:].split()
                if section=='atom':fields[0]=labels[fields[0]]
                else:fields[2],fields[3]=labels[fields[2]],labels[fields[3]]
                line='M  V30 '+' '.join(fields)
            lines.append(line)
        unsigned='\n'.join(lines)
        signed=unsigned.replace('CHG=1','CHG=+1')
        self.assertEqual(import_structure(signed),import_structure(unsigned))
        self.assertEqual(optical_candidate(payload(signed,'[2H][NH2+:7][13CH3]'))['graph'],
                         optical_candidate(payload(unsigned,'[2H][NH2+:7][13CH3]'))['graph'])

    def test_legacy_plus_radical_and_global_overrides(self):
        mol=Chem.MolFromSmiles('[CH3]');unsigned=Chem.MolToMolBlock(mol)
        lines=[line for line in unsigned.split('\n') if not line.startswith('M  RAD')]
        lines[4]=lines[4][:36]+'  4'+lines[4][39:];unsigned='\n'.join(lines)
        signed=unsigned.replace('C   0  4','C   0 +4')
        self.assertNotEqual(signed,unsigned)
        self.assertEqual(import_structure(signed),import_structure(unsigned))
        # An effective CHG on another atom overrides legacy on all atoms.
        unsigned=Chem.MolToMolBlock(Chem.MolFromSmiles('CC'))
        lines=unsigned.split('\n');lines[4]=lines[4][:36]+' +4'+lines[4][39:]
        signed='\n'.join(lines).replace('M  END','M  CHG +1  +2  +1\nM  END')
        self.assertEqual(import_structure(signed),import_structure(control(signed)))
        self.assertEqual([a['radicals'] for a in import_structure(signed)['atoms']],[0,0])

    def test_no_edits_outside_actual_charge_records(self):
        for suffix in ('M  END\nM  CHG +1  +1  +1', '$$$$\nM  CHG +1  +1  +1'):
            block=BASE.replace('M  END\n',suffix+'\n')
            self.assertEqual(prepare_mol(block,512,1024)[0],block)
        block=V3.replace('CHG=+1','CHG=1').replace('C 0.000000','C +0.000000')
        self.assertEqual(prepare_mol(block,512,1024)[0],block)
        for block in (BASE.replace('M  END','M  RAD +1   1   2\nM  END'),
                      V3.replace('CHG=+1','RAD=+2')):
            with self.assertRaises(ValueError):prepare_mol(block,512,1024)

if __name__ == '__main__': unittest.main()
