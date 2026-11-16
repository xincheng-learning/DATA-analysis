import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';

function streamClient(chunks,status=200){
  const compiled={exports:{}},bytes=new TextEncoder().encode(chunks);
  const body=new ReadableStream({start(controller){for(const value of bytes)controller.enqueue(Uint8Array.of(value));controller.close();}});
  const source=ts.transpileModule(fs.readFileSync(new URL('../src/lib/api.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  vm.runInNewContext(source,{exports:compiled.exports,module:compiled,require:()=>({supabase:null}),process:{env:{}},TextDecoder,
    fetch:async()=>new Response(status===200?body:JSON.stringify({detail:'任务不存在'}),{status,headers:{'content-type':status===200?'text/event-stream':'application/json'}})});
  return compiled.exports.streamAnalysis;
}
const task=(status,draft_answer)=>({task_id:'live',status,draft_answer});
const frame=value=>`event: task\r\ndata: ${JSON.stringify(value)}\r\n\r\n`;

test('SSE preserves Chinese across byte boundaries, heartbeats and ordered snapshots',async()=>{
  const updates=[],stream=streamClient(': keep-alive\r\n\r\n'+frame(task('running','第一段'))+frame(task('running','第一段，第二段'))+frame(task('succeeded','第一段，第二段')));
  await stream('live',value=>updates.push(value),new AbortController().signal);
  assert.deepEqual(updates.map(value=>value.draft_answer),['第一段','第一段，第二段','第一段，第二段']);
});
test('an early disconnect preserves received text and reports interruption for recovery',async()=>{
  const updates=[];
  await assert.rejects(streamClient(frame(task('running','已收到内容')))('live',value=>updates.push(value),new AbortController().signal),/提前结束/);
  assert.equal(updates[0].draft_answer,'已收到内容');
  await streamClient(frame(task('succeeded','已收到内容及后续内容')))('live',value=>updates.push(value),new AbortController().signal);
  assert.equal(updates.at(-1).draft_answer,'已收到内容及后续内容');
});
test('SSE rejects another task and preserves HTTP errors',async()=>{
  await assert.rejects(streamClient(frame({...task('succeeded','wrong'),task_id:'other'}))('live',()=>assert.fail('wrong owner/task'),new AbortController().signal),/无效/);
  await assert.rejects(streamClient('',404)('live',()=>{},new AbortController().signal),error=>error.status===404);
});
