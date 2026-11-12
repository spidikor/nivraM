"""Property admission is not a scientific descriptor oracle."""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from nivram.chemistry import import_structure
from nivram.properties import calculate_properties, property_response
from nivram.property_contract import validate_properties, bounded_json
from nivram.import_protocol import validate_prepared_result
from nivram.mass_contract import MASS_MESSAGE, MASS_REASON
from nivram.comparison import compare_request
from test_isotope_mass import prepared
from test_comparison import payload

class PropertyContractTests(unittest.TestCase):
    def test_actual_producer_cases_and_nonmutation(self):
        graphs=[{'atoms':[], 'bonds':[]}]+[import_structure(s) for s in ['CCO','C[NH3+]','[13CH4]','[CH3]','CCO.O','[CH3:5]O','C[C@H](O)F','[400CH4]','[Na+]','[NH3+]CC(=O)[O-]']]
        graphs.append({'atoms':[{'id':'隐😀','symbol':'C','x':-0.0,'y':1e-7}], 'bonds':[]})
        for graph in graphs:
            before=copy.deepcopy(graph)
            response=property_response({'graph':graph,'documentRevision':7,'requestId':'a'*32})
            self.assertEqual(response['sourceGraph'],before);self.assertEqual(graph,before)
            self.assertIs(validate_properties(response['properties'],graph,revision=7,expected_identity=response['sourceIdentity']),response['properties'])

    def test_native_nitro_charge_separation_keeps_source_unchanged(self):
        graph={'atoms':[dict(id=str(i),symbol=s,x=i,y=0) for i,s in enumerate(['C','N','O','O'])],
               'bonds':[dict(id='b0',begin='0',end='1',order=1),dict(id='b1',begin='1',end='2',order=2),dict(id='b2',begin='1',end='3',order=2)]}
        original=copy.deepcopy(graph)
        report=property_response(dict(graph=graph,documentRevision=7,requestId='a'*32))['properties']
        self.assertEqual(graph,original);self.assertEqual(report['formalCharge'],0)
        self.assertEqual(report['smiles'],'C[N+](=O)[O-]')
        self.assertEqual(report['logP']['status'],'calculated_with_warning')

    def test_controlled_producer_identity_corruption_rejected(self):
        graph=import_structure('CCO'); good=calculate_properties(graph,7)
        for field,value in [('graphHash','0'*64),('structureHash','0'*64),('documentRevision',8),('smiles','N')]:
            bad=copy.deepcopy(good);bad['inputIdentity'][field]=value
            with self.subTest(field=field),patch('nivram.properties.calculate_properties',return_value=bad),self.assertRaises(ValueError):
                property_response({'graph':graph,'documentRevision':7,'requestId':'a'*32})
        with self.assertRaises(ValueError):property_response({'graph':graph,'requestId':None})

    def test_prepared_schema_status_corruption_rejected(self):
        original=prepared('CCO')
        changes=[lambda p:p.update(status='empty'),lambda p:p.update(formalCharge=True),lambda p:p.update(fragmentCount=2),
            lambda p:p['molecularWeight'].update(value=None),lambda p:p['exactMass'].update(value=-1),
            lambda p:p['logP'].update(value=True),lambda p:p['logP'].update(status='not_applicable'),
            lambda p:p['exactMass'].update(unit='g/mol'),lambda p:p['logP'].update(method='invented'),
            lambda p:p['logP'].update(value=None,status='not_applicable',warnings=['Radical LogP is outside the evaluated application domain.']),
            lambda p:p['engine'].update(version=''),lambda p:p.update(calculatedAt=''),
            lambda p:p['molecularWeight'].update(extra=1),lambda p:p.update(extra=1),
            lambda p:p.update(warnings=['invented']),lambda p:p['logP'].update(value=float('nan'))]
        for change in changes:
            value=copy.deepcopy(original);change(value['scene']['properties'])
            with self.assertRaises(ValueError):validate_prepared_result(value,mode='import')
        value=prepared('C[NH3+]');value['scene']['properties']['logP']['warnings']=[];value['scene']['properties']['warnings']=[]
        with self.assertRaises(ValueError):validate_prepared_result(value,mode='import')

    def test_transport_limit_coherent_values_are_not_scientifically_checked(self):
        value=prepared('CCO');value['scene']['mass']=999
        for key in ('molecularWeight','exactMass','logP'):value['scene']['properties'][key]['value']=999
        self.assertIs(validate_prepared_result(value,mode='import'),value)
        value=prepared('[13CH4]');p=value['scene']['properties'];p['massUnavailable']={'reason':MASS_REASON,'message':MASS_MESSAGE,'isotopes':[{'symbol':'C','isotope':13}]}
        p['molecularWeight']['value']=p['exactMass']['value']=value['scene']['mass']=None;p['warnings'].append(MASS_MESSAGE)
        self.assertIs(validate_prepared_result(value,mode='import'),value)

    def test_comparison_bound_and_legacy_source_nonmutation(self):
        for bound in (False,True):
            request=payload()
            if bound:request['sourceBinding']='nivram-source/1'
            before=copy.deepcopy(request)
            with patch('nivram.comparison.render_scene',side_effect=ValueError('controlled unavailable preview')):response=compare_request(request)
            self.assertEqual(request,before)
            for side in ('pinned','current'):
                self.assertEqual('sourceGraph' in response[side],bound)
                if bound:self.assertEqual(response[side]['sourceGraph'],request[side]['graph']);self.assertEqual(response[side]['sourceIdentity'],response[side]['identity'])
        bad=calculate_properties(request['pinned']['graph'],5);bad['inputIdentity']['graphHash']='0'*64
        with patch('nivram.comparison.calculate_properties',return_value=bad),self.assertRaises(ValueError):compare_request(request)

    def test_oversized_integer_rejects_cleanly(self):
        with self.assertRaises(ValueError):bounded_json({'value':10**400})

    def test_data_only_imports(self):
        root=Path(__file__).resolve().parents[1]
        code=f"import sys;sys.path.insert(0,{str(root)!r});import nivram.property_contract,nivram.import_protocol;assert not any(x=='rdkit' or x.startswith('rdkit.') for x in sys.modules)"
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',code],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode())
