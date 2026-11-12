"""New SVG format keeps existing native download state gates (controlled COM).
This is not a Windows chooser, native rendering or byte-save acceptance test.
"""
import unittest
from unittest.mock import patch
from test_webview_downloads import setup, request, Operation, Dialog, BASE, BLOB, worker_call
from nivram.webview_downloads import _suggested_name, _valid_destination

class SvgDownloads(unittest.TestCase):
    def setUp(self):
        Dialog.instances=[]; Dialog.chosen=r'C:\exports\molecule.svg'; Dialog.showing_event=False
        for name,value in [('_ui_action',lambda f:f),('_SaveDialog',Dialog)]:
            patcher=patch('nivram.webview_downloads.'+name,value);patcher.start();self.addCleanup(patcher.stop)

    def test_svg_uses_specific_filter_and_does_not_report_completion_at_dialog_return(self):
        adapter,_,control,_=setup();args=request(control,Operation('image/svg+xml'),'molecule.svg')
        self.assertEqual(adapter.snapshot()['trace'][1]['format'],'svg')
        control.pump();self.assertEqual(Dialog.instances[-1].format_info[0],('.svg',))
        self.assertEqual(args.ResultFilePath,Dialog.chosen);self.assertEqual(adapter.snapshot()['completed'],0)
        args.DownloadOperation.transition('Completed');self.assertEqual(adapter.snapshot()['completed'],1)
        self.assertEqual(worker_call(control,lambda:adapter.wait_for_download(timeout_seconds=.1))['state'],'Completed')

    def test_svg_origin_and_destination_constraints(self):
        for uri in [BASE+'export.svg','https://evil.invalid/a.svg','data:image/svg+xml,x',BLOB.replace('54321','54322')]:
            adapter,_,control,_=setup();args=request(control,Operation('image/svg+xml',uri));control.pump()
            self.assertTrue(args.Cancel);self.assertEqual(adapter.snapshot()['accepted'],0)
        for value in ['relative.svg',r'C:\exports\x.html',r'C:\exports\x.svg:stream',r'C:\exports\CON.svg']:
            self.assertFalse(_valid_destination(value,('.svg',)))
        self.assertEqual(_suggested_name('../unsafe.exe',('.svg',)),'unsafe.svg')
        self.assertTrue(_valid_destination(r'C:\exports\ok.svg',('.svg',)))

    def test_svg_cancel_is_retained_and_overlapping_download_blocked(self):
        adapter,_,control,_=setup();Dialog.chosen=None
        first=request(control,Operation('image/svg+xml'));second=request(control,Operation('image/svg+xml'))
        self.assertTrue(second.Cancel);control.pump();self.assertTrue(first.Cancel)
        self.assertEqual(adapter.snapshot()['completed'],0)
        self.assertEqual(adapter.snapshot()['blocked'],1)

if __name__=='__main__':unittest.main()
