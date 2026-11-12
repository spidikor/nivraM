"""Offline fixture/worker transport for the reviewed PNG continuation test.

The PNG carries MOL text, not an optically recognized molecular drawing. Native
inspection/preparation receipts are captured using a temporary source store and
replayed by the JS test; no desktop, browser or inference model is exercised.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from PIL import Image, PngImagePlugin
from rdkit import Chem, rdBase

from nivram.chemistry import graph_to_mol, import_structure
from nivram.conformer import run_request
from nivram.image_session import ImageImportSession
from nivram.scene import checked_style
from nivram.worker_processes import CancellationToken, WorkerSupervisor


def controlled_scene(_graph, style):
    """No rendering claim: the integration consumes only normalized style."""
    return {"style": checked_style(style)}


def main(data):
    if data['action'] == 'fixture':
        graph = import_structure(data['smiles'])
        mol = graph_to_mol(graph)
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('MOL', Chem.MolToMolBlock(mol))
        stream = io.BytesIO()
        Image.new('RGB', (8, 8), 'white').save(stream, format='PNG', pnginfo=metadata)
        png = stream.getvalue()
        with tempfile.TemporaryDirectory(prefix='nivram-reviewed-png-test-') as directory:
            root = Path(directory)
            session = ImageImportSession(root)
            try:
                inspection = session.inspect_bytes(base64.b64encode(png).decode(), 'fixture.png', 'review_fixture_001')
                selected = [inspection['inspection']['candidates'][0]['id']]
                # Rendering requires installed system Arial and is outside this
                # graph/receipt integration. No substitute font is selected.
                with patch('nivram.scene.render_scene', side_effect=controlled_scene):
                    candidate = session.candidate('review_fixture_001', selected, {})
                    prepared = session.prepare_accept('review_fixture_001', selected, {})
                digest = hashlib.sha256(png).hexdigest()
                assert (root / (digest + '.png')).read_bytes() == png
            finally:
                session.close()
        corrected = import_structure(data.get('correctedSmiles', data['smiles']))
        return {'inspection': inspection, 'selected': selected, 'candidate': candidate,
                'prepared': prepared, 'corrected': {'graph': corrected, 'scene': controlled_scene(corrected, {})},
                'sourceSha256': digest, 'pngBase64': base64.b64encode(png).decode(),
                'originalCoordinates': [list(p)[:2] for p in mol.GetConformer().GetPositions()],
                'rdkitVersion': rdBase.rdkitVersion}
    if data['action'] == 'conformer':
        supervisor = WorkerSupervisor(max_workers=1)
        try:
            return run_request(data['payload'], supervisor, CancellationToken())
        finally:
            assert supervisor.shutdown(), 'Test-owned worker did not shut down.'
    if data['action'] == 'identity':
        # Independently parse the generated MOL and compare against the accepted
        # chemical graph, rather than trusting the result's identity label.
        mol = Chem.MolFromMolBlock(data['molText'], removeHs=False)
        assert mol is not None
        return {'source': Chem.MolToSmiles(graph_to_mol(data['graph']), isomericSmiles=True),
                'generated': Chem.MolToSmiles(mol, isomericSmiles=True),
                'coordinates': [list(p) for p in mol.GetConformer().GetPositions()]}
    raise ValueError('Unsupported test action')


if __name__ == '__main__':
    print(json.dumps(main(json.load(sys.stdin))))
