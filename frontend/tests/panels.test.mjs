import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const filename=fileURLToPath(new URL('../src/components/analysis-panels.tsx',import.meta.url));
const compiled={exports:{}};
const mocks={react:{...React,useState:value=>[value,()=>{}],useRef:()=>({current:null}),useCallback:value=>value,useLayoutEffect(){},useEffect(){}},'./chart-view':{ChartView:()=>null},'./panel-picker':{PanelPicker:()=>null},'@/lib/boards':{snap:value=>Math.max(0,Math.round(value/8)*8)}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{module:compiled,exports:compiled.exports,require:name=>mocks[name]||createRequire(filename)(name),setTimeout:()=>0});
const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
const pointer=(x,y)=>({button:0,pointerId:1,clientX:x,clientY:y,preventDefault(){},stopPropagation(){},target:{closest(){return null;}},currentTarget:{setPointerCapture(){}}});
const item={id:'note',kind:'text',title:'笔记',text:'',placed:true,x:80,y:80,width:220,height:120};

test('a quick pointer release uses its final coordinates even before the preview renders',()=>{
  let changed;
  const tree=elements(compiled.exports.CanvasFrame({item,zoom:.5,onChange:value=>changed=value,onRemove(){},onReturnDrop:()=>false,onDragPosition(){}}));
  const handle=tree.find(node=>node.props?.className==='board-frame-handle');
  handle.props.onPointerDown(pointer(10,10));handle.props.onPointerUp(pointer(110,60));
  assert.equal(changed.x,280);assert.equal(changed.y,184);
});

test('returning a chart to materials does not save an off-canvas position',()=>{
  let changed=false,returned=false;
  const tree=elements(compiled.exports.CanvasFrame({item:{...item,kind:'chart'},zoom:1,onChange:()=>changed=true,onRemove(){},onReturnDrop:()=>{returned=true;return true;},onDragPosition(){}}));
  const handle=tree.find(node=>node.props?.className==='board-frame-handle');
  handle.props.onPointerDown(pointer(500,200));handle.props.onPointerUp(pointer(200,200));
  assert.equal(returned,true);assert.equal(changed,false);
});

test('text notes can resize to 160 by 80 without the chart minimum',()=>{
  let changed;
  const tree=elements(compiled.exports.CanvasFrame({item,zoom:1,onChange:value=>changed=value,onRemove(){},onReturnDrop:()=>false,onDragPosition(){}}));
  const handle=tree.find(node=>node.props?.className==='board-resize-handle');
  handle.props.onPointerDown(pointer(100,100));handle.props.onPointerUp(pointer(0,0));
  assert.equal(changed.width,160);assert.equal(changed.height,80);
});

function frameHarness(){
  const hooks=[],effects=[];let index=0;
  const react={...React,useState:initial=>{const key=index++;hooks[key]??={value:initial};return [hooks[key].value,value=>{hooks[key].value=typeof value==='function'?value(hooks[key].value):value;}];},useRef:initial=>{const key=index++;hooks[key]??={current:initial};return hooks[key];},useCallback:value=>value,useEffect(){},useLayoutEffect:effect=>effects.push(effect)};
  const output={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{module:output,exports:output.exports,require:name=>name==='react'?react:mocks[name]||createRequire(filename)(name),window:{matchMedia:()=>({matches:false})},setTimeout:()=>0});
  return {effects,render:props=>{index=0;return elements(output.exports.CanvasFrame(props));}};
}

test('an existing canvas item follows the pointer at full size without a landing animation',()=>{
  const harness=frameHarness(),props={item,zoom:1,onChange(){},onRemove(){},onReturnDrop:()=>false,onDragPosition(){}};
  let tree=harness.render(props),handle=tree.find(node=>node.props?.className==='board-frame-handle');
  assert.doesNotMatch(tree[0].props.className,/is-entering|is-settling/);
  handle.props.onPointerDown(pointer(100,100));handle.props.onPointerMove(pointer(180,140));
  tree=harness.render(props);
  assert.equal(tree[0].props.style.transform,'translate3d(160px,120px,0)');
  assert.equal(tree[0].props.style.width,220);
  assert.doesNotMatch(tree[0].props.className,/is-card-dragging/);
});

test('a material landing starts at the floating card bounds and stays covered until the chart is drawn',()=>{
  const harness=frameHarness(),props={item:{...item,kind:'chart',width:600,height:400,spec:{data:[{type:'bar',y:[10]}]}},zoom:.5,arrival:{x:400,y:200,width:210,height:145},onChange(){},onRemove(){},onReturnDrop:()=>false,onDragPosition(){}};
  let tree=harness.render(props),animation,keyframes;
  tree[0].props.ref.current={offsetWidth:600,style:tree[0].props.style,getBoundingClientRect:()=>({left:380,top:180,width:300,height:200}),animate:frames=>{keyframes=frames;animation={cancel(){}};return animation;}};
  harness.effects.forEach(effect=>effect());
  assert.equal(keyframes[0].transform,'translate3d(80px,80px,0) translate3d(40px,40px,0) scale(0.7,0.725)');
  assert.equal(keyframes[1].transform,'translate3d(80px,80px,0)');
  assert.equal(keyframes[0].opacity,undefined,'do not flash the entire card from transparent');
  animation.onfinish();tree=harness.render(props);
  assert.doesNotMatch(tree.find(node=>node.props?.className?.includes('panel-material-landing')).props.className,/is-revealed/);
  tree.find(node=>node.props?.spec===props.item.spec).props.onReady();tree=harness.render(props);
  assert.match(tree.find(node=>node.props?.className?.includes('panel-material-landing')).props.className,/is-revealed/);
});

test('switching boards protects the previous draft while the new board is loading',async()=>{
  let cursor=0,release;const hooks=[],saved=[];const compiled={exports:{}};
  const initial={id:'a',title:'旧面板',version:1,items:[]};
  const react={...React,useState:initial=>{const i=cursor++;hooks[i]??={value:initial};return[hooks[i].value,value=>hooks[i].value=typeof value==='function'?value(hooks[i].value):value];},useRef:initial=>{const i=cursor++;return hooks[i]??={current:initial};},useCallback:fn=>fn,useEffect(){},useLayoutEffect(){}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{
    module:compiled,exports:compiled.exports,setTimeout:()=>1,clearTimeout(){},require:name=>name==='react'?react:name==='@/lib/boards'?{snap:v=>v,readBoard:async id=>id==='a'?initial:new Promise(resolve=>release=resolve),saveBoard:async board=>{saved.push(structuredClone(board));return{...board,version:board.version+1};}}:mocks[name]||createRequire(filename)(name),
  });
  const render=()=>{cursor=0;return elements(compiled.exports.AnalysisPanels({onClose(){},onAnalyse(){}}));};
  await render().find(node=>node.props?.onPick).props.onPick({id:'a'});
  render().find(node=>node.props?.['aria-label']==='面板名称').props.onChange({target:{value:'已编辑草稿'}});
  const loading=render().find(node=>node.props?.onPick).props.onPick({id:'b'});await new Promise(setImmediate);
  assert.equal(saved[0].title,'已编辑草稿');
  assert.equal(render().some(node=>node.props?.['aria-label']==='面板名称'),false,'a pending board read must not allow edits to be overwritten');
  release({id:'b',title:'新面板',version:1,items:[]});await loading;
  assert.equal(render().find(node=>node.props?.['aria-label']==='面板名称').props.value,'新面板');
});
