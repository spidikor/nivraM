"""Pointer evidence rejects incomplete input, missing cases and state corruption."""
import copy
import unittest

from nivram import acceptance, acceptance_pointer as pointer, acceptance_pointer_input as input_api
from test_acceptance_pointer_input import events
from test_acceptance_click import COORDINATES
import test_acceptance_reporting as reporting


def completed():
    value=reporting.AcceptanceFinalResultTests().completed_report();cases=[]
    for kind in pointer.KINDS:
        actions=[dict(type='drag',status='passed',ownershipVerified=True,start=COORDINATES,
            end={**COORDINATES,'x':205},observed=events()) for _ in range(4 if kind.startswith('erase-dpr') else 1)]
        row=dict(kind=kind,actions=actions,**dict.fromkeys(pointer.FACTS[kind],True))
        if kind.startswith('erase-dpr'):row.update(observedDpr=int(kind[-1]),distances=[3,4,5])
        if kind in ('erase-pointercancel','erase-lostcapture'):
            row['boundary']=dict(synthetic=True,trusted=False,pointerId=1,type='pointercancel' if kind=='erase-pointercancel' else 'lostpointercapture')
        cases.append(row)
    value.update(scenario='pointer',pointer_observation=dict(schemaVersion=1,metricsRestored=True,cases=cases),
        checks=[dict(id=k,status='passed') for k in acceptance.POINTER_REQUIRED_CHECKS],dialog_fixture_shutdown=[])
    for k in ('download_shutdown','downloads_final'):value[k]['completed']=0
    return value


class PointerEvidenceTests(unittest.TestCase):
    def test_empty_or_untyped_key_sequence_is_not_input_evidence(self):
        for kinds in ([],None,'undo',['undo']*129):
            with self.assertRaises(AssertionError):pointer.key_events([],kinds)

    def test_scale_accepts_observed_float_rounding_but_not_material_error(self):
        self.assertTrue(pointer.scale_matches(1.0000000149011612,1))
        self.assertTrue(pointer.scale_matches(2.0000000298023224,2))
        for bad in (True,False,None,'1',float('nan'),float('inf'),1.0001,1.25):
            self.assertFalse(pointer.scale_matches(bad,1))
        value=completed();value['pointer_observation']['cases'][0]['observedDpr']=1.0000000149011612
        self.assertEqual(pointer.observation_failures(value),[])

    def test_button_action_record_keeps_category_and_kind_distinct(self):
        journey=object.__new__(pointer.Journey);journey.row={'actions':[]}
        journey.record('button',kind='tool-select',isTrusted=True,userActivation=True)
        self.assertEqual(journey.row['actions'],[dict(type='button',kind='tool-select',status='passed',
            ownershipVerified=True,isTrusted=True,userActivation=True)])

    def test_complete_pointer_report_uses_all_twelve_checks_and_shared_cleanup(self):
        value=completed();acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['acceptance_failures'],[]);self.assertEqual(len(value['checks']),12)
        for key,field,bad in [('network_policy','failures',1),('downloads_final','completed',1),
            ('evaluation_shutdown','workerExited',False),('cleanup','profile_directory_retained',True)]:
            value=completed();value[key][field]=bad;acceptance.finalize_acceptance_report(value)
            self.assertEqual(value['status'],'failed')

    def test_every_fact_requires_explicit_true(self):
        for i,kind in enumerate(pointer.KINDS):
            for name in pointer.FACTS[kind]:
                for bad in (False,1,None,'true'):
                    value=completed();value['pointer_observation']['cases'][i][name]=bad
                    self.assertTrue(pointer.observation_failures(value),(kind,name,bad))

    def test_missing_duplicated_reordered_extra_or_untyped_cases_fail(self):
        mutations=[lambda c:c.pop(),lambda c:c.append(None),lambda c:c.reverse(),lambda c:c.__setitem__(1,c[0])]
        for mutate in mutations:
            value=completed();mutate(value['pointer_observation']['cases']);self.assertTrue(pointer.observation_failures(value))
        for bad in (None,[],dict(schemaVersion=True)):
            self.assertTrue(pointer.observation_failures(dict(pointer_observation=bad)))

    def test_trust_release_ownership_and_scale_cannot_be_inferred(self):
        for mutate in [lambda c:c['actions'][0].update(ownershipVerified=False),
            lambda c:c['actions'][0]['observed']['events'][-1].update(trusted=False),
            lambda c:c['actions'][0]['observed']['events'].pop(),
            lambda c:c.update(observedDpr=True),lambda c:c.update(distances=[3,5]),lambda c:c['actions'].pop()]:
            value=completed();mutate(value['pointer_observation']['cases'][0]);self.assertTrue(pointer.observation_failures(value))
        value=completed();value['pointer_observation']['metricsRestored']=False;self.assertTrue(pointer.observation_failures(value))

    def test_synthetic_boundary_cannot_claim_trust_or_wrong_pointer(self):
        for field,bad in [('trusted',True),('synthetic',False),('pointerId',True),('pointerId',0),('type','blur')]:
            value=completed();value['pointer_observation']['cases'][3]['boundary'][field]=bad
            self.assertTrue(pointer.observation_failures(value))

    def test_missing_native_check_fails_even_with_complete_case_facts(self):
        for name in acceptance.POINTER_REQUIRED_CHECKS:
            value=completed();value['checks']=[r for r in value['checks'] if r['id']!=name]
            acceptance.finalize_acceptance_report(value);self.assertIn('required_functional_checks_missing',value['acceptance_failures'])

    def test_fixed_key_events_require_complete_modifier_release_and_trusted_target(self):
        rows=[dict(type='keydown' if p['type']=='rawKeyDown' else 'keyup',key=p['key'],code=p['code'],
            modifiers=p['modifiers'],target=True,trusted=True,repeat=False) for p in input_api.key_sequence('undo')]
        pointer.key_events(rows,['undo'])
        for field,bad in [('trusted',1),('target',False),('modifiers',0),('repeat',True),('code','KeyY')]:
            changed=copy.deepcopy(rows);changed[1][field]=bad
            with self.assertRaises(AssertionError):pointer.key_events(changed,['undo'])
        with self.assertRaises(AssertionError):pointer.key_events(rows[:-1],['undo'])

    def test_translation_rejects_chemistry_other_atoms_and_nonuniform_coordinates(self):
        before=dict(graph=dict(atoms=[dict(id='a',x=1,y=2,isotope=13),dict(id='b',x=3,y=4,charge=-1),dict(id='c',x=5,y=6)],bonds=[]),style={'bond':1})
        after=copy.deepcopy(before)
        for a in after['graph']['atoms'][:2]:a['x']+=2;a['y']+=3
        pointer.translation(before,after,{'a','b'})
        for mutate in [lambda d:d['graph']['atoms'][0].update(isotope=12),lambda d:d['graph']['atoms'][1].update(y=8),
            lambda d:d['graph']['atoms'][2].update(x=0),lambda d:d['style'].update(bond=2)]:
            changed=copy.deepcopy(after);mutate(changed)
            with self.assertRaises(AssertionError):pointer.translation(before,changed,{'a','b'})

    def test_document_oracle_keeps_unsaved_empty_dirty_and_exact_revision_metadata(self):
        document={'graph':{'atoms':[],'bonds':[]},'style':{}}
        before=dict(snapshot=dict(document=document,revision=10,dirty=True,recoveryGeneration='fixture'))
        after=copy.deepcopy(before);after['snapshot']['revision']=11
        pointer.same_document(before,after,document)
        for field,bad in [('dirty',False),('revision',12),('recoveryGeneration','changed')]:
            changed=copy.deepcopy(after);changed['snapshot'][field]=bad
            with self.assertRaises(AssertionError):pointer.same_document(before,changed,document)


if __name__=='__main__':unittest.main()
