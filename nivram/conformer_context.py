"""Strict bounded transport for safe historical image provenance, not raw documents."""
import hashlib
import math
import re
from .population_jobs import _json
MAX_CONTEXT_BYTES = 49152
SCHEMA = 'nivram-3d-source-context/1'
RELATIONSHIP = 'unverified; prior image review does not verify the current graph'
KINDS = {'png-user-supplied-structure','png-optical-reconstruction','png-metadata-component-reconstruction'}

def checked_context(text, digest):
    def need(condition):
        if not condition: raise ValueError('Invalid or excessive safe source context.')
    def string(value, maximum):
        need(isinstance(value,str) and len(value.encode('utf-16-le'))//2<=maximum and not re.search(r'[\x00-\x1f\x7f]',value))
    def sha(value):need(isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None)
    def integer(value,minimum=1):need(type(value) in (int,float) and math.isfinite(value) and value==int(value) and minimum<=value<=32767)
    def fields(row,allowed):need(isinstance(row,dict) and not set(row)-set(allowed))
    need(isinstance(text,str));raw=text.encode('utf-8');need(len(raw)<=MAX_CONTEXT_BYTES)
    sha(digest);need(hashlib.sha256(raw).hexdigest()==digest)
    data=_json(text)
    if data is None:return None
    need(isinstance(data,dict) and set(data)=={'schema','currentGraphRelationship','imageImport'})
    need(data['schema']==SCHEMA and data['currentGraphRelationship']==RELATIONSHIP)
    image=data['imageImport'];fields(image,{'kind','sourceSha256','warnings','imageCorrespondence','recognition','reviewedRegion','correction'})
    need({'kind','sourceSha256'}<=set(image));need(image['kind'] in KINDS);sha(image['sourceSha256'])
    if 'warnings' in image:
        need(isinstance(image['warnings'],list) and len(image['warnings'])<=32)
        for item in image['warnings']:string(item,1024)
    if 'imageCorrespondence' in image:string(image['imageCorrespondence'],256)
    if 'recognition' in image:
        row=image['recognition'];limits={'method':64,'confidenceMeaning':128,'coordinates':256,'status':32}
        fields(row,set(limits)|{'modelSha256','inputSha256','confidence'})
        for key,value in row.items():
            if key in limits:string(value,limits[key])
            elif key in ('modelSha256','inputSha256'):sha(value)
            else:need(value is None or type(value) in (int,float) and 0<=value<=1)
    if 'reviewedRegion' in image:
        row=image['reviewedRegion'];limits={'kind':32,'scale':64,'metadataCorrespondence':256}
        fields(row,set(limits)|{'sourceSha256','pngSha256','sourceWidth','sourceHeight','width','height','rect'})
        for key,value in row.items():
            if key in limits:string(value,limits[key])
            elif key.endswith('Sha256'):sha(value)
            elif key=='rect':
                need(isinstance(value,dict) and set(value)=={'x','y','width','height'})
                for k,v in value.items():integer(v,0 if k in ('x','y') else 1)
            else:integer(value)
    if 'correction' in image:
        row=image['correction'];fields(row,{'kind','imageCorrespondence'})
        if 'kind' in row:need(row['kind']=='user-edited-graph')
        if 'imageCorrespondence' in row:string(row['imageCorrespondence'],256)
    return data
