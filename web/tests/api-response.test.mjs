import assert from 'node:assert/strict';
import {readApiResponse} from '../src/api-response.js';
await assert.rejects(readApiResponse(new Response('Internal Server Error',{status:500})),error=>error.message.includes('HTTP 500')&&!error.message.includes('Unexpected token'));
await assert.rejects(readApiResponse(new Response('<html>bad gateway</html>',{status:502})),/HTTP 502/);
await assert.rejects(readApiResponse(new Response('not JSON',{status:200})),/予期しない形式/);
await assert.rejects(readApiResponse(Response.json({error:{code:'SCHEMA_MISMATCH',message:'Unsupported issue shape'}},{status:409})),/Unsupported issue shape/);
assert.deepEqual(await readApiResponse(Response.json({issues:[]})),{issues:[]});
console.log('5 API response scenarios passed');
