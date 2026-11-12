"""Local test transport and independent RDKit Cartesian measurement oracle."""
import json
import sys

from rdkit import Chem, Geometry, rdBase
from rdkit.Chem import rdMolTransforms


def main(data):
    action = data['action']
    if action == 'fixture':
        from tests.test_conformer import request
        return request('CCO')
    if action in ('conformer', 'coordinate-recovery'):
        from nivram.worker_processes import CancellationToken, WorkerSupervisor
        if action == 'conformer':
            from nivram.conformer import run_request
        else:
            from nivram.coordinate_recovery import run_request
        supervisor = WorkerSupervisor(max_workers=1)
        try:
            return run_request(data['payload'], supervisor, CancellationToken())
        finally:
            supervisor.shutdown()
    assert action == 'oracle'
    scene = data['scene']
    # Construct this coordinate list independently of generatedDisplayModel and
    # the JavaScript measurement helper. Hydrogen attachments follow source atoms.
    atoms = list(scene['atoms']) + list(scene['generatedHydrogens']['hydrogens'])
    conformer = Chem.Conformer(len(atoms))
    for index, atom in enumerate(atoms):
        conformer.SetAtomPosition(index, Geometry.Point3D(atom['x'], atom['y'], atom['z']))
    results = []
    for ids in data['selections']:
        indices = [atom_id - 1 for atom_id in ids]
        method = {2: rdMolTransforms.GetBondLength,
                  3: rdMolTransforms.GetAngleDeg,
                  4: rdMolTransforms.GetDihedralDeg}[len(ids)]
        results.append({'ids': ids, 'value': method(conformer, *indices)})
    return {'engine': 'RDKit', 'version': rdBase.rdkitVersion, 'results': results}


if __name__ == '__main__':
    print(json.dumps(main(json.load(sys.stdin))))
