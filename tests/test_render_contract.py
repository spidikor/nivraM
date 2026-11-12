"""Graph-only controls with a pure scene producer, never native drawing/fonts."""
import copy
import http.client
import json
import threading
import unittest
from unittest.mock import patch
from rdkit import Chem
from nivram.chemistry import graph_to_mol, mol_to_graph
from nivram.properties import calculate_properties, input_identity
from nivram.scene import checked_style
from nivram.render_contract import begin_binding, finish_binding
from nivram.server import LocalServer, Handler

def graph():
    return {'atoms':[{'id':'a','symbol':'C','x':0,'y':0},{'id':'b','symbol':'O','x':1.5,'y':0}], 'bonds':[{'id':'ab','begin':'a','end':'b','order':1}]}
def request(**extra):
    return {'graph':graph(),'sourceBinding':'nivram-render/1','requestId':'a'*32,'documentRevision':7,**extra}
def scene(g, style=None):
    p=calculate_properties(g)
    return {'properties':p,'smiles':p['smiles'],'formula':p['formula'],'mass':0 if p['status']=='empty' else None if p['molecularWeight']['value'] is None else round(p['molecularWeight']['value'],3),'style':checked_style(style),'svg':'<svg/>','positions':[]}

class BindingTests(unittest.TestCase):
    def test_exact_snapshot_and_normalization(self):
        data=request(style={'color':False}); before=copy.deepcopy(data)
        ctx=begin_binding('render',data)
        out=mol_to_graph(graph_to_mol(data['graph']),['a','b'],['ab'])
        value=finish_binding(ctx,out,scene)
        self.assertEqual(set(value),{'schema','requestId','documentRevision','action','sourceRequest','sourceIdentity','outputIdentity','outputGraphJson','graph','scene'})
        self.assertEqual(value['sourceRequest'],{'graph':before['graph'],'style':{'color':False}})
        self.assertEqual(value['sourceIdentity'],input_identity(before['graph']))
        self.assertEqual(value['outputIdentity'],input_identity(out))
        self.assertEqual(value['outputGraphJson'],json.dumps(out,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False))
        import hashlib
        self.assertEqual(hashlib.sha256(value['outputGraphJson'].encode('utf-8')).hexdigest(),value['outputIdentity']['graphHash'])
        self.assertNotEqual(value['sourceIdentity']['graphHash'],value['outputIdentity']['graphHash'])
        data['graph']['atoms'][0]['x']=88; out['atoms'][0]['x']=99
        self.assertEqual(value['sourceRequest']['graph'],before['graph'])
        self.assertNotEqual(value['graph']['atoms'][0]['x'],99)

    def test_witness_unicode_negative_zero_and_small_coordinates(self):
        g={'atoms':[{'id':'隐😀','symbol':'C','x':-0.0,'y':1e-7}],'bonds':[]}
        value=finish_binding(begin_binding('render',request(graph=g)),g,scene)
        self.assertEqual(json.loads(value['outputGraphJson']),g)
        self.assertTrue(value['outputGraphJson'].isascii())
        self.assertIn('-0.0',value['outputGraphJson'])
        self.assertIn('1e-07',value['outputGraphJson'])

    def test_scene_rounded_isotope_mass(self):
        g={'atoms':[{'id':'a','symbol':'C','isotope':13,'x':0,'y':0}],'bonds':[]}
        report=calculate_properties(g)
        self.assertNotEqual(report['molecularWeight']['value'],round(report['molecularWeight']['value'],3))
        value=finish_binding(begin_binding('render',request(graph=g)),g,scene)
        self.assertEqual(value['scene']['mass'],round(report['molecularWeight']['value'],3))

    def test_closed_request(self):
        self.assertIsNone(begin_binding('render',{'graph':graph(),'legacy':True}))
        for changes in ({'sourceBinding':'wrong'},{'requestId':'A'*32},{'documentRevision':True},{'documentRevision':-1},{'documentRevision':2**53},{'extra':1},{'selected':[]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):begin_binding('render',request(**changes))
        for missing in ('sourceBinding','requestId','documentRevision','graph'):
            data=request();del data[missing]
            with self.subTest(missing=missing),self.assertRaises(ValueError):begin_binding('render',data)
        with self.assertRaises(ValueError):begin_binding('unknown',request())

    def test_bad_producer(self):
        for mutation in ('graph','hash','smiles','style','formula','mass','missing','input-style','foreign-report'):
            def producer(g,style):
                result=scene(g,style)
                if mutation=='input-style':style['color']=False
                elif mutation=='foreign-report':
                    foreign=copy.deepcopy(g);foreign['atoms'][0]['symbol']='N'
                    result=scene(foreign,style)
                elif mutation=='graph':g['atoms'][0]['x']+=1
                elif mutation=='hash':result['properties']['inputIdentity']['graphHash']='0'*64
                elif mutation=='smiles':result['smiles']='N'
                elif mutation=='style':result['style']['color']=False
                elif mutation=='formula':result['formula']='N'
                elif mutation=='mass':result['mass']=999
                else:del result['properties']
                return result
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):finish_binding(begin_binding('render',request(style={'color':True})),graph(),producer)

    def test_foreign_render_only(self):
        foreign=graph();foreign['atoms'][0]['symbol']='N'
        with self.assertRaises(ValueError):finish_binding(begin_binding('render',request()),foreign,scene)
        self.assertEqual(finish_binding(begin_binding('reflect',request(mode='mirror',axis='horizontal')),foreign,scene)['action'],'reflect')

    def test_empty_isotope_and_aromatic(self):
        for g in ({'atoms':[],'bonds':[]},{'atoms':[{'id':'a','symbol':'C','isotope':400,'x':0,'y':0}],'bonds':[]}):
            result=finish_binding(begin_binding('render',request(graph=g)),g,scene)
            self.assertIsNone(result['scene']['properties']['molecularWeight']['value'])
            self.assertEqual(result['scene']['mass'],0 if not g['atoms'] else None)
        from rdkit.Chem import rdDepictor
        m=Chem.MolFromSmiles('c1ccccc1');rdDepictor.Compute2DCoords(m);g=mol_to_graph(m)
        for a in g['atoms']:a['aromatic']=True
        for b in g['bonds']:b['order']=1.5
        m=graph_to_mol(g);Chem.Kekulize(m,clearAromaticFlags=True)
        out=mol_to_graph(m,[a['id'] for a in g['atoms']],[b['id'] for b in g['bonds']])
        result=finish_binding(begin_binding('render',request(graph=g)),out,scene)
        self.assertEqual(result['sourceIdentity']['structureHash'],result['outputIdentity']['structureHash'])
        self.assertNotEqual(result['sourceIdentity']['graphHash'],result['outputIdentity']['graphHash'])

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server=LocalServer(('127.0.0.1',0),Handler);self.server.api_token='controlled-token'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.stub=patch('nivram.server.render_scene',side_effect=scene);self.stub.start()
    def tearDown(self):
        self.stub.stop();self.server.shutdown();self.server.server_close();self.thread.join()
    def post(self,action,data,token='controlled-token'):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        c.request('POST','/api/'+action,json.dumps(data),{'Content-Type':'application/json','X-Nivram-Token':token})
        r=c.getresponse();out=r.status,json.loads(r.read());c.close();return out
    def test_routes_and_legacy(self):
        for action,extra in [('render',{}),('clean',{'selected':[]}),('reflect',{'mode':'flip','axis':'horizontal'}),('rotate',{'degrees':30})]:
            with self.subTest(action=action):
                status,value=self.post(action,request(**extra));self.assertEqual(status,200,value)
                self.assertEqual(value['action'],action);self.assertEqual(value['documentRevision'],7)
                self.assertEqual(value['sourceIdentity'],input_identity(graph()))
                status,value=self.post(action,{'graph':graph(),**extra});self.assertEqual(status,200,value)
                self.assertEqual(set(value),{'graph','scene'})
    def test_valence_diagnostic_legacy_and_bound(self):
        g={'atoms':[{'id':'center','symbol':'C','x':0,'y':0}]+[{'id':str(i),'symbol':'F','x':i+1,'y':1} for i in range(5)],'bonds':[{'id':'b'+str(i),'begin':'center','end':str(i),'order':1} for i in range(5)]}
        for data in ({'graph':g},request(graph=g)):
            status,value=self.post('render',data)
            self.assertEqual(status,422)
            self.assertEqual(value['diagnostic'],{'code':'atom-valence','atomIds':['center']})

    def test_rejection_security_and_import(self):
        self.assertEqual(self.post('render',request(requestId='bad'))[0],422)
        self.assertEqual(self.post('render',request(),token='bad')[0],403)
        with patch('nivram.server.render_scene',side_effect=lambda g,s:{**scene(g,s),'smiles':'foreign'}):
            self.assertEqual(self.post('render',request())[0],422)
        with patch.object(self.server.import_preparation,'prepare_import',return_value={'sentinel':'child'}) as prepare:
            self.assertEqual(self.post('import',{'source':'C','style':None}),(200,{'sentinel':'child'}))
            prepare.assert_called_once_with('C',None)

if __name__=='__main__':unittest.main()
