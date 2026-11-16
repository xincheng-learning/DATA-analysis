import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const filename=fileURLToPath(new URL('../src/components/table-relationship-map.tsx',import.meta.url)),compiled={exports:{}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{module:compiled,exports:compiled.exports,require:createRequire(filename)});
const {TableRelationshipMap}=compiled.exports;
const table=(name,columns,source={kind:'file'})=>({table_name:name,rows:61,columns,dtypes:Object.fromEntries(columns.map(c=>[c,'VARCHAR'])),source});
const props={tables:[table('left',['tenant','buyer_code']),table('right',['company','customer_ref']),table('independent',['humidity'])],relations:[{id:'alias',left_table:'left',right_table:'right',left_keys:['tenant','buyer_code'],right_keys:['company','customer_ref'],state:'suggested',label:'AI 候选，尚未业务确认'}],selected:'left',onSelect(){},onSelectRelation(){}};

test('composite and alias keys draw exact field pairs while isolated tables remain',()=>{
 const html=renderToStaticMarkup(React.createElement(TableRelationshipMap,props));
 assert.ok(html.includes('left.tenant → right.company')&&html.includes('left.buyer_code → right.customer_ref'));
 assert.ok(html.includes('independent')&&html.includes('humidity')&&html.includes('建议 / 待确认'));
 assert.equal((html.match(/class="schema-link-hit"/g)||[]).length,2);
 assert.ok(!html.includes('>PK<'));
});

test('only declared metadata marks physical PK and missing fields never connect to headers',()=>{
 const declared=table('left',['id'],{kind:'postgresql',relationship_metadata:{status:'declared',primary_key:['id'],foreign_keys:[]}});
 const html=renderToStaticMarkup(React.createElement(TableRelationshipMap,{...props,tables:[declared,...props.tables.slice(1)]}));
 assert.ok(html.includes('>PK<'));
 assert.equal((html.match(/class="schema-link-hit"/g)||[]).length,0);
});

test('nonadjacent columns route above the row instead of behind an unrelated table',()=>{
 const html=renderToStaticMarkup(React.createElement(TableRelationshipMap,{...props,tables:[props.tables[0],props.tables[2],props.tables[1]]}));
 const paths=[...html.matchAll(/class="schema-link-hit" d="([^"]+)"/g)].map(match=>match[1]);
 assert.equal(paths.length,2);
 assert.ok(paths.every(path=>path.includes(' H ')&&path.includes(' V ')&&!path.includes(' C ')));
});
