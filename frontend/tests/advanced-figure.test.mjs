import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {test} from 'node:test';
function load(file,dependencies={}){
  const compiled={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../src/lib/'+file,import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2017,module:ts.ModuleKind.CommonJS}}).outputText,{module:compiled,exports:compiled.exports,require:name=>dependencies[name]});
  return compiled.exports;
}
const analysis=load('analysis.ts'),{advancedFigure}=load('advanced-figure.ts',{'./analysis':analysis});
const view={title:'电商流向',chart_type:'sankey',dimensions:['state','category'],measure:'amount',row_count:201};
const rows=Array.from({length:201},(_,i)=>({state:'州'+i%23,category:'类'+i,amount:(i+.25).toFixed(2)}));
test('flow keeps all 201 verified links and exact hover values',()=>{
  const trace=advancedFigure(view,rows).data[0];
  assert.equal(trace.link.value.length,201);
  assert.equal(trace.link.value.reduce((a,b)=>a+b,0),201*200/2+201*.25);
  assert.equal(trace.link.customdata[200][2],'200.25');
  assert.throws(()=>advancedFigure(view,rows.slice(0,50)),/不完整/);
  assert.throws(()=>advancedFigure(view,[...rows.slice(0,200),{...rows[200],amount:null}]),/未知/);
});
test('hierarchy uses full paths for repeated labels and reconciles every parent',()=>{
  const data=[{a:'A',b:'same',amount:'12.30'},{a:'B',b:'same',amount:'7.70'},{a:'A',b:'other',amount:'1.00'}];
  const trace=advancedFigure({...view,chart_type:'treemap',dimensions:['a','b'],row_count:3},data).data[0];
  assert.equal(trace.ids.length,6);
  assert.equal(trace.ids.filter((id,i)=>trace.labels[i]==='same').length,2);
  assert.equal(trace.values[0],21);
  trace.ids.forEach((id,i)=>{
    const children=trace.values.filter((_,j)=>trace.parents[j]===id);
    if(children.length)assert.ok(Math.abs(children.reduce((a,b)=>a+b,0)-trace.values[i])<1e-10);
  });
  assert.equal(trace.customdata.find(([path])=>path==='A › same')[1],'12.30');
});
test('pivot preserves unknowns and zero without inventing a total',()=>{
  const data=[{state:'A',amount:null},{state:'B',amount:0}];
  assert.equal(advancedFigure({...view,chart_type:'pivot_table',row_count:2},data),null);
  assert.equal(data[0].amount,null);assert.equal(data[1].amount,0);
  assert.equal(advancedFigure({...view,row_count:1},[{state:'A',category:'B',amount:0}]),null);
});
