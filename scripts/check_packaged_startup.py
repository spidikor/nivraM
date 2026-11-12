"""Exercise the packaged real window with an isolated profile and reduced PATH."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    executable = ROOT / 'dist' / 'nivraM' / 'nivraM.exe'
    executable_hash = sha256(executable)
    runs = ROOT.parent / 'packaged-smoke-runs'
    runs.mkdir(parents=True, exist_ok=True)
    # Atomic reservation makes both child paths fresh without deleting any
    # earlier report or profile. Keep failed runs available for inspection.
    run = Path(tempfile.mkdtemp(prefix='run-', dir=runs))
    report, profile = run / 'report.json', run / 'profile'
    receipt_path = run / 'run.json'
    receipt = {
        'schemaVersion': 1, 'startedUtc': datetime.now(timezone.utc).isoformat(),
        'executableSha256': executable_hash, 'executableSha256After': None,
        'report': report.name, 'profile': profile.name, 'exitCode': None,
        'status': 'started',
    }
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    environment = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        environment.pop(key, None)
    environment['PATH'] = os.environ.get('SystemRoot', r'C:\Windows') + r'\System32'
    try:
        result = subprocess.run([str(executable), '--data-dir', str(profile),
                                 '--smoke-report', str(report)],
                                env=environment, creationflags=subprocess.CREATE_NO_WINDOW,
                                timeout=90)
        receipt['exitCode'] = result.returncode
        receipt['executableSha256After'] = sha256(executable)
        if receipt['executableSha256After'] != executable_hash:
            raise RuntimeError('Packaged executable changed during the startup check.')
        if result.returncode != 0:
            raise RuntimeError(f'Packaged application exited unsuccessfully ({result.returncode}).')
        if not report.is_file():
            raise RuntimeError('Packaged application produced no report for this startup run.')
        text = report.read_text(encoding='utf-8')
        payload = json.loads(text)
        if not isinstance(payload, dict) or payload.get('status') != 'passed':
            raise RuntimeError('Packaged application did not report a passed startup check.')
        receipt['status'] = 'passed'
    except Exception as error:
        receipt['status'] = 'failed'
        receipt['errorType'] = type(error).__name__
        raise
    finally:
        receipt['finishedUtc'] = datetime.now(timezone.utc).isoformat()
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
