"""Real stdlib-only math/validation bridge for offline JS transport regressions."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nivram.population_jobs import evaluate_request, _complete_model
from nivram.population_export import validate_payload, PopulationExportSaver
from nivram.reference_export import _csv_document

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)

value = json.load(sys.stdin)
if sys.argv[1] == 'evaluate':
    sys.stdout.buffer.write(evaluate_request(value))
elif sys.argv[1] == 'validate':
    model = _complete_model(value['modelText']).to_dict()
    for kind in ('json', 'csv'):
        payload = value[kind]
        assert validate_payload(kind, payload) == payload.encode()
        document = (json.loads(payload) if kind == 'json' else
                    _csv_document(payload, max_rows=3000000, max_depth=20, max_items=4096))
        assert canonical(document['populationResults']['model']) == canonical(model)
        assert document['source']['modelInputHash'] == value['modelInputHash']
        for row in document['populationResults']['samples']:
            if row['status'] == 'computed':
                assert canonical(row['result']['conditions']) == canonical(model['conditions'])
        window = SimpleNamespace(create_file_dialog=Mock(return_value=None))
        saver = PopulationExportSaver()
        try:
            with patch.dict(sys.modules, {'webview': SimpleNamespace(FileDialog=SimpleNamespace(SAVE='SAVE'))}):
                receipt = saver.save(window, kind, payload, 'test-token', 'test-token')
            assert receipt == {'status': 'cancelled'} and window.create_file_dialog.call_count == 1
        finally:
            saver.close()
    print(json.dumps({'validated':['json','csv'], 'nativeStatus':'cancelled', 'exactModelTypes':True}))
else:
    raise SystemExit('Unsupported helper mode')
