"""Conservative fixed-field grammar before radical override/extraction."""
import unittest
from unittest.mock import patch
from nivram.import_limits import check_mol
from nivram.chemistry import import_structure, ChemistryError
from nivram.optical_import import optical_candidate
from test_mol_radical_declarations import V2, payload

BASE = V2.replace('M  RAD  1   1   1\n','')
def field(value):
    lines=BASE.split('\n'); lines[4]=lines[4][:36]+value+lines[4][39:]
    return '\n'.join(lines)
def prop(value):return field('  4').replace('M  END',value+'\nM  END')

class RadicalLexicalTests(unittest.TestCase):
    def rejected(self, block):
        with self.assertRaisesRegex(ValueError, 'charge'):check_mol(block,512,1024)
        with patch('nivram.chemistry.Chem.MolFromMolBlock') as parser:
            with self.assertRaisesRegex(ChemistryError, 'charge'):import_structure(block)
            parser.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'null character' if '\0' in block else 'charge'):
            optical_candidate(payload(block))

    def test_malformed_legacy_field_rejects_before_native_parser(self):
        for value in ('4 0','4+0','4-0','4\0x','4x ','4.0','  8',' -1','4\t '):
            with self.subTest(value=value):self.rejected(field(value))

    def test_valid_legacy_field_spellings_and_defaults(self):
        for value in ('  4',' 04','004',' +4','+04','04 ',' 4 ','4  '):
            with self.subTest(value=value):self.assertEqual(check_mol(field(value),512,1024),{0:1})
        for value in ('   ','  0','000',' +0',' -0','  1','  2','  3','  5','  6','  7'):
            with self.subTest(value=value):self.assertEqual(check_mol(field(value),512,1024),{})

    def test_malformed_charge_property_rejects(self):
        for record in ('M  CHG','M  CHG -1','M  CHG  0garbage','M  CHG  9',
                       'M  CHG  1','M  CHG  1   0   0','M  CHG  1   2   0',
                       'M  CHG  1   1  16','M  CHG  1   1 -16',
                       'M  CHG  1   1 1x ','M  CHG 1x   1   0'):
            with self.subTest(record=record):self.rejected(prop(record))

    def test_valid_charge_properties_override_legacy(self):
        for record in ('M  CHG  0','M  CHG +0','M  CHG000','M  CHG  1   1   0',
                       'M  CHG  1   1  +1','M  CHG  1   1 -15','M  CHG  1   1  15'):
            with self.subTest(record=record):self.assertEqual(check_mol(prop(record),512,1024),{})
        # Lexical validation is confined to actual records, not skipped payloads.
        for record in ('A    1\nM  CHG','G    1\nM  CHG -1','S  SKP  1\nM  CHG  0garbage'):
            self.assertEqual(check_mol(prop(record),512,1024),{0:1})
        # Duplicate CHG assignments keep existing native precedence; no new policy.
        self.assertEqual(check_mol(prop('M  CHG  2   1   0   1   1'),512,1024),{})

if __name__=='__main__':unittest.main()
