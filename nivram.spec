# Build on Windows with the isolated, pinned environment.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata
from pathlib import Path
import os
import json
import importlib.metadata
from datetime import datetime, timezone
from nivram import __version__
from nivram.naming_runtime import discover_naming_runtime

datas = [('public', 'public')]
datas += [('nivram/optical_worker.py', 'nivram')]
source_digest = os.environ.get('NIVRAM_SOURCE_SHA256', '')
if len(source_digest) != 64 or any(c not in '0123456789abcdef' for c in source_digest):
    raise RuntimeError('Use scripts/build_windows.py to bind this build to its source snapshot.')
metadata_dir = Path(SPECPATH) / 'build' / 'nivram-generated'
metadata_dir.mkdir(parents=True, exist_ok=True)
metadata_path = metadata_dir / 'build-info.json'
metadata_path.write_text(json.dumps({'status':'bound-at-build','sourceSha256':source_digest,
    'version':__version__,'builtAt':datetime.now(timezone.utc).isoformat(),
    'versions':{name:importlib.metadata.version(name) for name in ('rdkit','numpy','pillow','pywebview','pythonnet','nispo')}},indent=2),encoding='utf-8')
datas += [(str(metadata_path), 'nivram/data')]
runtime_bundle = Path(os.environ.get('NIVRAM_NAMING_BUNDLE', str(Path(SPECPATH).parent / 'naming-runtime' / 'bundle')))
runtime = discover_naming_runtime(runtime_bundle)
if runtime.status != 'available':
    raise RuntimeError('The pinned local naming runtime must validate before packaging: ' + runtime.message)
datas += [(str(runtime_bundle), 'naming-runtime')]
datas += [('nivram/data', 'nivram/data')]
datas += collect_data_files('webview', includes=['js/**', 'lib/**'])
datas += collect_data_files('rdkit', includes=['Data/**'], excludes=['**/*.ttf', '**/*.otf'])
datas += collect_data_files('rdkit', includes=['_nivram_native/manifest.json'])
datas += copy_metadata('rdkit')
private_rdkit_metadata = 'rdkit-' + importlib.metadata.version('rdkit') + '.dist-info/direct_url.json'
a = Analysis(['run_desktop.py'], pathex=[], binaries=collect_dynamic_libs('rdkit', search_patterns=['*.dll', '*.pyd']),
             datas=datas, hiddenimports=['webview.platforms.edgechromium', 'webview.platforms.winforms'],
             excludes=['tkinter', 'pytest', 'playwright'], noarchive=False)
# RDKit hooks may collect optional fonts; the renderer uses installed system fonts.
a.datas = [item for item in a.datas if not item[0].lower().endswith(('.ttf', '.otf', '.ttc', '.otc', '.woff', '.woff2', '.eot'))
           and item[0].replace('\\', '/') != private_rdkit_metadata]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='nivraM',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
worker = Analysis(['run_worker.py'], pathex=[], binaries=collect_dynamic_libs('rdkit', search_patterns=['*.dll', '*.pyd']),
                  datas=collect_data_files('nispo') + copy_metadata('nispo')
                      + collect_data_files('rdkit', includes=['_nivram_native/manifest.json']) + copy_metadata('rdkit'),
                  hiddenimports=['nispo'], excludes=['webview','playwright','tkinter'], noarchive=False)
worker.datas = [item for item in worker.datas if not item[0].lower().endswith(('.ttf','.otf','.ttc','.otc','.woff','.woff2','.eot'))
                and item[0].replace('\\', '/') != private_rdkit_metadata]
worker_pyz = PYZ(worker.pure)
worker_exe = EXE(worker_pyz, worker.scripts, [], exclude_binaries=True, name='nivram-worker',
                 debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, worker_exe, a.binaries, a.datas, worker.binaries, worker.datas,
               strip=False, upx=False, name='nivraM')
