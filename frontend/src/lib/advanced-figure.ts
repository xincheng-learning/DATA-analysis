import {businessLabel, type RecordValue} from './analysis';
import type {AdvancedView} from './api';

export const advancedColors=['#3B8D80','#718FBA','#C49A59','#9B86B3','#698F9A','#BD8277','#8D9C69','#958B7D'];
const label=(value:unknown)=>value==null?'空值':String(value);

/** Only consumes the full, verified Superset readback, never the 50-row chat preview. */
export function advancedFigure(view:AdvancedView, rows:RecordValue[]):RecordValue|null {
  if(rows.length!==view.row_count)throw new Error('高级视图结果不完整，未绘制部分数据。');
  if(view.chart_type==='pivot_table')return null;
  const weights=rows.map(row=>{
    const raw=row[view.measure];
    const value=typeof raw==='number'||typeof raw==='string'&&raw.trim()!==''?Number(raw):NaN;
    if(!Number.isFinite(value)||value<0)throw new Error('图形权重含未知或无效数值，请使用保留空值的透视表。');
    return value;
  });
  if(weights.every(value=>value===0))return null;
  const layout={title:{text:view.title},meta:{verified_rows:rows.length,measure:view.measure}};
  if(view.chart_type==='sankey'){
    const nodes=Array.from(new Set(rows.flatMap(row=>view.dimensions.map(dim=>label(row[dim]))))).sort();
    const index=new Map(nodes.map((name,i)=>[name,i]));
    const colors=nodes.map((_,i)=>advancedColors[i%advancedColors.length]);
    const stages=view.dimensions.map(dim=>{
      const totals=new Map<string,number>();rows.forEach((row,i)=>totals.set(label(row[dim]),(totals.get(label(row[dim]))||0)+weights[i]));
      return [...totals].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]));
    });
    const positions=new Map<string,{x:number;y:number}>();
    if(stages[0].every(([name])=>!stages[1].some(([target])=>target===name))){
      const total=weights.reduce((a,b)=>a+b,0),gap=Math.min(.018,.45/Math.max(...stages.map(stage=>stage.length)));
      stages.forEach((stage,column)=>{let offset=0;const available=1-gap*(stage.length-1);stage.forEach(([name,value])=>{const height=value/total*available;positions.set(name,{x:column ? .99 : .01,y:offset+height/2});offset+=height+gap;});});
    }
    return {data:[{type:'sankey',arrangement:'snap',orientation:'h',valueformat:',.2f',
      node:{label:nodes.map(name=>name.length>26?`${name.slice(0,25)}…`:name),customdata:nodes,
        ...(positions.size?{x:nodes.map(name=>positions.get(name)!.x),y:nodes.map(name=>positions.get(name)!.y)}:{}),
        pad:12,thickness:12,color:colors,line:{width:0},hovertemplate:'%{customdata}<br>流量：%{value:,.2f}<extra></extra>'},
      link:{source:rows.map(row=>index.get(label(row[view.dimensions[0]]))),target:rows.map(row=>index.get(label(row[view.dimensions[1]]))),
        value:weights,customdata:rows.map(row=>[label(row[view.dimensions[0]]),label(row[view.dimensions[1]]),String(row[view.measure])]),
        color:rows.map(row=>{const hex=colors[index.get(label(row[view.dimensions[0]]))!];return `rgba(${[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(',')},0.22)`;}),
        hovertemplate:`%{customdata[0]} → %{customdata[1]}<br>${businessLabel(view.measure)}：%{customdata[2]}<extra></extra>`}}],layout};
  }
  const tree=new Map<string,{label:string;parent:string;value:number;path:string;color:string;exact?:string}>();
  const groups=Array.from(new Set(rows.map(row=>label(row[view.dimensions[0]])))).sort();
  tree.set('[]',{label:'全部',parent:'',value:0,path:'全部',color:'#71858C'});
  rows.forEach((row,i)=>{
    const path=view.dimensions.map(dim=>row[dim]),color=advancedColors[groups.indexOf(label(path[0]))%advancedColors.length];
    tree.get('[]')!.value+=weights[i];
    path.forEach((value,depth)=>{
      const id=JSON.stringify(path.slice(0,depth+1));
      const item=tree.get(id)||{label:label(value),parent:JSON.stringify(path.slice(0,depth)),value:0,path:path.slice(0,depth+1).map(label).join(' › '),color};
      item.value+=weights[i];if(depth===path.length-1)item.exact=String(row[view.measure]);tree.set(id,item);
    });
  });
  const entries=[...tree.values()];
  return {data:[{type:view.chart_type,ids:[...tree.keys()],labels:entries.map(item=>item.label),parents:entries.map(item=>item.parent),
    values:entries.map(item=>item.value),branchvalues:'total',sort:true,
    customdata:entries.map(item=>[item.path,item.exact??item.value.toLocaleString('zh-CN',{maximumFractionDigits:2})]),
    textinfo:'label',textfont:{size:14},marker:{colors:entries.map(item=>item.color),line:{width:2,color:'transparent'}},
    ...(view.chart_type==='treemap'?{texttemplate:entries.map(item=>item.exact&&item.value/tree.get('[]')!.value>=.03?'%{label}<br>%{percentRoot:.1%}':'%{label}')} : {}),
    hovertemplate:`%{customdata[0]}<br>${businessLabel(view.measure)}：%{customdata[1]}<extra></extra>`,
    ...(view.chart_type==='treemap'?{tiling:{pad:4},pathbar:{visible:true,thickness:28}}:{insidetextorientation:'auto'})}],layout};
}
