import json
import unittest
from nivram.diagnostics import diagnostics_report, sanitize_build_info, sanitize_downloads, sanitize_reference_exports


class DiagnosticTests(unittest.TestCase):
    def test_permission_observations_are_bounded_metadata_without_uri_or_extra_fields(self):
        row = {'sequence': 1, 'download_sequence': 10, 'kind': 7,
               'is_user_initiated': False, 'state': 'Default', 'origin_matches': True}
        observation = {'status': 'observing', 'seen': 1, 'read_failures': 0, 'dropped': 0,
                       'events': [{**row, 'Uri': 'PRIVATE', 'exception': 'PRIVATE'}], 'url': 'PRIVATE'}
        safe = sanitize_downloads({'status': 'installed', 'permission_observer': observation})['permission_observer']
        self.assertEqual(safe, {'status': 'observing', 'seen': 1, 'read_failures': 0, 'dropped': 0, 'events': [row]})
        self.assertNotIn('PRIVATE', json.dumps(safe))
        self.assertEqual(len(sanitize_downloads({'status':'installed','permission_observer':{**observation,'events':[row]*10}})['permission_observer']['events']),4)

    def test_permission_observations_reject_malformed_rows_and_counters(self):
        row = {'sequence': 1, 'download_sequence': 10, 'kind': 7,
               'is_user_initiated': False, 'state': 'Default', 'origin_matches': True}
        invalid = [dict(row, kind=True), dict(row, kind=14), dict(row, kind=-2),
                   dict(row, state='PRIVATE'), dict(row, state=[]), dict(row, origin_matches=1),
                   dict(row, is_user_initiated=None), dict(row, sequence=0), dict(row, download_sequence=-1)]
        invalid += [{k:v for k,v in row.items() if k != missing} for missing in row]
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                safe = sanitize_downloads({'status':'installed','permission_observer':{
                    'status':'observing','seen':True,'read_failures':-1,'dropped':10**40,'events':[candidate]}})['permission_observer']
                self.assertEqual(safe, {'status':'observing','events':[]})
        safe = sanitize_downloads({'status':'installed','permission_observer':{'status':[],'events':[]}})['permission_observer']
        self.assertEqual(safe, {'status':'unrecognized','events':[]})

    def test_maximum_permission_and_download_payload_fits_native_observer_bound(self):
        from nivram.webview_downloads import DIAGNOSTIC_COUNTERS, DIAGNOSTIC_EVENTS
        row = {'sequence':10**9,'event':max(DIAGNOSTIC_EVENTS, key=len),'format':'unrecognized','download_id':10**9,
               'reason':'download_dialog_cancellation_unconfirmed','active':10**9,'state':'Interrupted',
               'args_pending':False,'dialog_pending':False,'deferral_pending':False}
        permission = {'sequence':10**9,'download_sequence':10**9,'kind':13,'is_user_initiated':False,
                      'state':'Unrecognized','origin_matches':False}
        value = {'status':'shutdown_unconfirmed',**{k:10**9 for k in DIAGNOSTIC_COUNTERS},
                 'shutdown_confirmed':False,'last_state':'Interrupted','failure_code':'download_dialog_cancellation_unconfirmed',
                 'trace':[row]*16,'permission_observer':{'status':'subscribe_failed','seen':10**9,'read_failures':10**9,
                     'dropped':10**9,'events':[permission]*4}}
        safe = sanitize_downloads(value)
        self.assertEqual(len(safe['permission_observer']['events']),4)
        activation = sanitize_reference_exports({'schema':1,'seen':10**9,'dropped':10**9,'events':[{
            'sequence':10**9,'format':'json','trusted':False,'handler_active':False,
            'dispatch_active':False,'dispatch_observed':True}]*4})
        self.assertEqual(len(activation['events']),4)
        self.assertLess(len(json.dumps({'status':'observed','downloads':safe,
            'reference_exports':activation}).encode('utf-8')),7000)

    def test_download_diagnostics_skip_rows_missing_nullable_required_fields(self):
        row={'sequence':1,'event':'start','format':'unrecognized','download_id':0,'reason':None,
             'active':0,'state':None,'args_pending':False,'dialog_pending':False,'deferral_pending':False}
        for missing in ('reason','state'):
            incomplete=dict(row); incomplete.pop(missing)
            with self.subTest(missing=missing):
                self.assertEqual(sanitize_downloads({'status':'installed','trace':[incomplete]})['trace'],[])

    def test_download_metadata_has_strict_enum_count_boolean_allowlist(self):
        row={'sequence':1,'event':'blocked','format':'csv','download_id':0,'reason':'active_download',
             'active':1,'state':None,'args_pending':False,'dialog_pending':False,'deferral_pending':False}
        value={'status':'installed','accepted':1,'blocked':1,'trace_count':3,'trace_dropped':0,'trace':[row],
               'filename':'PRIVATE','url':'PRIVATE','operation':'PRIVATE'}
        safe=sanitize_downloads(value)
        self.assertEqual(safe['trace'],[row]); self.assertNotIn('PRIVATE',json.dumps(safe))
        result=diagnostics_report(downloads=value)
        self.assertEqual(result['webviewDownloads'],safe)
        bad={**value,'status':'PRIVATE','accepted':True,'blocked':10**40,'failure_code':'PRIVATE',
             'last_state':'PRIVATE','trace':[{**row,'event':'PRIVATE'}, {**row,'reason':'PRIVATE'},
                 {**row,'sequence':True},{**row,'dialog_pending':'PRIVATE'},{**row,'active':10**40}]}
        safe=sanitize_downloads(bad)
        self.assertEqual(safe['status'],'unrecognized'); self.assertEqual(safe['trace'],[])
        self.assertNotIn('PRIVATE',json.dumps(safe)); self.assertNotIn('accepted',safe); self.assertNotIn('blocked',safe)
        bounded=sanitize_downloads({**value,'trace':[row]*40})
        self.assertEqual(len(bounded['trace']),16)
        self.assertEqual(sanitize_downloads(None),{'status':'unconfigured'})

    def test_default_report_has_metadata_without_home_or_molecular_content(self):
        report = diagnostics_report()
        encoded = json.dumps(report)
        self.assertNotIn('C:\\\\Users\\\\', encoded)
        self.assertNotIn('SMILES', encoded)
        self.assertFalse(report['privacy']['molecularContentIncluded'])
        self.assertFalse(report['privacy']['automaticUpload'])
        self.assertEqual(report['configuration']['externalProviders'], 'per-request consent')

    def test_build_metadata_is_allowlisted_and_sensitive_strings_are_rejected(self):
        secret = 'C:\\Users\\private\\secret-project.nivram'
        build, versions = sanitize_build_info({'status': secret, 'version': secret, 'sourceSha256': secret,
            'builtAt': secret, 'unrelatedField': secret, 'versions': {'rdkit': secret, 'numpy': '2.3.5'}})
        self.assertNotIn(secret, json.dumps([build, versions]))
        self.assertEqual(versions, {'numpy': '2.3.5'})
        with self.assertRaises(ValueError):
            sanitize_build_info([])


if __name__ == '__main__':
    unittest.main()
