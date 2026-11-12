"""Disposable parse-only recovery of native recorded-generated 3D coordinates.

No embedding/optimization is imported or invoked. Hashes establish consistency,
not authenticity; historical provenance is never promoted to current generation.
"""
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

MAX_TRANSPORT = 2 * 1024 * 1024
MAX_OUTPUT = 524288
ELEMENTS = {'H','B','C','N','O','F','Si','P','S','Cl','Br','I'}


def validate_request(data):
    from .png_metadata import validate_3d_coordinates
    if not isinstance(data,dict) or set(data) != {'recordText','recordSha256','clientId','jobId','expiresAt'}:
        raise ValueError('Provide an exact coordinate recovery request.')
    if not isinstance(data['recordText'],str) or len(data['recordText'].encode('utf-8')) > 1024*1024:
        raise ValueError('Recovery record exceeds its byte bound.')
    if hashlib.sha256(data['recordText'].encode('utf-8')).hexdigest() != data['recordSha256']:
        raise ValueError('Recovery record checksum mismatch.')
    if any(not isinstance(data[k],str) or not re.fullmatch('[a-f0-9]{32}',data[k]) for k in ('clientId','jobId')):
        raise ValueError('Invalid recovery identifiers.')
    if type(data['expiresAt']) is not int or not 0 < data['expiresAt'] <= 2**53-1:
        raise ValueError('Invalid recovery deadline.')
    record, provenance = validate_3d_coordinates(data['recordText'].encode('utf-8'))
    if record['recordedOrigin'] != 'generated_coordinates':
        raise ValueError('Only recorded generated V3000 coordinates use the chemistry recovery worker.')
    return record, provenance


def _v3000(text, *, allow_radicals=False):
    """Reject features RDKit might otherwise ignore, before native parsing."""
    if not isinstance(text,str) or not text.isascii() or len(text.encode()) > 262144:
        raise ValueError('Invalid bounded V3000 text.')
    lines=text.splitlines()
    if len(lines)<11 or lines[1][20:22] != '3D' or lines[3] != '  0  0  0  0  0  0  0  0  0  0999 V3000':
        raise ValueError('Expected emitted explicit-3D V3000 header.')
    if any(len(line)>512 or any(ord(c)<32 for c in line) for line in lines):
        raise ValueError('Unsupported V3000 text encoding.')
    if lines[4]!='M  V30 BEGIN CTAB' or not re.fullmatch(r'M  V30 COUNTS \d+ \d+ 0 0 0',lines[5]):
        raise ValueError('Unsupported V3000 CTAB features.')
    count,bcount=map(int,lines[5].split()[3:5])
    if not 1<=count<=128 or not 0<=bcount<=256:
        raise ValueError('Recovery supports 1–128 atoms and at most 256 bonds.')
    expected=(12+count+bcount) if bcount else (10+count)
    tail_ok=(lines[8+count]=='M  V30 BEGIN BOND' and lines[-3:]==['M  V30 END BOND','M  V30 END CTAB','M  END']) if bcount and len(lines)==expected else (not bcount and lines[-2:]==['M  V30 END CTAB','M  END'])
    if len(lines)!=expected or lines[6]!='M  V30 BEGIN ATOM' or lines[7+count]!='M  V30 END ATOM' or not tail_ok:
        raise ValueError('Unsupported V3000 sections or additional records.')
    atoms=[];bonds=[];edges=set()
    def props(fields, allowed):
        result={}
        for field in fields:
            if not re.fullmatch(r'[A-Z]+=-?\d+',field):raise ValueError('Unsupported V3000 property.')
            key,value=field.split('=');value=int(value)
            if key in result or key not in allowed or not allowed[key][0]<=value<=allowed[key][1]:raise ValueError('Unsupported V3000 property.')
            result[key]=value
        return result
    for i,line in enumerate(lines[7:7+count],1):
        fields=line.split()
        if len(fields)<8 or fields[:2]!=['M','V30'] or fields[2]!=str(i) or fields[3] not in ELEMENTS:
            raise ValueError('Unsupported V3000 atom.')
        if not all(re.fullmatch(r'-?\d+(?:\.\d+)?',v) for v in fields[4:7]):raise ValueError('Invalid coordinate spelling.')
        xyz=tuple(map(float,fields[4:7]))
        if not all(math.isfinite(v) and abs(v)<=10000 for v in xyz):raise ValueError('Coordinates exceed bounds.')
        if not re.fullmatch(r'\d+',fields[7]) or int(fields[7])>999999:raise ValueError('Invalid atom map.')
        p=props(fields[8:],{'CHG':(-8,8),'MASS':(1,400),'CFG':(1,2),'VAL':(-1,8),**({'RAD':(0,3)} if allow_radicals else {})})
        atoms.append((fields[3],xyz,int(fields[7]),p))
    for i,line in enumerate(lines[9+count:9+count+bcount],1):
        fields=line.split()
        if len(fields)<6 or fields[:2]!=['M','V30'] or fields[2]!=str(i) or fields[3] not in ('1','2','3') or not all(re.fullmatch(r'\d+',v) for v in fields[4:6]):raise ValueError('Unsupported V3000 bond.')
        a,b=map(int,fields[4:6]);edge=tuple(sorted((a,b)))
        if not 1<=a<=count or not 1<=b<=count or a==b or edge in edges:raise ValueError('Invalid V3000 bond endpoints.')
        edges.add(edge);p=props(fields[6:],{'CFG':(1,3)})
        if p and (fields[3]!='1' or p['CFG']==2):raise ValueError('Unsupported ambiguous bond stereo.')
        bonds.append((int(fields[3]),a,b,p))
    if any('RAD' in a[3] for a in atoms) and (any(set(a[3]) & {'CFG','VAL'} for a in atoms) or any(b[3] for b in bonds)):
        raise ValueError('Mixed radical and native declarations are unsupported.')
    return atoms,bonds


def recover(data):
    record,provenance=validate_request(data)
    text=record['molText'];declared_atoms,declared_bonds=_v3000(text)
    if len(record['sourceAtomIds']) != len(declared_atoms):
        raise ValueError('Source atom identifiers do not match the MOL atom count.')
    from rdkit import Chem,rdBase
    with rdBase.BlockLogs():
        mol=Chem.MolFromMolBlock(text,sanitize=True,removeHs=False,strictParsing=True)
        if mol is None or mol.GetNumConformers()!=1 or not mol.GetConformer().Is3D() or mol.GetNumAtoms()!=len(declared_atoms) or mol.GetNumBonds()!=len(declared_bonds) or len(Chem.GetMolFrags(mol))!=1:
            raise ValueError('Recovered molecule violates the supported generated subset.')
        if mol.GetStereoGroups() or any(a.HasQuery() or a.GetNumRadicalElectrons() or a.GetSymbol() not in ELEMENTS for a in mol.GetAtoms()) or any(b.HasQuery() for b in mol.GetBonds()):
            raise ValueError('Unsupported recovered molecular features.')
        if any(str(s.specified)=='Unspecified' for s in Chem.FindPotentialStereo(mol)):
            raise ValueError('Recovered coordinates contain unspecified potential stereochemistry.')
        identity=Chem.MolToSmiles(mol,isomericSmiles=True)
        if identity!=provenance['canonicalIsomericSmiles']:
            raise ValueError('Recovered chemical identity does not match the recorded claim.')
        # RDKit gives 3D coordinates priority over CFG. Re-serialization must
        # preserve every declaration; contradictory CFG cannot silently disappear.
        check_atoms,check_bonds=_v3000(Chem.MolToMolBlock(mol,includeStereo=True,forceV3000=True))
        if [(a[0],a[2],a[3]) for a in declared_atoms]!=[(a[0],a[2],a[3]) for a in check_atoms] or declared_bonds!=check_bonds:
            raise ValueError('MOL declarations or stereochemistry conflict with parsed 3D geometry.')
        atoms=[]
        for i,a in enumerate(mol.GetAtoms()):
            p=mol.GetConformer().GetAtomPosition(i);xyz=(p.x,p.y,p.z);d=declared_atoms[i]
            if xyz!=d[1] or (a.GetSymbol(),a.GetAtomMapNum(),a.GetFormalCharge(),a.GetIsotope())!=(d[0],d[2],d[3].get('CHG',0),d[3].get('MASS',0)):
                raise ValueError('Parsed coordinates or atom declarations changed.')
            atoms.append(dict(id=i+1,sourceId=record['sourceAtomIds'][i],element=a.GetSymbol(),charge=a.GetFormalCharge(),isotope=a.GetIsotope(),x=p.x,y=p.y,z=p.z))
        bonds=[dict(id=i+1,a=b.GetBeginAtomIdx()+1,b=b.GetEndAtomIdx()+1,order=4 if b.GetIsAromatic() else int(b.GetBondTypeAsDouble())) for i,b in enumerate(mol.GetBonds())]
    attachment = None
    if record['schema'] == 'nivram-png-coordinates/6':
        from .generated_hydrogens import validate_topology
        from .png_metadata import _json
        attachment = validate_topology(_json(record['generatedHydrogensJson']), mol, text)
    scene = dict(kind='recovered_coordinates',sourceText=text,atoms=atoms,bonds=bonds)
    if attachment is not None:
        scene['generatedHydrogens'] = attachment
    validation=dict(schema='nivram-3d-recovery-validation/1',engine='RDKit',engineVersion=rdBase.rdkitVersion,canonicalIsomericSmiles=identity,chemicalIdentityConsistent=True,sourceBindingReverified=False,generationClaimsVerified=False)
    if attachment is not None:
        validation.update(schema='nivram-3d-recovery-validation/2',generatedHydrogensSha256=record['generatedHydrogensSha256'],generatedHydrogenTopologyConsistent=True,hydrogenGeometryAuthenticated=False)
    return dict(recordSha256=data['recordSha256'],molSha256=record['molSha256'],jobId=data['jobId'],scene=scene,validation=validation)


def worker_command():
    if getattr(sys,'frozen',False):return [str(Path(sys.executable).with_name('nivram-worker.exe')),'--coordinate-recovery']
    return [sys.executable,'-B','-s','-m','nivram.coordinate_recovery','--worker']


def run_request(data,supervisor,token):
    record,_=validate_request(data)
    timeout=min(15.0,(data['expiresAt']-time.time()*1000)/1000)
    if timeout<=0:raise ValueError('Coordinate recovery request expired.')
    try:
        completed=supervisor.run(worker_command(),input_text=json.dumps(data,ensure_ascii=False,allow_nan=False),timeout_seconds=timeout,cancellation_token=token,max_input_bytes=MAX_TRANSPORT,max_stdout_bytes=MAX_OUTPUT,max_stderr_bytes=8192)
    except subprocess.TimeoutExpired:raise ValueError('Coordinate recovery exceeded its 15-second bound.') from None
    supervisor.check_cancelled(token)
    from .png_metadata import _json
    result=_json(completed.stdout)
    if not isinstance(result,dict):raise ValueError('Invalid coordinate recovery worker response.')
    if completed.returncode!=0:raise ValueError(result.get('error','Coordinate recovery worker failed.'))
    if result.get('recordSha256')!=data['recordSha256'] or result.get('jobId')!=data['jobId'] or result.get('molSha256')!=record['molSha256']:
        raise ValueError('Stale or inconsistent coordinate recovery response.')
    return result


def worker_main():
    try:
        if os.name=='nt':
            from .worker_resource_limits import install_worker_limits
            install_worker_limits()
        else:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU,(10,10));resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
        raw=sys.stdin.buffer.read(MAX_TRANSPORT+1)
        if len(raw)>MAX_TRANSPORT:raise ValueError('Recovery transport exceeds its bound.')
        from .png_metadata import _json
        result=recover(_json(raw.decode('utf-8')));code=0
    except Exception as exc:
        result={'error':str(exc) if isinstance(exc,ValueError) else 'Coordinate recovery failed before validated output.'};code=2
    sys.stdout.write(json.dumps(result,ensure_ascii=False,allow_nan=False));sys.stdout.flush()
    return code

if __name__=='__main__':raise SystemExit(worker_main() if sys.argv[1:]==['--worker'] else 2)
