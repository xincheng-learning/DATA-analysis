import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const filename=fileURLToPath(new URL('../src/components/question-navigator.tsx',import.meta.url));
const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
function load(){
  const output={exports:{}},menu={current:{open:true,contains:()=>false}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{exports:output.exports,module:output,require:name=>name==='react'?{...React,useRef:()=>menu,useEffect(){}}:createRequire(filename)(name)});
  return {...output.exports,menu};
}

test('question navigation skips uploads and preserves original message indexes',()=>{
  const {QuestionNavigator,menu}=load();let selected,latest=0;
  const tree=elements(QuestionNavigator({messages:[{role:'user',content:'上传成功',metadata:{type:'upload'}},{role:'assistant',content:'答复'},{role:'user',content:'  为什么下降？\n请解释  '},{role:'assistant',content:'原因'},{role:'user',content:'再看地区差异'}],onJump:index=>selected=index,onLatest:()=>latest++}));
  const questions=tree.filter(node=>node.type==='button'&&node.props['data-question-index']!==undefined);
  assert.equal(questions.length,2);
  assert.equal(questions[0].props['data-question-index'],2);
  assert.equal(questions[0].props.title,'为什么下降？ 请解释');
  questions[1].props.onClick();assert.equal(selected,4);assert.equal(menu.current.open,false);
  tree.find(node=>node.props?.['aria-label']==='回到最新消息').props.onClick();assert.equal(latest,1);
});

test('an empty or upload-only chat does not show an empty directory',()=>{
  const {QuestionNavigator}=load();
  assert.equal(QuestionNavigator({messages:[{role:'user',content:'文件.csv',metadata:{type:'upload'}}],onJump(){},onLatest(){}}),null);
});
