"""Independent edit oracles and rejection of incomplete native evidence."""
import copy
import unittest

from nivram import acceptance, acceptance_editor_contract as editor
from nivram.chemistry import import_structure
from nivram.reflection import reflect_graph
from nivram.rotation import rotate_graph
import test_acceptance_reporting as reporting


def document(smiles):
    return dict(graph=import_structure(smiles),style={'bondWidth':4.2},export={'width':2000})


def completed():
    value=reporting.AcceptanceFinalResultTests().completed_report();cases=[]
    for kind in editor.KINDS:
        row=dict(kind=kind,actions=[dict(type='button',kind='tool-select',status='passed',
            ownershipVerified=True,isTrusted=True,userActivation=True)],**dict.fromkeys(editor.FACTS[kind],True))
        if kind.startswith('reflection-') and kind!='reflection-reject-cancel':
            row['reflections']=[dict(mode=m,axis=a) for m in ('flip','mirror') for a in ('horizontal','vertical')]
        if kind=='reflection-reject-cancel':row['syntheticBoundaries']=['422-after-real-200','cancel-before-late-real-200']
        cases.append(row)
    value.update(scenario='editor-contract',editor_observation=dict(schemaVersion=1,cases=cases),
                 checks=[dict(id=k,status='passed') for k in acceptance.EDITOR_REQUIRED_CHECKS],dialog_fixture_shutdown=[])
    for k in ('download_shutdown','downloads_final'):value[k]['completed']=0
    return value


class EditorContractTests(unittest.TestCase):
    def test_drawing_comparison_ignores_only_bijective_generated_gradient_names(self):
        one='nivram-bond-'+'a'*32+'-10';two='nivram-bond-'+'b'*32+'-22'
        before='<g><linearGradient id="'+one+'" x1="1"/><path d="M 1,2" style="stroke:url(#'+one+');fill:#333399"/></g>'
        after=before.replace(one,two)
        self.assertEqual(editor.canonical_drawing(before),editor.canonical_drawing(after))
        for bad in [after.replace('x1="1"','x1="2"'),after.replace('M 1,2','M 1,3'),after.replace('#333399','#000000')]:
            self.assertNotEqual(editor.canonical_drawing(before),editor.canonical_drawing(bad))
        for bad in [after.replace('url(#'+two+')','url(#'+one+')'),after.replace('<path','<linearGradient id="'+two+'"/><path')]:
            with self.assertRaises(AssertionError):editor.canonical_drawing(bad)

    def test_complete_report_requires_shared_native_cleanup_and_every_case(self):
        value=completed();acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['acceptance_failures'],[]);self.assertEqual(len(value['checks']),13)
        for key,field,bad in [('network_policy','failures',1),('downloads_final','completed',1),
                              ('evaluation_shutdown','workerExited',False),('cleanup','profile_directory_retained',True)]:
            value=completed();value[key][field]=bad;acceptance.finalize_acceptance_report(value)
            self.assertEqual(value['status'],'failed')
        for name in acceptance.EDITOR_REQUIRED_CHECKS:
            value=completed();value['checks']=[r for r in value['checks'] if r['id']!=name]
            acceptance.finalize_acceptance_report(value);self.assertIn('required_functional_checks_missing',value['acceptance_failures'])

    def test_every_fact_requires_explicit_true(self):
        for i,kind in enumerate(editor.KINDS):
            for name in editor.FACTS[kind]:
                for bad in (False,1,None,'true'):
                    value=completed();value['editor_observation']['cases'][i][name]=bad
                    self.assertTrue(editor.observation_failures(value),(kind,name,bad))

    def test_missing_reordered_duplicate_untyped_or_untrusted_evidence_fails(self):
        mutations=[lambda c:c.pop(),lambda c:c.reverse(),lambda c:c.append(None),lambda c:c.__setitem__(1,c[0]),
                   lambda c:c[0]['actions'][0].update(isTrusted=False),lambda c:c[0]['actions'][0].update(ownershipVerified=False),
                   lambda c:c[3]['reflections'].pop(),lambda c:c[6].update(syntheticBoundaries=[])]
        for mutate in mutations:
            value=completed();mutate(value['editor_observation']['cases']);self.assertTrue(editor.observation_failures(value))
        for bad in (None,[],dict(schemaVersion=True)):
            self.assertTrue(editor.observation_failures(dict(editor_observation=bad)))

    def test_real_right_click_requires_trust_canvas_target_button_and_coordinates(self):
        p=dict(x=32,y=43);v=dict(trusted=True,inside=True,button=2,**p);editor.context_event(v,p)
        for key,bad in [('trusted',1),('inside',False),('button',1),('x',33),('y',float('nan'))]:
            with self.assertRaises(AssertionError):editor.context_event({**v,key:bad},p)

    def test_meso_mirror_cannot_pass_using_whole_identity_only(self):
        before=document(editor.MESO)
        for mode in ('flip','mirror'):
            for axis in ('horizontal','vertical'):
                after={**before,'graph':reflect_graph(before['graph'],mode=mode,axis=axis)}
                result=editor.reflection(before,after,mode,axis)
                self.assertTrue(result['identityUnchanged'])
                if mode=='mirror':self.assertNotEqual(result['beforeCenters'],result['afterCenters'])
                flip={**before,'graph':reflect_graph(before['graph'],mode='flip',axis=axis)}
                with self.assertRaisesRegex(AssertionError,'editor_reflection_stereo'):
                    editor.reflection(before,flip,'mirror',axis)

    def test_reflection_rejects_other_fragment_movement_and_metadata_loss(self):
        before=document(editor.CHIRAL+'.CCO');chosen=[a['id'] for a in before['graph']['atoms'][:6]]
        after={**before,'graph':reflect_graph(before['graph'],mode='flip',axis='horizontal',selected=chosen)}
        editor.reflection(before,after,'flip','horizontal',chosen)
        for change in [lambda d:d['graph']['atoms'][-1].update(x=999),lambda d:d['export'].update(width=1),
                       lambda d:d['graph']['atoms'][0].update(isotope=15)]:
            bad=copy.deepcopy(after);change(bad)
            with self.assertRaises(AssertionError):editor.reflection(before,bad,'flip','horizontal',chosen)

    def test_rotation_checks_selected_coordinates_and_all_other_fields(self):
        before=document('CCCC.CC');ids=[a['id'] for a in before['graph']['atoms'][:2]]
        after={**before,'graph':rotate_graph(before['graph'],degrees=30,selected=ids)}
        editor.rotation(before,after,30,ids)
        for change in [lambda d:d['graph']['atoms'][-1].update(x=999),lambda d:d['export'].update(width=1),
                       lambda d:d['graph']['atoms'][0].update(y=float('nan'))]:
            bad=copy.deepcopy(after);change(bad)
            with self.assertRaises(AssertionError):editor.rotation(before,bad,30,ids)

    def test_theme_oracle_rejects_element_recoloring_halo_and_geometry_changes(self):
        common=dict(tag='path',d='',strokeWidth='1',filter='none',stroke='none')
        light=dict(theme='light',canvas='rgb(255, 255, 255)',paints=[
            dict(common,classes='bond-0',inline='fill:#000000;',fill='rgb(0, 0, 0)'),
            dict(common,classes='atom-0',inline='fill:#333399;',fill='rgb(51, 51, 153)')])
        dark=copy.deepcopy(light);dark.update(theme='dark',canvas='rgb(16, 16, 16)')
        dark['paints'][0]['fill']='rgb(214, 214, 214)';dark['paints'][1]['filter']='drop-shadow(rgb(0, 0, 0) 1px 0px 0px)'
        editor.theme_paint(light,dark)
        for key,bad in [('fill','rgb(255, 255, 255)'),('filter','none'),('strokeWidth','2')]:
            changed=copy.deepcopy(dark);changed['paints'][1][key]=bad
            with self.assertRaises(AssertionError):editor.theme_paint(light,changed)


if __name__=='__main__':unittest.main()
