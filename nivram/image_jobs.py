"""One bounded, cancellable local PNG metadata worker, separate from the UI."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import threading

MAX_INPUT = 64 * 1024 * 1024
MAX_OUTPUT = 16 * 1024 * 1024


class ImageJobCancelled(ValueError):
    pass


def worker_command():
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('nivram-worker.exe')), '--png-metadata']
    return [sys.executable, '-m', 'nivram.image_jobs', '--worker']


class ImageJobs:
    def __init__(self):
        self._lock = threading.RLock()
        self._active = None

    def inspect(self, data, job_id, *, timeout=45, _command=None, _cancel_event=None):
        if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', job_id):
            raise ValueError('Invalid image job identifier.')
        if not isinstance(data, bytes) or len(data) > MAX_INPUT:
            raise ValueError('PNG input exceeds the 64 MiB limit.')
        if not 0 < timeout <= 60:
            raise ValueError('Image worker timeout must be between zero and 60 seconds.')
        state = {'id': job_id, 'process': None, 'cancelled': False}
        with self._lock:
            if self._active is not None:
                raise ValueError('An image import is already running. Cancel it or wait for completion.')
            self._active = state
        process = None
        try:
            with self._lock:
                if state['cancelled'] or (_cancel_event is not None and _cancel_event.is_set()):
                    raise ImageJobCancelled('Image inspection was cancelled.')
                process = subprocess.Popen(_command or worker_command(), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                state['process'] = process
            try:
                output, _ = process.communicate(data, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.communicate(timeout=5)
                raise ValueError('Image inspection exceeded its time limit; the drawing is unchanged.') from exc
            with self._lock:
                if state['cancelled']:
                    raise ImageJobCancelled('Image inspection was cancelled.')
            if process.returncode != 0 or len(output) > MAX_OUTPUT:
                raise ValueError('Image metadata could not be inspected within the supported limits.')
            result = json.loads(output)
            if not isinstance(result, dict) or result.get('schemaVersion') != 1:
                raise ValueError('Image worker returned an invalid result.')
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            with self._lock:
                if self._active is state:
                    self._active = None

        # Cancellation can finish while finalization waits for the service lock.
        # Check this attempt after cleanup, without replacing an earlier failure.
        if state['cancelled'] or (_cancel_event is not None and _cancel_event.is_set()):
            raise ImageJobCancelled('Image inspection was cancelled.')
        return result

    def cancel(self, job_id):
        with self._lock:
            state = self._active
            if state is None or state['id'] != job_id:
                return {'status': 'not_running'}
            state['cancelled'] = True
            process = state['process']
            if process is not None and process.poll() is None:
                process.terminate()
            return {'status': 'cancelled'}

    def close(self):
        with self._lock:
            if self._active is not None:
                self.cancel(self._active['id'])


def worker_main():
    from .image_import import inspect_png_metadata
    try:
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            return 2
        result = inspect_png_metadata(data)
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_OUTPUT:
            return 2
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception:
        # Neither source content nor personal paths are emitted in a failed job.
        return 2


if __name__ == '__main__':
    raise SystemExit(worker_main() if sys.argv[1:] == ['--worker'] else 2)
