// Pinned actual producer reports. Transport wrappers are controlled test data.
import fs from 'node:fs';
const reports=JSON.parse(fs.readFileSync(new URL('./fixtures/property-reports.json',import.meta.url),'utf8'));
export function propertyReport(label,revision=null){const r=structuredClone(reports[label]);r.inputIdentity.documentRevision=revision;return r;}
export function propertyEnvelope(request,label){const properties=propertyReport(label,request.documentRevision);return {schema:'nivram-properties/1',requestId:request.requestId,sourceGraph:structuredClone(request.graph),sourceIdentity:structuredClone(properties.inputIdentity),properties};}
