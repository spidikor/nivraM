"""Named, immutable revisions of local drawing and PNG export settings."""
import json
from pathlib import Path
import re
import threading
import uuid
from .persistence import atomic_write, validate_project, _read_bounded
from .scene import checked_style

SCHEMA = 'nivram-profile/1'
MAX_RECORDS = 128


def validate_profile(value):
    keys = {'schema', 'id', 'revision', 'name', 'style', 'export'}
    if not isinstance(value, dict) or set(value) != keys or value['schema'] != SCHEMA:
        raise ValueError('Unsupported profile schema or fields.')
    if not isinstance(value['id'], str) or not re.fullmatch('[0-9a-f]{32}', value['id']):
        raise ValueError('Invalid profile identifier.')
    if isinstance(value['revision'], bool) or not isinstance(value['revision'], int) or not 1 <= value['revision'] <= 999999:
        raise ValueError('Invalid profile revision.')
    name = value['name']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Use a profile name of 1–80 printable characters.')
    document = validate_project({'schema': 'nivram/1', 'graph': {'atoms': [], 'bonds': []},
                                 'style': value['style'], 'export': value['export']})
    return {**value, 'name': name.strip(), 'style': checked_style(document['style']), 'export': document['export']}


class ProfileStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._lock = threading.RLock()

    def list(self):
        with self._lock:
            paths = list(self.directory.glob('*.json'))
            if len(paths) > MAX_RECORDS:
                raise ValueError('The profile store exceeds its supported record limit.')
            records, invalid = [], 0
            for path in paths:
                if not re.fullmatch(r'[0-9a-f]{32}-\d{6}\.json', path.name):
                    continue
                try:
                    record = validate_profile(json.loads(_read_bounded(path, 16_384)))
                    if path.name != f"{record['id']}-{record['revision']:06d}.json":
                        raise ValueError('Profile identity does not match its file.')
                    records.append(record)
                except (ValueError, OSError):
                    invalid += 1
            return {'profiles': sorted(records, key=lambda x: (x['name'].casefold(), x['id'], -x['revision'])),
                    'invalidRecords': invalid}

    def save(self, name, style, export, profile_id=None, expected_revision=None):
        with self._lock:
            listing = self.list()
            if listing['invalidRecords']:
                raise ValueError('A profile record is damaged. Existing revisions are retained; repair the store before saving.')
            if len(listing['profiles']) >= MAX_RECORDS:
                raise ValueError('The local profile history is full. Existing revisions are retained.')
            if profile_id is None:
                profile_id, revision = uuid.uuid4().hex, 1
            else:
                matches = [p for p in listing['profiles'] if p['id'] == profile_id]
                if not matches or max(p['revision'] for p in matches) != expected_revision:
                    raise ValueError('The profile changed or no longer exists. Reload before saving another revision.')
                revision = expected_revision + 1
            record = validate_profile({'schema': SCHEMA, 'id': profile_id, 'revision': revision,
                                       'name': name, 'style': style, 'export': export})
            data = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
            self.directory.mkdir(parents=True, exist_ok=True)
            destination = self.directory / f'{profile_id}-{revision:06d}.json'
            if destination.exists():
                raise ValueError('That profile revision already exists. It was not replaced.')
            atomic_write(destination, data)
            if _read_bounded(destination, 16_384) != data:
                raise OSError('The profile could not be verified after saving.')
            return record
