"""Fixed synthetic drawing/profile/PNG scenario for real native acceptance.

The shared harness owns the window, bounded evaluator, dialogs and cleanup.
This scenario neither installs software nor exercises native batch publication.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import re
import time

from PIL import Image

REQUIRED_CHECKS = frozenset({
    'synthetic-hash-import-and-style',
    'native-spaced-profile-disk-readback-and-apply',
    'native-main-and-preview-png-rgba-equality',
    'native-theme-png-rgba-equality-and-document-nonmutation',
})
SMILES = 'N[C@H](C)O'
PROFILE_NAME = 'Synthetic spaced drawing PNG'
OPTIONS = {'width': 640, 'marginMode': 'pixels', 'margin': 12, 'transparentBackground': False}
MAX_PNG_BYTES = 32 * 1024 * 1024
PNG_FILENAMES = {'main': 'spaced main \u00fc.png', 'preview': 'spaced preview \u00fc.png',
                 'theme': 'spaced other theme \u00fc.png'}
STATE = """(() => ({snapshot:window.nivramDocument.getSnapshot(),
    drawing:document.getElementById('drawing').outerHTML,
    undoDisabled:document.getElementById('undo-button').disabled,
    redoDisabled:document.getElementById('redo-button').disabled}))()"""
READY = "document.getElementById('request-status').textContent==='Ready'"
PREVIEW_READY = """(() => {
    const dialog=document.getElementById('png-preview-dialog'), image=document.getElementById('png-preview-image');
    const saves=dialog?[...dialog.querySelectorAll('button')].filter(button=>button.textContent==='Save this PNG'):[];
    return dialog?.open && image?.complete && image.currentSrc && image.naturalWidth===640 &&
        image.naturalHeight>24 && saves.length===1 && !saves[0].disabled;
})()"""


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_preserved(before, after):
    _require(type(before) is dict and type(after) is dict and before == after,
             'The native PNG operation changed the complete document, revision, history controls or drawing.')


def assert_transition(before, after, expected_document, *, redo_disabled=True):
    """One intentional, undoable appearance change; never a chemical change."""
    _require(type(before) is dict and type(after) is dict, 'The appearance history snapshots are missing.')
    a, b = before['snapshot'], after['snapshot']
    _require(type(a['revision']) is int and type(b['revision']) is int and b['revision'] == a['revision'] + 1
             and b['document'] == expected_document and b['document']['graph'] == a['document']['graph']
             and b['document']['export'] == a['document']['export']
             and b['dirty'] == a['dirty'] and b.get('recoveryGeneration') == a.get('recoveryGeneration'),
             'The appearance operation did not preserve the graph/settings as one history change.')
    _require(after['undoDisabled'] is False and after['redoDisabled'] is redo_disabled,
             'The appearance operation did not preserve the expected Undo/Redo availability.')


def validate_png_bytes(data):
    """Decode every RGBA pixel; return metadata plus private comparison bytes."""
    _require(type(data) is bytes and 0 < len(data) <= MAX_PNG_BYTES
             and data.startswith(b'\x89PNG\r\n\x1a\n'), 'The native output is not a bounded PNG.')
    try:
        with Image.open(io.BytesIO(data)) as image:
            _require(image.format == 'PNG' and image.width == 640 and 24 < image.height <= 4096,
                     'The native PNG dimensions or format differ from the fixed scenario.')
            image.load()
            with image.convert('RGBA') as rgba:
                with rgba.getchannel('A') as alpha:
                    _require(alpha.getextrema() == (255, 255), 'The native PNG contains a nonopaque pixel.')
                white = ((255, 255),) * 4
                width, height = rgba.size
                for rectangle in ((0, 0, width, 12), (0, height - 12, width, height),
                                  (0, 0, 12, height), (width - 12, 0, width, height)):
                    with rgba.crop(rectangle) as border:
                        _require(border.getextrema() == white, 'A native PNG margin is not opaque white.')
                _require(rgba.getextrema() != white, 'The native PNG contains no nonwhite drawing ink.')
                pixels = rgba.tobytes()
    except (OSError, ValueError, SyntaxError) as error:
        raise AssertionError('The native PNG could not be decoded completely.') from error
    return {'width': width, 'height': height, 'padding': 12, 'opaque': True, 'whiteBorders': True,
            'nonwhiteInk': True, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'rgbaSha256': hashlib.sha256(pixels).hexdigest()}, pixels


def assert_rgba_equal(left, right):
    _require(left[0]['width'] == right[0]['width'] and left[0]['height'] == right[0]['height']
             and left[1] == right[1], 'Native main, preview or theme PNG pixels differ.')


def preview_pixel_observation(identity):
    """Bounded status/dimensions/digest only; never pixel data or exception text."""
    value = identity if type(identity) is dict else {}
    digest = value.get('rgbaSha256')
    return {'status': value.get('status') if value.get('status') in ('pending', 'ready', 'failed') else 'invalid',
            'width': value.get('width') if type(value.get('width')) is int and 0 < value['width'] <= 640 else None,
            'height': value.get('height') if type(value.get('height')) is int and 0 < value['height'] <= 4096 else None,
            'rgbaSha256': digest if type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest) else None}


def assert_displayed_pixels_saved(identity, decoded):
    observed = preview_pixel_observation(identity)
    _require(observed['status'] == 'ready' and observed['width'] == 640
             and type(observed['height']) is int and 24 < observed['height'] <= 4096
             and observed['rgbaSha256'] is not None
             and decoded[0]['width'] == observed['width'] and decoded[0]['height'] == observed['height']
             and decoded[0]['rgbaSha256'] == observed['rgbaSha256'],
             'The native preview save differs from the complete displayed RGBA pixel identity.')


def _semantic_hash_classes(graph):
    from rdkit import Chem
    from rdkit.Chem.Draw import rdMolDraw2D
    from .chemistry import graph_to_mol
    before = copy.deepcopy(graph)
    mol = graph_to_mol(graph)
    expected = Chem.MolFromSmiles(SMILES)
    _require(Chem.MolToSmiles(mol, isomericSmiles=True) == Chem.MolToSmiles(expected, isomericSmiles=True),
             'The actual native import does not preserve the fixed synthetic chemical identity.')
    drawing = rdMolDraw2D.PrepareMolForDrawing(Chem.Mol(mol), kekulize=True,
        addChiralHs=False, wedgeBonds=True, forceCoords=False)
    classes = sorted(f'bond-{bond.GetIdx()} atom-{bond.GetBeginAtomIdx()} atom-{bond.GetEndAtomIdx()}'
                     for bond in drawing.GetBonds() if bond.GetBondDir() == Chem.BondDir.BEGINDASH)
    _require(graph == before and len(classes) == 1, 'The synthetic fixture lacks its one unchanged semantic hashed stereobond.')
    return classes


def _hash_expression(classes):
    return """(() => {
        const classes=CLASSES, drawing=document.getElementById('drawing');
        return classes.map(cls=>{const paths=[...drawing.querySelectorAll('path')].filter(path=>path.getAttribute('class')===cls);
            return {cls,count:paths.length,first:paths[0]?.getAttribute('d'),last:paths.at(-1)?.getAttribute('d'),
                widths:paths.map(path=>Number.parseFloat(path.style.strokeWidth)),
                solid:paths.every(path=>path.style.fill==='none' && !/url\\s*\\(/i.test(path.getAttribute('style')??''))};});
    })()""".replace('CLASSES', json.dumps(classes))


def _assert_hash_style(engine, spaced):
    _require(type(engine) is list and type(spaced) is list and len(engine) == len(spaced) == 1,
             'The native drawing did not retain its one semantic hash group.')
    a, b = engine[0], spaced[0]
    _require(a['cls'] == b['cls'] and a['first'] == b['first'] and a['last'] == b['last']
             and 3 <= b['count'] < a['count'] and len(a['widths']) == a['count'] and len(b['widths']) == b['count']
             and all(value == 2.1 for value in a['widths']) and all(value == 4.2 for value in b['widths'])
             and a['solid'] is True and b['solid'] is True,
             'The native optional hash appearance changed endpoints or failed its width/count contract.')
    return {'semanticClass': a['cls'], 'engineBars': a['count'], 'spacedBars': b['count'],
            'engineWidth': 2.1, 'spacedWidth': 4.2, 'endpointsPreserved': True}


def run(*, evaluate, poll, choose, check, stage, report, profile, api, downloads, window):
    from .acceptance_click import click_png_button, NativeClickRejected
    deadline = time.monotonic() + 120
    def remaining(cap):
        value = min(cap, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('The focused native drawing PNG scenario exceeded its deadline.')
        return value
    def ev(expression, label):
        return evaluate(expression, timeout=remaining(10), stage=label)
    def wait(expression):
        return poll(expression, timeout=remaining(15))
    def state():
        return ev(STATE, 'drawing-png-document-snapshot')
    def ready():
        wait(READY)

    profile = Path(profile)
    fixtures = profile / 'synthetic fixtures'
    _require(profile.is_dir() and not profile.is_symlink() and not profile.is_junction(), 'The isolated native profile is unavailable.')
    _require(not fixtures.is_symlink() and not fixtures.is_junction(), 'The synthetic fixture directory is redirected.')
    fixtures.mkdir(exist_ok=True)
    paths = {key: fixtures / name for key, name in PNG_FILENAMES.items()}
    _require(not any(path.exists() or path.is_symlink() for path in paths.values()), 'Native PNG evidence targets must be fresh.')
    report['drawing_png_scenario'] = {'fixture': SMILES, 'bondWidth': 4.2, 'hashBondStyle': 'spaced',
        'export': dict(OPTIONS), 'nativeBatchVerified': False,
        'scope': 'Fixed synthetic native profile and actual main/preview/theme PNG saves; no corpus calibration.'}

    def save_png(kind):
        stage('native-drawing-' + kind + '-png-save')
        before = state()
        prior = downloads.snapshot()['last_id']
        _require(type(prior) is int and prior >= 0, 'The native download identity is invalid.')
        choice = choose(paths[kind], timeout=remaining(15))
        try:
            gesture = click_png_button(window, evaluate, 'preview' if kind == 'preview' else 'main')
        except NativeClickRejected as error:
            report['drawing_png_click_failure'] = {'kind': kind, 'code': error.code}
            raise
        selection = choice.finish()
        receipt = downloads.wait_for_download(after_id=prior, timeout_seconds=remaining(15))
        _require(type(receipt.get('id')) is int and receipt['id'] > prior and receipt.get('state') == 'Completed'
                 and receipt.get('settled') is True and receipt.get('cancel_requested') is False
                 and receipt.get('failure_code') is None, 'The native PNG download did not confirm settled completion.')
        ready()
        with paths[kind].open('rb') as stream:
            data = stream.read(MAX_PNG_BYTES + 1)
        decoded = validate_png_bytes(data)
        assert_preserved(before, state())
        return decoded, {'fileName': paths[kind].name, 'download': receipt, 'dialog': selection, 'nativeGesture': gesture, **decoded[0]}

    stage('native-drawing-import-and-style')
    ev("""(() => {document.getElementById('import-button').click();
        document.getElementById('structure-input').value='N[C@H](C)O';
        document.getElementById('load-structure').click();return true;})()""", 'drawing-png-import-buttons')
    wait("!document.getElementById('import-dialog').open && " + READY
         + " && window.nivramDocument.getSnapshot().document.graph.atoms.length===4")
    imported = state()
    classes = _semantic_hash_classes(imported['snapshot']['document']['graph'])
    ev("document.getElementById('bond-width').value='4.2';document.getElementById('hash-bond-style').value='engine';document.getElementById('apply-style').click();true",
       'drawing-png-fixed-engine-width-button')
    wait(READY + " && window.nivramDocument.getSnapshot().document.style.bondWidth===4.2 && window.nivramDocument.getSnapshot().document.style.hashBondStyle==='engine'")
    engine = ev(_hash_expression(classes), 'drawing-png-engine-hash-metadata')
    ev("""(() => {
        const $=id=>document.getElementById(id);
        $('export-width').value='640';$('export-width').dispatchEvent(new Event('input'));
        $('margin-mode').value='pixels';$('margin-mode').dispatchEvent(new Event('change'));
        $('margin-value').value='12';$('margin-value').dispatchEvent(new Event('input'));
        $('bond-width').value='4.2';$('hash-bond-style').value='spaced';$('apply-style').click();return true;
    })()""", 'drawing-png-apply-spaced-buttons')
    wait(READY + " && window.nivramDocument.getSnapshot().document.style.hashBondStyle==='spaced'")
    spaced = state()
    _require(spaced['snapshot']['document']['graph'] == imported['snapshot']['document']['graph']
             and spaced['snapshot']['document']['export'] == OPTIONS
             and spaced['snapshot']['document']['style']['bondWidth'] == 4.2, 'The native appearance or fixed PNG settings differ.')
    hash_metadata = _assert_hash_style(engine, ev(_hash_expression(classes), 'drawing-png-spaced-hash-metadata'))
    check('synthetic-hash-import-and-style', fixture=SMILES, graphPreserved=True, **hash_metadata)

    stage('native-drawing-profile-save-and-readback')
    wait("!!document.getElementById('save-profile-button')")
    before_save = state()
    ev("""(() => {document.querySelector('.profile-controls').open=true;
        document.getElementById('profile-name').value='Synthetic spaced drawing PNG';
        document.getElementById('save-profile-button').click();return true;})()""", 'drawing-png-save-profile-button')
    wait("document.getElementById('profile-choice').options.length===2 && !document.getElementById('apply-profile-button').disabled")
    assert_preserved(before_save, state())
    from .profiles import ProfileStore
    profile_directory = Path(api._profiles.directory)
    _require(profile_directory.resolve().is_relative_to(profile.resolve())
             and not profile_directory.is_symlink() and not profile_directory.is_junction(),
             'The native profile store is outside the isolated acceptance profile.')
    listing = ProfileStore(profile_directory).list()
    _require(listing == api.list_profiles() and listing['invalidRecords'] == 0 and len(listing['profiles']) == 1,
             'Fresh native profile disk readback differs from the bridge store.')
    record = listing['profiles'][0]
    _require(record['name'] == PROFILE_NAME and record['revision'] == 1 and record['style'] == spaced['snapshot']['document']['style']
             and record['export'] == OPTIONS and ev("document.getElementById('profile-choice').value", 'drawing-png-profile-selected-id')
             == f"{record['id']}/1", 'The saved native profile does not retain the exact spaced settings.')
    ev("document.getElementById('hash-bond-style').value='engine';document.getElementById('apply-style').click();true",
       'drawing-png-apply-engine-button')
    wait(READY + " && window.nivramDocument.getSnapshot().document.style.hashBondStyle==='engine'")
    engine_state = state()
    expected_engine = copy.deepcopy(spaced['snapshot']['document']); expected_engine['style']['hashBondStyle'] = 'engine'
    assert_transition(before_save, engine_state, expected_engine)
    ev("document.getElementById('apply-profile-button').click();true", 'drawing-png-apply-profile-button')
    wait(READY + " && window.nivramDocument.getSnapshot().document.style.hashBondStyle==='spaced' && !document.getElementById('apply-profile-button').disabled")
    reapplied = state(); assert_transition(engine_state, reapplied, spaced['snapshot']['document'])
    ev("document.getElementById('undo-button').click();true", 'drawing-png-profile-undo-button')
    wait(READY + " && window.nivramDocument.getSnapshot().document.style.hashBondStyle==='engine'")
    undone = state(); assert_transition(reapplied, undone, expected_engine, redo_disabled=False)
    ev("document.getElementById('redo-button').click();true", 'drawing-png-profile-redo-button')
    wait(READY + " && window.nivramDocument.getSnapshot().document.style.hashBondStyle==='spaced'")
    redone = state(); assert_transition(undone, redone, spaced['snapshot']['document'])
    check('native-spaced-profile-disk-readback-and-apply', profileId=record['id'], revision=1,
          freshDiskReadback=True, graphPreserved=True, oneHistoryChangePerAction=True, undoRedo=True)

    baseline = state()
    main, main_receipt = save_png('main')
    stage('native-drawing-exact-preview')
    preview_before = state()
    ev("document.getElementById('preview-profile-button').click();true", 'drawing-png-preview-button')
    wait(PREVIEW_READY)
    stage('native-drawing-preview-document-nonmutation')
    assert_preserved(preview_before, state())
    ev("""(() => {window.__nivramDrawingPngPreview={status:'pending'};
        void (async()=>{try{
            const image=document.getElementById('png-preview-image'), width=image.naturalWidth,height=image.naturalHeight;
            if(!image.complete || !image.currentSrc || width!==640 || !Number.isInteger(height) || height<=24 || height>4096)throw new Error();
            const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
            const context=canvas.getContext('2d',{willReadFrequently:true});if(!context)throw new Error();
            context.drawImage(image,0,0);
            const pixels=context.getImageData(0,0,width,height).data;
            const digest=new Uint8Array(await crypto.subtle.digest('SHA-256',pixels));
            window.__nivramDrawingPngPreview={status:'ready',width,height,
                rgbaSha256:Array.from(digest,value=>value.toString(16).padStart(2,'0')).join('')};
        }catch{window.__nivramDrawingPngPreview={status:'failed'};}})();return true;})()""", 'drawing-png-preview-pixel-identity')
    preview_identity = wait("window.__nivramDrawingPngPreview?.status!=='pending' && window.__nivramDrawingPngPreview")
    observed_preview = preview_pixel_observation(preview_identity)
    report['drawing_png_preview'] = observed_preview
    stage('native-drawing-preview-displayed-pixel-identity')
    _require(observed_preview['status'] == 'ready' and observed_preview['width'] == 640
             and type(observed_preview['height']) is int and 24 < observed_preview['height'] <= 4096
             and observed_preview['rgbaSha256'] is not None, 'The displayed preview pixel identity is unconfirmed.')
    preview, preview_receipt = save_png('preview')
    stage('native-drawing-preview-saved-pixel-identity')
    assert_displayed_pixels_saved(observed_preview, preview)
    assert_rgba_equal(main, preview)
    ev("""(() => {const closes=[...document.getElementById('png-preview-dialog').querySelectorAll('button')]
        .filter(button=>button.textContent==='Close / cancel preview');if(closes.length!==1)throw new Error('Preview close unavailable');
        closes[0].click();return true;})()""", 'drawing-png-close-preview-button')
    assert_preserved(baseline, state())
    check('native-main-and-preview-png-rgba-equality', main=main_receipt, preview=preview_receipt,
          displayedPreviewPixelsSaved=True, decodedPixelIdentity=observed_preview,
          rgbaEqual=True, completeDocumentUnchanged=True)

    stage('native-drawing-other-theme-png')
    before_theme = state()
    theme = ev("document.documentElement.getAttribute('data-theme')", 'drawing-png-read-theme')
    _require(theme in ('light', 'dark'), 'The native editor theme is invalid.')
    ev("document.getElementById('theme-toggle').click();true", 'drawing-png-toggle-theme-button')
    wait("document.documentElement.getAttribute('data-theme')===" + json.dumps('dark' if theme == 'light' else 'light'))
    assert_preserved(before_theme, state())
    other, other_receipt = save_png('theme')
    assert_rgba_equal(main, other); assert_preserved(before_theme, state())
    ev("document.getElementById('theme-toggle').click();true", 'drawing-png-restore-theme-button')
    wait("document.documentElement.getAttribute('data-theme')===" + json.dumps(theme))
    assert_preserved(before_theme, state())
    check('native-theme-png-rgba-equality-and-document-nonmutation', otherTheme=other_receipt,
          rgbaEqual=True, originalThemeRestored=True, completeDocumentUnchanged=True)
