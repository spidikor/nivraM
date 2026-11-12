import copy
import unittest
from unittest.mock import patch
from nivram.chemistry import import_structure
from nivram.scene import DEFAULT_STYLE
from nivram.comparison import compare_request

def payload(left='CCO',right='CC=O'):
 return {'requestId':'a'*32,'pinId':'b'*32,'documentRevision':5,'pinned':{'graph':import_structure(left),'style':dict(DEFAULT_STYLE)},'current':{'graph':import_structure(right),'style':dict(DEFAULT_STYLE)}}

class ComparisonTests(unittest.TestCase):
 def test_real_properties_nonmutation_and_correlated_sides(self):
  p=payload();before=copy.deepcopy(p)
  with patch('nivram.comparison.render_scene',side_effect=ValueError('font unavailable')):r=compare_request(p)
  self.assertEqual(p,before);self.assertEqual(r['pinned']['formula'],'C2H6O');self.assertEqual(r['current']['formula'],'C2H4O')
  self.assertNotEqual(r['pinned']['identity']['structureHash'],r['current']['identity']['structureHash'])
  self.assertEqual(r['requestId'],p['requestId']);self.assertEqual(r['pinId'],p['pinId']);self.assertEqual(r['documentRevision'],5)
  self.assertEqual(r['transformations'],[]);self.assertEqual(r['pinned']['preview']['status'],'unavailable');self.assertIsNone(r['pinned']['preview']['scene'])
 def test_stereo_isotope_charge_are_not_collapsed(self):
  for a,b in [('C[C@H](O)F','C[C@@H](O)F'),('[13CH3]CO','CCO'),('CN','C[NH3+]')]:
   with self.subTest(a=a),patch('nivram.comparison.render_scene',side_effect=ValueError('unavailable')):
    r=compare_request(payload(a,b));self.assertNotEqual(r['pinned']['identity']['structureHash'],r['current']['identity']['structureHash'])
 def test_multifragment_logp_stays_unavailable(self):
  with patch('nivram.comparison.render_scene',side_effect=ValueError('unavailable')):r=compare_request(payload('CCO.O','CCO'))
  self.assertIsNone(r['pinned']['logP']['value']);self.assertEqual(r['pinned']['logP']['status'],'not_applicable')
 def test_empty_real_preview_and_no_fabricated_mass(self):
  p=payload();p['pinned']['graph']=p['current']['graph']={'atoms':[],'bonds':[]};r=compare_request(p)
  self.assertEqual(r['pinned']['preview']['status'],'available');self.assertIsNone(r['pinned']['molecularWeight']);self.assertEqual(r['pinned']['status'],'empty')
 def test_invalid_envelopes_and_chemistry_fail(self):
  for mutate in [lambda p:p.update(pinId='bad'),lambda p:p.update(requestId='bad'),lambda p:p.update(documentRevision=True),lambda p:p.update(extra=1),lambda p:p['pinned'].update(extra=1),lambda p:p['current']['graph']['bonds'][0].update(order=7),lambda p:p['pinned']['style'].update(labelSize=0)]:
   p=payload();mutate(p)
   with self.assertRaises(ValueError):compare_request(p)
 def test_preview_failure_is_explicit_but_properties_survive(self):
  # Actual renderer, no replacement font: outcome follows installed Arial.
  r=compare_request(payload())
  for side in ['pinned','current']:
   self.assertTrue(r[side]['formula']);self.assertIn(r[side]['preview']['status'],['available','unavailable'])
   if r[side]['preview']['status']=='unavailable':self.assertTrue(r[side]['preview']['error'])

if __name__=='__main__':unittest.main()
