"""Reject wrong native close controls and preserve original confirmation returns."""
import copy
import threading
from types import SimpleNamespace as NS
import unittest
from nivram import acceptance_close_dialog as d


def candidate():
    return {'handle': 10, 'pid': 42, 'thread': 8, 'class': '#32770', 'title': d.TITLE,
        'owner': 0, 'visible': True, 'enabled': True, 'bodyMatched': True,
        'buttons': [{'id': i, 'handle': 10+i, 'parent': 10, 'pid': 42, 'thread': 8,
            'class': 'Button', 'text': label, 'visible': True, 'enabled': True}
            for i, label in ((1, 'OK'), (2, 'Cancel'))]}


class Backend:
    def __init__(self, decision=False):
        self.pid, self.value, self.posts, self.closed = 42, candidate(), [], False
        self.decision = decision
        self.posted = threading.Event()
        self.k = NS(GetCurrentThreadId=lambda: 8)
        self.u = NS(PostMessageW=self.post)
    def enumerate(self, **_): return [10]
    def window(self, _): return True
    def window_pid(self, _): return self.pid
    def visible(self, _): return True
    def class_name(self, _): return '#32770'
    def snapshot_dialog(self, *_): return copy.deepcopy(self.value)
    def process_pinned(self, _): return True
    def post(self, *args):
        self.posts.append(args)
        self.posted.set()
        return True
    def close(self): self.closed = True


class ConfirmationTests(unittest.TestCase):
    def validate(self, value): d.validate_dialog(value, pid=42, thread=8, owner=99)

    def test_exact_thread_prompt_and_controls_with_vendor_ownerless_dialog(self):
        self.validate(candidate())

    def test_wrong_process_thread_owner_prompt_or_disabled_dialog_rejected(self):
        for key, value in [('pid', 43), ('thread', 9), ('owner', 77), ('class', 'Other'),
                           ('title', 'Another prompt'), ('bodyMatched', False), ('visible', False), ('enabled', False)]:
            with self.subTest(key=key), self.assertRaises(AssertionError):
                row = candidate(); row[key] = value; self.validate(row)

    def test_foreign_changed_or_aliased_buttons_rejected(self):
        for key, value in [('id', 3), ('parent', 99), ('pid', 7), ('thread', 9), ('class', 'Edit'),
                           ('text', 'Save'), ('visible', False), ('enabled', False), ('handle', 12)]:
            with self.subTest(key=key), self.assertRaises(AssertionError):
                row = candidate(); row['buttons'][0][key] = value; self.validate(row)

    def test_original_cancel_and_ok_returns_are_observed_not_replaced(self):
        for decision in (False, True):
            backend = Backend(decision)
            def original(title, message):
                self.assertEqual((title, message), (d.TITLE, d.MESSAGE))
                self.assertTrue(backend.posted.wait(2))
                return decision
            window = NS(create_confirmation_dialog=original, native=NS(Handle=NS(ToInt64=lambda:99)))
            observer = d.ConfirmationObserver(window, (decision,), backend_factory=lambda: backend)
            observer.attach()
            self.assertIs(window.create_confirmation_dialog(d.TITLE, d.MESSAGE), decision)
            observer.assert_count(1)
            self.assertEqual(backend.posts, [(11 if decision else 12, 0xF5, 0, 0)])
            self.assertTrue(backend.closed)
            self.assertTrue(observer.close()['shutdownConfirmed'])
            self.assertIs(window.create_confirmation_dialog, original)
            with self.assertRaises(AssertionError): observer.observed(d.TITLE, d.MESSAGE)

    def test_wrong_native_return_fails_even_after_successful_button_post(self):
        backend = Backend()
        def original(*_):
            self.assertTrue(backend.posted.wait(2)); return True
        window = NS(create_confirmation_dialog=original, native=NS(Handle=NS(ToInt64=lambda:99)))
        observer = d.ConfirmationObserver(window, (False,), backend_factory=lambda: backend)
        observer.attach()
        with self.assertRaises(AssertionError): window.create_confirmation_dialog(d.TITLE, d.MESSAGE)
        with self.assertRaises(AssertionError): observer.assert_count(1)
        self.assertTrue(observer.close()['shutdownConfirmed'])

    def test_unrecognized_dialog_never_receives_button_post(self):
        backend = Backend(); backend.value['title'] = 'Other'
        row = {}
        d.ConfirmationObserver._choose(backend, 8, 99, False, threading.Event(), row)
        self.assertEqual(backend.posts, [])
        self.assertEqual(row['error'], 'AssertionError')

    def test_backend_setup_failure_does_not_leave_active_observer(self):
        def unavailable(): raise OSError('synthetic')
        window = NS(create_confirmation_dialog=lambda *_: self.fail('Unexpected native call'))
        observer = d.ConfirmationObserver(window, (False,), backend_factory=unavailable)
        observer.attach()
        with self.assertRaises(AssertionError): window.create_confirmation_dialog(d.TITLE, d.MESSAGE)
        self.assertEqual(observer.active, 0)
        self.assertTrue(observer.close()['shutdownConfirmed'])


if __name__ == '__main__': unittest.main()
