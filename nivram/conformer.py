"""Isolated bounded local conformer generation. Never edits a document."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

MAX_INPUT = 262144
MAX_OUTPUT = 524288
MAX_TRANSPORT = 2*1024*1024
SEED = 61453

def validate_request(data):
    if not isinstance(data, dict) or set(data) != {'graphText','sourceRevision','sourceHash','sourceContextText','sourceContextHash','clientId','jobId','expiresAt'}:
        raise ValueError('Provide an exact copied-graph conformer request.')
    if type(data['sourceRevision']) is not int or not 0 <= data['sourceRevision'] < 2**53:
        raise ValueError('Invalid source revision.')
    if not isinstance(data['graphText'],str) or len(data['graphText'].encode('utf-8')) > MAX_INPUT:
        raise ValueError('The copied graph exceeds the input bound.')
    if hashlib.sha256(data['graphText'].encode('utf-8')).hexdigest() != data['sourceHash']:
        raise ValueError('The copied graph hash does not match.')
    from .conformer_context import checked_context
    checked_context(data['sourceContextText'],data['sourceContextHash'])
    from .population_jobs import _json
    graph = _json(data['graphText'])
    if not isinstance(graph,dict) or not isinstance(graph.get('atoms'),list) or not isinstance(graph.get('bonds'),list) or not 1 <= len(graph['atoms']) <= 128 or len(graph['bonds']) > 256:
        raise ValueError('Generation supports 1–128 source atoms and at most 256 source bonds.')
    return graph

def generate(data):
    graph = validate_request(data)
    from rdkit import Chem, rdBase
    from rdkit.Chem import rdDistGeom
    from .chemistry import graph_to_mol
    with rdBase.BlockLogs():
        mol = graph_to_mol(graph)
        if len(Chem.GetMolFrags(mol)) != 1 or any(a.GetSymbol() not in {'H','B','C','N','O','F','Si','P','S','Cl','Br','I'} or a.GetNumRadicalElectrons() for a in mol.GetAtoms()):
            raise ValueError('Generation supports one connected, nonradical organic structure in the stated element subset.')
        # Coordinate assignment would otherwise choose an unspecified stereoisomer.
        # Fail closed until a representation preserving that ambiguity is available.
        from .stereo_guidance import unresolved_diagnostic, UnresolvedStereoError
        denial = unresolved_diagnostic(mol, graph, Chem.FindPotentialStereo(mol))
        if denial is not None:
            raise UnresolvedStereoError(denial)
        identity = Chem.MolToSmiles(mol, isomericSmiles=True)
        source_count = mol.GetNumAtoms()
        explicit = Chem.AddHs(mol)
        if explicit.GetNumAtoms() > 256 or explicit.GetNumBonds() > 512:
            raise ValueError('Hydrogen addition exceeds the 256-atom/512-bond generation bound.')
        explicit.RemoveAllConformers()
        params = rdDistGeom.ETKDGv3()
        params.randomSeed = SEED
        params.numThreads = 1
        params.maxIterations = 100
        params.enforceChirality = True
        if rdDistGeom.EmbedMolecule(explicit, params) < 0:
            raise ValueError('Bounded ETKDG embedding failed; the drawing is unchanged.')
        # Canonical MOL retains source atoms and declarations; added H coordinates
        # are preserved separately for display without ambiguous H removal.
        result = Chem.Mol(mol)
        result.RemoveAllConformers()
        conf = Chem.Conformer(source_count); conf.Set3D(True)
        for i in range(source_count):
            conf.SetAtomPosition(i, explicit.GetConformer().GetAtomPosition(i))
        result.AddConformer(conf)
        block = Chem.MolToMolBlock(result, includeStereo=True, forceV3000=True)
        recovered = Chem.MolFromMolBlock(block, sanitize=True, removeHs=False, strictParsing=True)
        if recovered is None or Chem.MolToSmiles(recovered,isomericSmiles=True) != identity:
            raise ValueError('Generated MOL readback changed identity or stereochemistry; no result is published.')
        # Verify coordinates, atom order, isotope, formal charge and mapping on
        # actual exported readback, rather than the pre-serialization molecule.
        atoms=[]
        for i,(a,b) in enumerate(zip(result.GetAtoms(),recovered.GetAtoms())):
            if (a.GetAtomicNum(),a.GetIsotope(),a.GetFormalCharge(),a.GetAtomMapNum()) != (b.GetAtomicNum(),b.GetIsotope(),b.GetFormalCharge(),b.GetAtomMapNum()):
                raise ValueError('Generated atom identity readback failed.')
            p=recovered.GetConformer().GetAtomPosition(i)
            if not all(math.isfinite(v) and abs(v)<=10000 for v in (p.x,p.y,p.z)):
                raise ValueError('Generated coordinates exceed viewer bounds.')
            atoms.append({'id':i+1,'sourceId':graph['atoms'][i]['id'],'element':b.GetSymbol(),'charge':b.GetFormalCharge(),'isotope':b.GetIsotope(),'x':p.x,'y':p.y,'z':p.z})
        bonds=[{'id':i+1,'a':b.GetBeginAtomIdx()+1,'b':b.GetEndAtomIdx()+1,'order':4 if b.GetIsAromatic() else int(b.GetBondTypeAsDouble())} for i,b in enumerate(recovered.GetBonds())]
    provenance={'schema':'nivram-generated-3d/1','origin':'generated_coordinates','generated':True,'method':'RDKit ETKDGv3','engineVersion':rdBase.rdkitVersion,'seed':SEED,'maxIterations':100,'threads':1,'hydrogens':'Added during embedding; output retains source atoms and hydrogen declarations','minimization':'none','sourceRevision':data['sourceRevision'],'sourceSha256':data['sourceHash'],'outputSha256':hashlib.sha256(block.encode()).hexdigest(),'format':'MOL V3000','canonicalIsomericSmiles':identity,'chemicalIdentityRoundtripVerified':True,'experimentalStructureClaim':False,'limitations':['One local generated conformer, not an experimental structure, bound pose, global minimum or aqueous population.','Unspecified potential stereochemistry is rejected, never silently assigned.','Source PNG recognition/review provenance remains in the unchanged 2D document; generation does not verify image correspondence.']}
    from .conformer_context import checked_context
    provenance.update(sourceContext=checked_context(data['sourceContextText'],data['sourceContextHash']),sourceContextSha256=data['sourceContextHash'])
    from .generated_hydrogens import from_embedding
    attachment = from_embedding(explicit, source_count, recovered, block)
    return {'sourceContextHash':data['sourceContextHash'],'sourceHash':data['sourceHash'],'sourceRevision':data['sourceRevision'],'scene':{'kind':'generated_coordinates','atoms':atoms,'bonds':bonds,'sourceText':block,'generatedHydrogens':attachment},'provenance':provenance}

def worker_command():
    if getattr(sys,'frozen',False):
        return [str(Path(sys.executable).with_name('nivram-worker.exe')),'--conformer']
    return [sys.executable,'-B','-s','-m','nivram.conformer','--worker']

def run_request(data, supervisor, token):
    graph = validate_request(data)
    timeout=min(15.0,(data['expiresAt']-time.time()*1000)/1000)
    if timeout<=0: raise ValueError('Conformer request expired.')
    try:
        completed=supervisor.run(worker_command(),input_text=json.dumps(data,ensure_ascii=False,allow_nan=False),timeout_seconds=timeout,cancellation_token=token,max_input_bytes=MAX_TRANSPORT,max_stdout_bytes=MAX_OUTPUT,max_stderr_bytes=8192)
    except subprocess.TimeoutExpired:
        raise ValueError('Conformer generation exceeded its 15-second bound.') from None
    supervisor.check_cancelled(token)
    result=json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise ValueError('Invalid conformer worker response.')
    if completed.returncode != 0: raise ValueError(result.get('error','Conformer worker failed.'))
    if result.get('sourceContextHash')!=data['sourceContextHash'] or result.get('sourceHash')!=data['sourceHash'] or result.get('sourceRevision')!=data['sourceRevision']:
        raise ValueError('Stale or inconsistent conformer response.')
    if 'denial' in result:
        from .stereo_guidance import validate_denial
        if set(result) != {'denial', 'sourceHash', 'sourceRevision', 'sourceContextHash'}:
            raise ValueError('Invalid stereochemistry diagnostic envelope.')
        validate_denial(result['denial'], graph)
    return result

def worker_main():
    from .stereo_guidance import UnresolvedStereoError
    data = None
    try:
        # Apply before chemistry imports. Windows uses the existing fail-closed
        # job-object mechanism; Linux uses per-process CPU/address-space limits.
        if os.name=='nt':
            from .worker_resource_limits import install_worker_limits
            install_worker_limits()
        else:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU,(10,10))
            resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
        raw=sys.stdin.buffer.read(MAX_TRANSPORT+1)
        if len(raw)>MAX_TRANSPORT: raise ValueError('Conformer input exceeds its byte bound.')
        from .population_jobs import _json
        data = _json(raw.decode('utf-8'))
        result=generate(data)
        code=0
    except UnresolvedStereoError as exc:
        result = {'denial': exc.denial, 'sourceHash': data['sourceHash'],
                  'sourceRevision': data['sourceRevision'], 'sourceContextHash': data['sourceContextHash']}
        code = 0
    except Exception as exc:
        result={'error':str(exc) if isinstance(exc,ValueError) else 'Conformer worker failed before validated output.'};code=2
    sys.stdout.write(json.dumps(result,ensure_ascii=False,allow_nan=False));sys.stdout.flush()
    return code

if __name__=='__main__':
    raise SystemExit(worker_main() if sys.argv[1:]==['--worker'] else 2)
