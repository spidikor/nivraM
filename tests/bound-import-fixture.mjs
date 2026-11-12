// Synthetic UI transport only: not native parsing, chemistry, or source proof.
import {createHash} from 'node:crypto';
import {boundRenderFixture} from './bound-render-fixture.mjs';
export function boundImportFixture(request,result){
 if(request.sourceBinding!=='nivram-import/1'||typeof request.source!=='string')throw new Error('Fixture expected a bound import request.');
 const mock=boundRenderFixture('render',{sourceBinding:'nivram-render/1',requestId:request.requestId,documentRevision:request.documentRevision,graph:result.graph,style:request.style},result);
 return {schema:'nivram-import/1',action:'import',requestId:request.requestId,documentRevision:request.documentRevision,source:{sha256:createHash('sha256').update(request.source,'utf8').digest('hex'),bytes:Buffer.byteLength(request.source,'utf8')},style:mock.scene.style,outputIdentity:mock.outputIdentity,outputGraphJson:mock.outputGraphJson,graph:mock.graph,scene:mock.scene};
}
