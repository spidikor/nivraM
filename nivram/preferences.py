"""Small durable appearance record, separate from documents and WebView sessions."""
import json
from pathlib import Path
import threading
import uuid

from .persistence import _read_bounded, atomic_write


class AppearanceStore:
    def __init__(self, directory):
        self._path = Path(directory) / 'appearance.json'
        self._lock = threading.RLock()
        self.begin_page()

    def begin_page(self):
        with self._lock:
            self.generation = uuid.uuid4().hex
            self._revision = -1
            self._saved = None

    def read(self):
        with self._lock:
            try:
                value = json.loads(_read_bounded(self._path, 4096))
                if (not isinstance(value, dict) or set(value) != {'schema', 'theme'}
                        or value['schema'] != 'nivram-appearance/1'
                        or value['theme'] not in ('light', 'dark')):
                    raise ValueError('Invalid appearance record.')
                theme, status = value['theme'], 'loaded'
            except FileNotFoundError:
                theme, status = 'light', 'default'
            except (OSError, ValueError, UnicodeError):
                theme, status = 'light', 'unavailable'
            return {'theme': theme, 'status': status, 'generation': self.generation}

    def save(self, theme, generation, revision):
        if (not isinstance(theme, str) or theme not in ('light', 'dark')
                or not isinstance(generation, str) or len(generation) != 32
                or type(revision) is not int or not 0 <= revision <= 9007199254740991):
            raise ValueError('Invalid appearance preference.')
        with self._lock:
            if generation != self.generation or revision < self._revision:
                return {'status': 'superseded'}
            if revision == self._revision and self._saved is not None:
                if self._saved != theme:
                    raise ValueError('An appearance revision cannot name two choices.')
                return {'status': 'saved', 'theme': theme, 'revision': revision}
            # Reserve the newest sequence even on write failure. An older bridge
            # request may not overwrite it; the same sequence may retry safely.
            self._revision, self._saved = revision, None
            data = json.dumps({'schema': 'nivram-appearance/1', 'theme': theme},
                              sort_keys=True, separators=(',', ':')).encode('utf-8')
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(self._path, data)
                if _read_bounded(self._path, 4096) != data:
                    raise OSError('Appearance verification failed.')
            except (OSError, ValueError) as error:
                raise OSError('Appearance preference could not be saved.') from error
            self._saved = theme
            return {'status': 'saved', 'theme': theme, 'revision': revision}

    def save_for_close(self, appearance):
        if (not isinstance(appearance, dict) or set(appearance) != {'theme', 'revision', 'generation'}
                or type(appearance['revision']) is not int or appearance['revision'] < 0):
            raise ValueError('Appearance close state is unavailable.')
        if appearance['revision'] == 0:
            # Closing during initial loading must not replace a saved mode with
            # the temporary default. No interaction means no preference write.
            return
        generation = appearance['generation'] or self.generation
        result = self.save(appearance['theme'], generation, appearance['revision'])
        if result['status'] != 'saved':
            raise ValueError('Appearance close state is no longer current.')
