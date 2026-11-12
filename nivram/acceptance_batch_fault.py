"""Acceptance-only faults after real publication, with bounded observer cleanup.

The bridge case raises after the real manager returns a verified PNG receipt.
The manifest case raises after the real atomic writer commits its exact bytes,
before the batch manager can acknowledge or verify that manifest publication.
"""
from __future__ import annotations

import copy
import hashlib
import math
import threading

from . import batch_export
from .acceptance_batch_png import _ordinary_directory, _read_owned, MAX_PNG_BYTES, MAX_MANIFEST_BYTES

MODES = ('lost-ack', 'manifest-uncertain')
MANIFEST_ERROR = 'The latest manifest could not be saved; completed PNGs were retained.'


class PublicationFaultObserver:
    def __init__(self, manager, output, mode):
        if mode not in MODES:
            raise ValueError('Unsupported fixed publication fault.')
        self.manager, self.output, self.mode = manager, _ordinary_directory(output), mode
        self._lock, self._idle = threading.RLock(), threading.Event()
        self._idle.set()
        self._active, self._closing, self.attached = 0, False, False
        names = ('prepare', 'write_png', 'cancel', 'fail', 'finish', '_manifest')
        self.originals = {name: getattr(manager, name) for name in names}
        self.previous = {name: manager.__dict__.get(name) for name in names}
        self.values = {name: 0 for name in ('prepareCalls', 'writeCalls', 'cancelCalls', 'failCalls',
            'finishCalls', 'manifestCalls', 'faultCount', 'atomicCommits')}
        self.values.update(mode=mode, preparationReady=False, writeResultStatus=None,
            writeManifestError=None, methodsRestored=False, atomicWriterRestored=True)
        self.chosen = self.written = self.terminal = None
        self.png = self.committed_manifest = None

        def enter(counter):
            with self._lock:
                if self._closing:
                    raise RuntimeError('The publication observer is closing.')
                self.values[counter] += 1
                self._active += 1
                self._idle.clear()

        def leave():
            with self._lock:
                self._active -= 1
                if not self._active:
                    self._idle.set()

        def prepare(*args, **kwargs):
            enter('prepareCalls')
            try:
                with manager._lock:
                    if self.chosen is None:
                        self.chosen = manager._receipt(manager._session)
                result = self.originals['prepare'](*args, **kwargs)
                self.values['preparationReady'] = type(result) is dict and result.get('status') == 'ready'
                return result
            finally:
                leave()

        def write(*args, **kwargs):
            enter('writeCalls')
            try:
                result = self.originals['write_png'](*args, **kwargs)
                if type(result) is not dict or result.get('status') != 'exported':
                    raise AssertionError('The injected fault requires a real exported PNG.')
                item = result['item']
                self.png = _read_owned(self.output, self.output / item['outputName'], MAX_PNG_BYTES)
                if hashlib.sha256(self.png).hexdigest() != item['outputSha256']:
                    raise AssertionError('The fault boundary lacks the verified published PNG.')
                self.written = copy.deepcopy(result)
                self.values.update(writeResultStatus=result['status'], writeManifestError=result.get('manifestError'),
                                   publishedPngSha256=item['outputSha256'])
                if self.mode == 'lost-ack':
                    if result.get('manifestError') is not None or self.values['faultCount']:
                        raise AssertionError('The lost acknowledgement must follow one successful write.')
                    self.values['faultCount'] += 1
                    raise RuntimeError('Synthetic PNG write acknowledgement unavailable.')
                return result
            finally:
                leave()

        def manifest(session, *args, **kwargs):
            enter('manifestCalls')
            try:
                target = (self.mode == 'manifest-uncertain' and self.chosen is not None
                    and session['id'] == self.chosen['sessionId'] and session['directory'] == self.output
                    and [item['status'] for item in session['items']] == ['exported', 'pending']
                    and self.values['faultCount'] == 0)
                if not target:
                    return self.originals['_manifest'](session, *args, **kwargs)
                original_atomic = batch_export.atomic_write
                target_path = session['manifest']

                def atomic(path, data):
                    if path != target_path or path.parent != self.output or self.values['atomicCommits']:
                        raise AssertionError('The injected manifest writer received an unexpected target.')
                    original_atomic(path, data)
                    actual = _read_owned(self.output, path, MAX_MANIFEST_BYTES)
                    if actual != data:
                        raise AssertionError('The real atomic manifest commit could not be observed.')
                    self.committed_manifest = bytes(actual)
                    self.values['atomicCommits'] += 1
                    self.values['faultCount'] += 1
                    self.values['committedManifestSha256'] = hashlib.sha256(actual).hexdigest()
                    raise OSError('Synthetic manifest publication acknowledgement unavailable.')

                batch_export.atomic_write = atomic
                self.values['atomicWriterRestored'] = False
                try:
                    return self.originals['_manifest'](session, *args, **kwargs)
                finally:
                    if batch_export.atomic_write is not atomic:
                        raise RuntimeError('The observed atomic writer was replaced during acceptance.')
                    batch_export.atomic_write = original_atomic
                    self.values['atomicWriterRestored'] = True
            finally:
                leave()

        def observed(name, counter):
            def invoke(*args, **kwargs):
                enter(counter)
                try:
                    result = self.originals[name](*args, **kwargs)
                    if name == 'cancel':
                        self.terminal = copy.deepcopy(result)
                    return result
                finally:
                    leave()
            return invoke

        self.methods = {'prepare': prepare, 'write_png': write, '_manifest': manifest,
            'cancel': observed('cancel', 'cancelCalls'), 'fail': observed('fail', 'failCalls'),
            'finish': observed('finish', 'finishCalls')}

    def attach(self):
        with self._lock:
            if self.attached or self._closing:
                raise RuntimeError('The fault observer is already attached or closing.')
            for name, method in self.methods.items():
                setattr(self.manager, name, method)
            self.attached = True

    def snapshot(self):
        with self._lock:
            return dict(self.values)

    def assert_complete(self):
        value = self.snapshot()
        expected_error = None if self.mode == 'lost-ack' else MANIFEST_ERROR
        if not (value['prepareCalls'] == value['writeCalls'] == value['cancelCalls'] == value['faultCount'] == 1
            and value['failCalls'] == value['finishCalls'] == 0
            and value['manifestCalls'] == (4 if self.mode == 'lost-ack' else 3)
            and value['atomicCommits'] == (0 if self.mode == 'lost-ack' else 1)
            and value['preparationReady'] is True and value['writeResultStatus'] == 'exported'
            and value['writeManifestError'] == expected_error and value['atomicWriterRestored'] is True
            and self.chosen is not None and self.written is not None and self.terminal is not None):
            raise AssertionError('The fixed native publication fault sequence was not observed.')

    def close(self, timeout=2):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 2:
            raise ValueError('Invalid publication observer cleanup bound.')
        with self._lock:
            self._closing = True
        stopped = self._idle.wait(timeout)
        with self._lock:
            stopped = stopped and self._active == 0
            if self.attached and stopped:
                if any(getattr(self.manager, name) is not method for name, method in self.methods.items()):
                    raise RuntimeError('An observed batch method was replaced during acceptance.')
                for name, previous in self.previous.items():
                    if previous is None:
                        delattr(self.manager, name)
                    else:
                        setattr(self.manager, name, previous)
                self.attached = False
                self.values['methodsRestored'] = True
            return {'shutdownConfirmed': stopped and not self.attached and self.values['atomicWriterRestored'],
                    **self.snapshot()}
