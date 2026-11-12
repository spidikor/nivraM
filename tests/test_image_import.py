import copy
import struct
import unittest
import zlib

from rdkit import Chem

from nivram.chemistry import graph_to_mol, import_structure
from nivram.image_import import ImageImportError, inspect_png_metadata, select_metadata_candidates


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def png(metadata=(), width=1, height=1):
    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + b''.join(chunk(kind, data) for kind, data in metadata)
            + chunk(b'IDAT', zlib.compress(b'\0\xff\xff\xff\xff')) + chunk(b'IEND', b''))


def mrv(smiles):
    return Chem.MolToMrvBlock(graph_to_mol(import_structure(smiles))).encode('utf-8')


def with_mrv(xml):
    return png([(b'zTXt', b'molSource\0\0' + zlib.compress(xml))])


class ImageImportTests(unittest.TestCase):
    def test_plain_png_is_not_claimed_as_recognized_chemistry(self):
        result = inspect_png_metadata(png())
        self.assertEqual(result['status'], 'no_supported_metadata')
        self.assertEqual(result['recognition']['status'], 'not_run')
        self.assertEqual(result['candidates'], [])

    def test_mrv_preserves_stereo_isotopes_charge_and_coordinates(self):
        for smiles in ['C[C@@H](O)F', 'F/C=C/F', '[13CH3]C(=O)[O-]']:
            with self.subTest(smiles=smiles):
                source = graph_to_mol(import_structure(smiles))
                xml = Chem.MolToMrvBlock(source).encode('utf-8')
                result = inspect_png_metadata(with_mrv(xml))
                self.assertEqual(len(result['candidates']), 1)
                candidate = result['candidates'][0]
                self.assertEqual(candidate['status'], 'supported')
                rebuilt = graph_to_mol(candidate['graph'])
                self.assertEqual(Chem.MolToSmiles(rebuilt, isomericSmiles=True), Chem.MolToSmiles(source, isomericSmiles=True))
                for a, b in zip(source.GetConformer().GetPositions(), rebuilt.GetConformer().GetPositions()):
                    for x, y in zip(a, b): self.assertAlmostEqual(x, y, places=3)

    def test_salt_components_are_not_automatically_claimed_as_compounds(self):
        result = inspect_png_metadata(with_mrv(mrv('[Na+].[Cl-]')))
        self.assertEqual(len(result['candidates']), 2)
        self.assertTrue(all(c['kind'] == 'disconnected_component' for c in result['candidates']))
        self.assertTrue(any('compound' in x.lower() for x in result['warnings']))
        graph = select_metadata_candidates(result, [c['id'] for c in result['candidates']])
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(graph)), '[Cl-].[Na+]')

    def test_selection_is_explicit_and_does_not_mutate_candidates(self):
        result = inspect_png_metadata(with_mrv(mrv('CCO.O')))
        original = copy.deepcopy(result)
        with self.assertRaises(ImageImportError): select_metadata_candidates(result, [])
        graph = select_metadata_candidates(result, [result['candidates'][0]['id']])
        self.assertEqual(len(graph['atoms']), 3)
        self.assertEqual(result, original)

    def test_unknown_selection_and_duplicates_are_rejected(self):
        result = inspect_png_metadata(with_mrv(mrv('CCO')))
        cid = result['candidates'][0]['id']
        for selected in [['missing'], [cid, cid]]:
            with self.assertRaises(ImageImportError): select_metadata_candidates(result, selected)

    def test_two_top_level_molecules_are_both_presented(self):
        one = '<molecule molID="m1"><atomArray><atom id="a1" elementType="C" x2="0" y2="0"/></atomArray></molecule>'
        two = '<molecule molID="m2"><atomArray><atom id="a2" elementType="O" x2="3" y2="0"/></atomArray></molecule>'
        xml = ('<cml><MDocument><MChemicalStruct>' + one + two + '</MChemicalStruct></MDocument></cml>').encode()
        result = inspect_png_metadata(with_mrv(xml))
        self.assertEqual(len(result['candidates']), 2)

    def test_text_smiles_and_mol_are_supported_without_binary_deserialization(self):
        result = inspect_png_metadata(png([(b'tEXt', b'SMILES rdkit 2025.09.4\0CCO')]))
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(result['candidates'][0]['graph'])), 'CCO')
        block = Chem.MolToMolBlock(graph_to_mol(import_structure('CCO'))).encode()
        result = inspect_png_metadata(png([(b'tEXt', b'MOL rdkit 2025.09.4\0' + block)]))
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(result['candidates'][0]['graph'])), 'CCO')
        result = inspect_png_metadata(png([(b'tEXt', b'rdkitPKL rdkit 2025.09.4\0malicious-pickle')]))
        self.assertEqual(result['candidates'], [])
        self.assertTrue(result['unsupportedSources'])

    def test_bad_crc_and_truncation_rejected(self):
        data = bytearray(with_mrv(mrv('CCO')))
        data[30] ^= 1
        for invalid in [bytes(data), png()[:-5], b'not-png', png() + b'extra']:
            with self.subTest(size=len(invalid)), self.assertRaises(ImageImportError): inspect_png_metadata(invalid)

    def test_zero_dimensions_and_unknown_critical_chunk_rejected(self):
        for data in [png(width=0), png([(b'ABCD', b'unsupported')])]:
            with self.assertRaises(ImageImportError): inspect_png_metadata(data)

    def test_large_dimensions_require_crop_without_decoding_raster(self):
        result = inspect_png_metadata(with_mrv(mrv('CCO')))
        self.assertTrue(result['image']['metadataOnly'])
        large = inspect_png_metadata(png(width=11559, height=9999))
        self.assertEqual(large['image']['previewMode'], 'crop_required')
        self.assertEqual(large['image']['pixelCount'], 11559 * 9999)

    def test_compressed_metadata_bomb_is_rejected(self):
        data = png([(b'zTXt', b'molSource\0\0' + zlib.compress(b'A' * (1024 * 1024 + 1)))])
        with self.assertRaises(ImageImportError): inspect_png_metadata(data)

    def test_dtd_entities_and_utf16_declarations_rejected(self):
        xml = '<?xml version="1.0"?><!DOCTYPE cml [<!ENTITY x SYSTEM "file:///private">]><cml>&x;</cml>'
        for data in [xml.encode(), xml.encode('utf-16')]:
            with self.subTest(encoding=data[:8]):
                result = inspect_png_metadata(with_mrv(data))
                self.assertEqual(result['candidates'], [])
                self.assertTrue(result['unsupportedSources'])

    def test_declared_windows1252_mrv_is_supported(self):
        xml = mrv('CCO').decode().replace('encoding="utf-8"', 'encoding="windows-1252"')
        xml = xml.replace('encoding="UTF-8"', 'encoding="windows-1252"')
        if '<?xml' not in xml:
            xml = '<?xml version="1.0" encoding="windows-1252"?>' + xml
        xml = xml.replace('<molecule ', '<molecule title="caf\u00e9" ', 1)
        result = inspect_png_metadata(with_mrv(xml.encode('cp1252')))
        self.assertEqual(result['candidates'][0]['status'], 'supported')
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(result['candidates'][0]['graph'])), 'CCO')

    def test_xml_depth_and_atom_allocation_are_bounded(self):
        deep = b'<cml>' * 65 + b'</cml>' * 65
        atoms = b'<cml><molecule><atomArray atomID="' + b'a ' * 16385 + b'" /></molecule></cml>'
        for xml in [deep, atoms]:
            with self.subTest(kind=xml[:30]):
                result = inspect_png_metadata(with_mrv(xml))
                self.assertEqual(result['candidates'], [])
                self.assertTrue(result['unsupportedSources'])

    def test_unknown_chemical_attribute_is_not_silently_ignored(self):
        xml = mrv('CCO').replace(b'elementType="C"', b'elementType="C" unknownChemistry="1"', 1)
        result = inspect_png_metadata(with_mrv(xml))
        self.assertFalse(any(c['status'] == 'supported' for c in result['candidates']))
        self.assertTrue(result['unsupportedSources'])

    def test_unresolved_stereo_mark_is_retained_and_warned(self):
        xml = b'<cml><MDocument><MChemicalStruct><molecule molID="m1"><atomArray><atom id="a1" elementType="C" x2="0" y2="0"/><atom id="a2" elementType="C" x2="1" y2="0"/></atomArray><bondArray><bond id="b1" atomRefs2="a1 a2" order="1"><bondStereo convention="MDL" conventionValue="4"/></bond></bondArray></molecule></MChemicalStruct></MDocument></cml>'
        result = inspect_png_metadata(with_mrv(xml))
        candidate = result['candidates'][0]
        self.assertEqual(candidate['status'], 'supported')
        self.assertTrue(any('unresolved stereochemical' in w for w in candidate['warnings']))
        self.assertEqual(candidate['graph']['bonds'][0]['direction'], int(Chem.BondDir.UNKNOWN))

    def test_foreign_xml_namespaces_and_invalid_stereo_are_not_ignored(self):
        foreign = mrv('CCO').replace(b'<atom ', b'<q:atom xmlns:q="https://example.invalid/" ', 1)
        invalid_stereo = mrv('C[C@H](O)F').replace(b'>W<', b'>unsupported-stereo<')
        for xml in [foreign, invalid_stereo]:
            result = inspect_png_metadata(with_mrv(xml))
            self.assertFalse(any(c['status'] == 'supported' for c in result['candidates']))
            self.assertTrue(result['unsupportedSources'])

    def test_reaction_metadata_is_not_flattened(self):
        result = inspect_png_metadata(with_mrv(b'<cml><reaction><molecule/></reaction></cml>'))
        self.assertEqual(result['candidates'], [])
        self.assertTrue(result['unsupportedSources'])

    def test_molecule_in_unsupported_context_is_not_flattened(self):
        xml = b'<cml><Rgroup><molecule molID="m1"><atomArray><atom id="a1" elementType="C" x2="0" y2="0"/></atomArray></molecule></Rgroup></cml>'
        result = inspect_png_metadata(with_mrv(xml))
        self.assertEqual(result['candidates'], [])
        self.assertTrue(result['unsupportedSources'])

    def test_unsupported_polymer_candidate_cannot_be_selected(self):
        xml = b'<cml><MDocument><MChemicalStruct><molecule molID="m1"><atomArray><atom id="a1" elementType="C" x2="0" y2="0" sgroupRef="sg1"/></atomArray><molecule id="sg1" role="SruSgroup" title="n" atomRefs="a1" connect="ht"/></molecule></MChemicalStruct></MDocument></cml>'
        result = inspect_png_metadata(with_mrv(xml))
        self.assertTrue(result['unsupportedSources'])
        if result['candidates']:
            with self.assertRaises(ImageImportError):
                select_metadata_candidates(result, [result['candidates'][0]['id']])


if __name__ == '__main__':
    unittest.main()
