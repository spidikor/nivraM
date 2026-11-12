import copy
import threading
from types import SimpleNamespace
import unittest
from nivram.lifecycle import CloseCoordinator

GENERATION = 'a' * 32
_DEFAULT_RECEIPT = object()


def receipt(**changes):
    return {'status': 'recovered-copy-saved', 'id': '1789900000000000000-' + 'b' * 32,
            'generation': GENERATION, 'revision': 1, **changes}


class Window:
    def __init__(self, dirty=True, agree=True):
        self.snapshot = {'dirty': dirty, 'revision': 1, 'document': {'fixture': True},
                         'recoveryGeneration': GENERATION}
        self.agree = agree
        self.closed = threading.Event()
        self.events = SimpleNamespace(closed=self.closed)
        self.messages = []
    def evaluate_js(self, expression):
        return copy.deepcopy(self.snapshot)
    def create_confirmation_dialog(self, title, message):
        self.messages.append(message)
        return self.agree
    def destroy(self):
        self.closed.set()


class Store:
    def __init__(self, fail=False, reply=_DEFAULT_RECEIPT):
        self.fail = fail; self.documents = []; self.calls = []; self.reply = reply
    def autosave(self, document, generation, revision):
        self.calls.append((copy.deepcopy(document), generation, revision))
        if self.fail:
            raise OSError('disk failure')
        self.documents.append(copy.deepcopy(document))
        return receipt(generation=generation, revision=revision) if self.reply is _DEFAULT_RECEIPT else copy.deepcopy(self.reply)


class StalledWindow(Window):
    """Hold only the external native evaluation boundary until the test releases it."""
    def __init__(self, stage='begin', *, agree=False):
        super().__init__(agree=agree)
        self.stage = stage
        self.entered = threading.Event()
        self.release = threading.Event()
        self.prompted = threading.Event()
        self.resumed = threading.Event()
        self.calls = []
        self.destroy_calls = 0

    def evaluate_js(self, expression):
        stage = ('begin' if 'beginCloseReview' in expression else
                 'end' if 'endCloseReview' in expression else 'recheck')
        self.calls.append(stage)
        if stage == self.stage:
            self.entered.set()
            self.release.wait()
        if stage == 'end':
            self.resumed.set()
        return copy.deepcopy(self.snapshot)

    def create_confirmation_dialog(self, title, message):
        self.prompted.set()
        return super().create_confirmation_dialog(title, message)

    def destroy(self):
        self.destroy_calls += 1
        super().destroy()


def review_thread(coordinator):
    finished, errors = threading.Event(), []
    def run():
        try:
            coordinator._review_close()
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, finished, errors


class CloseTests(unittest.TestCase):
    def test_clean_close_commits_frozen_appearance_before_destroy(self):
        window, store, events = Window(dirty=False), Store(), []
        window.snapshot['appearance'] = {'theme': 'dark', 'revision': 1, 'generation': GENERATION}
        original = window.destroy
        def destroy():
            events.append('destroy')
            original()
        window.destroy = destroy
        CloseCoordinator(window, store, save_appearance=lambda value: events.append(value))._review_close()
        self.assertEqual(events, [window.snapshot['appearance'], 'destroy'])
        self.assertEqual(store.calls, [])

    def test_failed_preference_save_can_cancel_close_without_claiming_molecule_loss(self):
        window, store = Window(dirty=False, agree=False), Store()
        def fail(_value):
            raise OSError('private path')
        CloseCoordinator(window, store, save_appearance=fail)._review_close()
        self.assertFalse(window.closed.is_set())
        self.assertEqual(store.calls, [])
        self.assertEqual(len(window.messages), 1)
        self.assertIn('light/dark choice could not be saved', window.messages[0])
        self.assertNotIn('private path', window.messages[0])

    def test_declined_dirty_close_does_not_commit_close_appearance(self):
        window, saved = Window(agree=False), []
        CloseCoordinator(window, Store(), save_appearance=saved.append)._review_close()
        self.assertEqual(saved, [])
        self.assertFalse(window.closed.is_set())

    def test_dirty_close_binds_storage_request_and_verified_receipt_to_review(self):
        window, store = Window(), Store()
        CloseCoordinator(window, store)._review_close()
        self.assertEqual(store.calls, [({'fixture': True}, GENERATION, 1)])
        self.assertTrue(window.closed.is_set())
        self.assertIn('recovery copy was saved', window.messages[0])

    def test_malformed_or_wrong_identity_receipt_cannot_claim_recovery_saved(self):
        replies = [None, {}, {'status': 'recovered-copy-saved'}, receipt(status='superseded'),
            receipt(generation='c' * 32), receipt(generation=None), receipt(revision=0),
            receipt(revision=True), receipt(revision=1.0), receipt(id='0-' + 'b' * 32),
            receipt(id='1-' + 'B' * 32), receipt(id='1' * 21 + '-' + 'b' * 32),
            receipt(id='../private'), {**receipt(), 'unexpected': True}]
        for reply in replies:
            with self.subTest(reply=reply):
                window, store = StalledWindow(stage=None, agree=False), Store(reply=reply)
                CloseCoordinator(window, store)._review_close()
                self.assertFalse(window.closed.is_set())
                self.assertTrue(window.resumed.is_set())
                self.assertIn('could not be confirmed', window.messages[0])
                self.assertNotIn('recovery copy was saved', window.messages[0])
                self.assertNotIn('private', window.messages[0])

    def test_invalid_generation_or_revision_never_reaches_storage(self):
        changes = [{'recoveryGeneration': value} for value in (None, '', 'A' * 32, 'a' * 31, True)]
        changes += [{'revision': value} for value in (True, -1, 1.0, 9007199254740992)]
        for change in changes:
            with self.subTest(change=change):
                window, store = Window(agree=False), Store()
                window.snapshot.update(change)
                CloseCoordinator(window, store)._review_close()
                self.assertEqual(store.calls, [])
                self.assertFalse(window.closed.is_set())
                self.assertIn('current recovery copy', window.messages[0])
        window, store = Window(agree=False), Store()
        del window.snapshot['recoveryGeneration']
        CloseCoordinator(window, store)._review_close()
        self.assertEqual(store.calls, [])
        self.assertFalse(window.closed.is_set())

    def test_clean_missing_generation_is_allowed_but_present_invalid_generation_is_not(self):
        window = Window(dirty=False)
        del window.snapshot['recoveryGeneration']
        CloseCoordinator(window, Store())._review_close()
        self.assertTrue(window.closed.is_set())
        self.assertEqual(window.messages, [])
        window = Window(dirty=False, agree=False)
        window.snapshot['recoveryGeneration'] = 'invalid'
        CloseCoordinator(window, Store())._review_close()
        self.assertFalse(window.closed.is_set())
        self.assertIn('current recovery copy', window.messages[0])

    def test_changed_generation_during_confirmation_cancels_close_even_at_same_revision(self):
        window, store = StalledWindow(stage=None, agree=True), Store()
        def changed_generation(title, message):
            window.snapshot['recoveryGeneration'] = 'c' * 32
            return True
        window.create_confirmation_dialog = changed_generation
        CloseCoordinator(window, store)._review_close()
        self.assertFalse(window.closed.is_set())
        self.assertTrue(window.resumed.is_set())
        self.assertEqual(store.calls[0][1:], (GENERATION, 1))

    def test_changed_document_during_confirmation_cancels_close_even_at_same_identity(self):
        window, store = StalledWindow(stage=None, agree=True), Store()
        def changed_content(title, message):
            window.snapshot['document']['fixture'] = False
            return True
        window.create_confirmation_dialog = changed_content
        CloseCoordinator(window, store)._review_close()
        self.assertFalse(window.closed.is_set())
        self.assertTrue(window.resumed.is_set())
        self.assertEqual(store.documents, [{'fixture': True}])

    def test_next_review_cannot_reuse_old_generation_success_receipt(self):
        window, store = StalledWindow(stage=None, agree=False), Store(reply=receipt())
        coordinator = CloseCoordinator(window, store)
        coordinator._review_close()
        self.assertIn('recovery copy was saved', window.messages[0])
        window.snapshot['recoveryGeneration'] = 'c' * 32
        coordinator._review_close()
        self.assertIn('could not be confirmed', window.messages[1])
        self.assertEqual([call[1] for call in store.calls], [GENERATION, 'c' * 32])
        self.assertFalse(window.closed.is_set())
        self.assertEqual(window.calls, ['begin', 'end', 'begin', 'end'])

    def test_unverified_recheck_does_not_upgrade_superseded_receipt_to_saved_assurance(self):
        window, store = StalledWindow(stage='recheck', agree=True), Store(reply=receipt(status='superseded'))
        window.agree = True
        def confirm(title, message):
            window.agree = not window.messages
            return StalledWindow.create_confirmation_dialog(window, title, message)
        window.create_confirmation_dialog = confirm
        coordinator = CloseCoordinator(window, store, evaluation_timeout=.05)
        worker, finished, errors = review_thread(coordinator)
        native = None
        try:
            self.assertTrue(window.entered.wait(1))
            native = coordinator._evaluations._worker
            self.assertTrue(finished.wait(.5))
            self.assertIn('could not be confirmed', window.messages[0])
            self.assertIn('could not confirm', window.messages[1])
            self.assertNotIn('reviewed revision was saved', window.messages[1])
            self.assertFalse(window.closed.is_set())
        finally:
            window.release.set()
            worker.join(1)
            if native is not None:
                native.join(1)
        self.assertEqual(errors, [])
        self.assertFalse(native.is_alive())

    def test_failed_destroy_does_not_irreversibly_stop_services(self):
        window, services = Window(dirty=False), {'live': True}
        def stop_services():
            services['live'] = False
        def failed_destroy():
            raise OSError('before native close')
        window.destroy = failed_destroy
        class ObservedCoordinator(CloseCoordinator):
            def observe_closed(self):
                stop_services()
                super().observe_closed()
        coordinator = ObservedCoordinator(window, Store())
        with self.assertRaises(OSError):
            coordinator._review_close()
        self.assertTrue(services['live'], 'A still-open window needs its existing live services.')

    def test_vetoed_native_close_return_does_not_count_as_closed(self):
        window = StalledWindow(stage=None)
        window.snapshot['dirty'] = False
        window.destroy = lambda: None  # FormClosing veto; Invoke/Close still returns.
        coordinator = CloseCoordinator(window, Store(), evaluation_timeout=.05)
        coordinator._review_close()
        self.assertFalse(coordinator._approved, 'Only FormClosed can confirm destruction.')
        self.assertTrue(window.resumed.is_set())
        self.assertFalse(window.closed.is_set())

    def test_actual_closed_evidence_wins_over_later_destroy_exception(self):
        window = StalledWindow(stage=None)
        window.snapshot['dirty'] = False
        def closed_then_failed():
            window.closed.set()
            raise OSError('vendor callback failed after actual close')
        window.destroy = closed_then_failed
        coordinator = CloseCoordinator(window, Store(), evaluation_timeout=.05)
        with self.assertRaises(OSError):
            coordinator._review_close()
        self.assertTrue(coordinator._approved, 'A closed editor must never be resumed.')
        self.assertFalse(window.resumed.is_set())

    def test_closed_notification_between_check_and_resume_is_terminal(self):
        window = StalledWindow(stage=None)
        window.snapshot['dirty'] = False
        window.destroy = lambda: None
        coordinator = CloseCoordinator(window, Store(), evaluation_timeout=.05)
        def close_after_negative_check():
            coordinator.observe_closed()
            return False  # The earlier check was stale before finally resumed.
        coordinator._has_closed = close_after_negative_check
        coordinator._review_close()
        self.assertTrue(coordinator._approved)
        self.assertTrue(coordinator._evaluations._closed)
        self.assertFalse(window.resumed.is_set())

    def test_failed_native_destroy_releases_editor_and_allows_another_review(self):
        window, store = StalledWindow(stage=None), Store()
        window.snapshot['dirty'] = False
        evaluate = window.evaluate_js
        suspended = [False]
        def editor(expression):
            if 'beginCloseReview' in expression:
                suspended[0] = True
            elif 'endCloseReview' in expression:
                suspended[0] = False
            return evaluate(expression)
        window.evaluate_js = editor
        destroy = window.destroy
        def fail_once():
            window.destroy = destroy
            raise OSError('native destroy failed before window closed')
        window.destroy = fail_once
        coordinator = CloseCoordinator(window, store, evaluation_timeout=.05)
        with self.assertRaisesRegex(OSError, 'native destroy failed'):
            coordinator._review_close()
        self.assertFalse(window.closed.is_set())
        self.assertFalse(coordinator._approved)
        self.assertFalse(coordinator._pending)
        self.assertFalse(suspended[0], 'The still-open editor must resume after destruction failed.')
        self.assertEqual(window.calls, ['begin', 'end'])
        coordinator._review_close()
        self.assertTrue(window.closed.is_set())
        self.assertEqual(window.calls, ['begin', 'end', 'begin'])
        self.assertEqual(window.messages, [])

    def test_failed_destroy_releases_older_outstanding_review_after_it_settles(self):
        # The second attempt cannot begin while the first timed-out call is
        # outstanding. If its explicit close then fails, release belongs to
        # the first token that still owns the editor, not the latest attempt.
        window, store = StalledWindow(), Store()
        def fail_destroy():
            raise OSError('native destroy failed before window closed')
        window.destroy = fail_destroy
        coordinator = CloseCoordinator(window, store, evaluation_timeout=.05)
        first, finished, errors = review_thread(coordinator)
        native = None
        try:
            self.assertTrue(window.entered.wait(1))
            native = coordinator._evaluations._worker
            self.assertTrue(finished.wait(.5))
            self.assertEqual(errors, [])
            window.agree = True
            with self.assertRaisesRegex(OSError, 'native destroy failed'):
                coordinator._review_close()
            self.assertFalse(coordinator._approved)
            self.assertFalse(coordinator._pending)
            self.assertFalse(window.closed.is_set())
            self.assertEqual(window.calls, ['begin'])
            self.assertEqual(store.documents, [])
        finally:
            window.release.set()
            first.join(1)
            if native is not None:
                native.join(1)
        self.assertFalse(native.is_alive())
        self.assertTrue(window.resumed.is_set(), 'Release must follow the older outstanding review.')
        self.assertEqual(window.calls, ['begin', 'end'])
        self.assertEqual(store.documents, [])
        self.assertFalse(window.closed.is_set())

    def test_failed_destroy_can_resume_after_pending_release_failed(self):
        window, store = StalledWindow(stage='end'), Store()
        evaluate = window.evaluate_js
        end_attempts = [0]
        def fail_first_release(expression):
            if 'endCloseReview' in expression:
                end_attempts[0] += 1
                if end_attempts[0] == 1:
                    window.calls.append('end')
                    window.entered.set()
                    window.release.wait()
                    raise OSError('release failed before editor resumed')
            return evaluate(expression)
        window.evaluate_js = fail_first_release
        def fail_destroy():
            raise OSError('native destroy failed before window closed')
        window.destroy = fail_destroy
        coordinator = CloseCoordinator(window, store, evaluation_timeout=.05)
        first, finished, errors = review_thread(coordinator)
        native = None
        try:
            self.assertTrue(window.entered.wait(1))
            native = coordinator._evaluations._worker
            self.assertTrue(finished.wait(.5))
            self.assertEqual(errors, [])
            def settle_failed_release():
                window.release.set()
                native.join(1)
            def settle_then_fail_destroy():
                settle_failed_release()
                fail_destroy()
            window.destroy = settle_then_fail_destroy
            window.agree = True
            with self.assertRaisesRegex(OSError, 'native destroy failed'):
                coordinator._review_close()
            self.assertFalse(native.is_alive())
            self.assertTrue(window.resumed.is_set(), 'A failed release must not discard its review token.')
            self.assertFalse(coordinator._approved)
            self.assertFalse(window.closed.is_set())
            # The second release occurs only after the first actually failed.
            self.assertEqual(window.calls, ['begin', 'end', 'end'])
        finally:
            window.release.set()
            first.join(1)
            if native is not None:
                native.join(1)

    def test_stalled_initial_evaluation_reaches_recovery_warning_before_native_return(self):
        # A missing caller deadline leaves the close review waiting forever and
        # never offers the user a recovery-aware choice.
        window, store = StalledWindow(), Store()
        coordinator = CloseCoordinator(window, store)
        coordinator._evaluation_timeout = .05
        finished = threading.Event()
        def review():
            try:
                coordinator._review_close()
            finally:
                finished.set()
        review_thread = threading.Thread(target=review, daemon=True)
        review_thread.start()
        try:
            self.assertTrue(window.entered.wait(1))
            self.assertTrue(window.prompted.wait(.5), 'The stalled native call prevented the close fallback.')
            self.assertTrue(finished.wait(.5), 'Declining close must release the caller without waiting for native completion.')
            self.assertFalse(window.closed.is_set())
            self.assertEqual(store.documents, [])
            self.assertEqual(window.calls, ['begin'])
            self.assertIn('recovery', window.messages[0].lower())
            self.assertIn('not', window.messages[0].lower())
        finally:
            window.release.set()
            review_thread.join(1)
        self.assertTrue(window.resumed.wait(1))
        self.assertFalse(window.closed.is_set())
        self.assertEqual(store.documents, [])

    def test_confirmed_close_after_initial_timeout_ignores_late_dirty_snapshot(self):
        # Approval belongs to the explicit fallback, not to a later native
        # result. That result must not save, prompt again or destroy twice.
        window, store = StalledWindow(agree=True), Store()
        coordinator = CloseCoordinator(window, store, evaluation_timeout=.05)
        review, finished, errors = review_thread(coordinator)
        native = None
        try:
            self.assertTrue(window.entered.wait(1))
            native = coordinator._evaluations._worker
            self.assertTrue(finished.wait(.5))
            self.assertTrue(window.closed.is_set())
            self.assertEqual(window.destroy_calls, 1)
            self.assertEqual(store.documents, [])
            self.assertEqual(window.calls, ['begin'])
        finally:
            window.release.set()
            review.join(1)
            if native is not None:
                native.join(1)
        self.assertEqual(errors, [])
        self.assertFalse(native.is_alive())
        self.assertEqual(window.destroy_calls, 1)
        self.assertEqual(len(window.messages), 1)
        self.assertEqual(window.calls, ['begin'])
        self.assertEqual(store.documents, [])

    def test_stalled_recheck_warns_only_reviewed_revision_was_recovered(self):
        # The initial dirty confirmation cannot authorize an unverifiable
        # latest state. Declining the second warning must survive a late reply.
        window, store = StalledWindow(stage='recheck'), Store()
        def confirm(title, message):
            window.agree = not window.messages
            return StalledWindow.create_confirmation_dialog(window, title, message)
        window.create_confirmation_dialog = confirm
        coordinator = CloseCoordinator(window, store, evaluation_timeout=.05)
        review, finished, errors = review_thread(coordinator)
        native = None
        try:
            self.assertTrue(window.entered.wait(1))
            native = coordinator._evaluations._worker
            self.assertTrue(finished.wait(.5))
            self.assertFalse(window.closed.is_set())
            self.assertEqual(store.documents, [{'fixture': True}])
            self.assertEqual(len(window.messages), 2)
            self.assertIn('reviewed revision', window.messages[1])
            self.assertIn('More recent changes may not be recovered', window.messages[1])
            self.assertEqual(window.calls, ['begin', 'recheck'])
        finally:
            window.release.set()
            review.join(1)
            if native is not None:
                native.join(1)
        self.assertEqual(errors, [])
        self.assertFalse(native.is_alive())
        self.assertEqual(window.calls, ['begin', 'recheck', 'end'])
        self.assertEqual(window.destroy_calls, 0)
        self.assertEqual(store.documents, [{'fixture': True}])

    def test_stalled_release_and_later_close_never_overlap_or_duplicate_native_calls(self):
        # A cancelled close must return even when resume stalls. A subsequent
        # attempt cannot submit a new begin or resume against that pending call.
        window, store = StalledWindow(stage='end'), Store()
        coordinator = CloseCoordinator(window, store, evaluation_timeout=.05)
        first, finished, errors = review_thread(coordinator)
        native = None
        second = None
        try:
            self.assertTrue(window.entered.wait(1))
            native = coordinator._evaluations._worker
            self.assertTrue(finished.wait(.5))
            second, again_finished, again_errors = review_thread(coordinator)
            self.assertTrue(again_finished.wait(.5))
            self.assertEqual(again_errors, [])
            self.assertEqual(window.calls, ['begin', 'end'])
            self.assertEqual(store.documents, [{'fixture': True}])
            self.assertFalse(window.closed.is_set())
        finally:
            window.release.set()
            first.join(1)
            if second is not None:
                second.join(1)
            if native is not None:
                native.join(1)
        self.assertEqual(errors, [])
        self.assertFalse(native.is_alive())
        self.assertEqual(window.calls, ['begin', 'end'])
        self.assertEqual(window.destroy_calls, 0)

    def test_invalid_or_missing_snapshot_never_counts_as_clean_work(self):
        # Missing readiness data must require a choice, not silently close.
        for snapshot in (None, {}, {'dirty': False, 'revision': True},
                         {'dirty': True, 'revision': 1, 'document': None}):
            with self.subTest(snapshot=snapshot):
                window, store = Window(agree=False), Store()
                window.evaluate_js = lambda expression: snapshot
                coordinator = CloseCoordinator(window, store, evaluation_timeout=.05)
                coordinator._review_close()
                self.assertFalse(window.closed.is_set())
                self.assertEqual(store.documents, [])
                self.assertEqual(len(window.messages), 1)
                self.assertIn('current recovery copy', window.messages[0])

    def test_dirty_close_creates_recovery_before_confirmation(self):
        window, store = Window(), Store()
        close = CloseCoordinator(window, store)
        self.assertFalse(close.request_close())
        self.assertTrue(window.closed.wait(2))
        self.assertEqual(store.documents, [{'fixture': True}])
        self.assertIn('recovery copy was saved', window.messages[0])
        self.assertTrue(close.request_close())

    def test_failed_recovery_and_declined_close_retain_window(self):
        for after_publication in (False, True):
            with self.subTest(after_publication=after_publication):
                window, store = Window(agree=False), Store(fail=not after_publication)
                if after_publication:
                    autosave = store.autosave
                    def written_but_unconfirmed(*args):
                        autosave(*args)
                        raise OSError('Synthetic readback failure after publication')
                    store.autosave = written_but_unconfirmed
                close = CloseCoordinator(window, store)
                close._review_close()
                self.assertFalse(window.closed.is_set())
                self.assertIn('could not be confirmed', window.messages[0])
                self.assertIn('may not be recovered', window.messages[0])
                self.assertNotIn('will lose', window.messages[0])
                self.assertNotIn('Synthetic', window.messages[0])
                self.assertEqual(len(store.documents), int(after_publication))

    def test_clean_close_needs_no_prompt(self):
        window, store = Window(dirty=False), Store()
        CloseCoordinator(window, store)._review_close()
        self.assertTrue(window.closed.is_set())
        self.assertEqual(window.messages, [])
        self.assertEqual(store.documents, [])

    def test_edit_during_confirmation_cancels_close(self):
        window, store = Window(), Store()
        def edit_then_confirm(title, message):
            window.snapshot['revision'] += 1
            return True
        window.create_confirmation_dialog = edit_then_confirm
        CloseCoordinator(window, store)._review_close()
        self.assertFalse(window.closed.is_set())

    def test_native_observer_wins_when_vendor_never_publishes_public_closed_event(self):
        window, store = Window(dirty=False), Store()
        coordinator = CloseCoordinator(window, store)
        def close_then_error():
            coordinator.observe_closed()
            raise OSError('vendor failed before its public event')
        window.destroy = close_then_error
        with self.assertRaises(OSError):
            coordinator._review_close()
        self.assertFalse(window.closed.is_set())
        self.assertTrue(coordinator._approved)
        self.assertTrue(coordinator._closed)


if __name__ == '__main__':
    unittest.main()
