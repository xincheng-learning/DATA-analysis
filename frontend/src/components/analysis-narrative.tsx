"use client";
import {ArrowUpRight,Printer} from 'lucide-react';
import {record,records,reportSources,businessLabel,type RecordValue} from '@/lib/analysis';
import {MessageMarkdown} from '@/components/message-markdown';
import {ResultTable} from '@/components/data-panel';

export function AnalysisNarrative({report,metadata,onViewSource}:{report:RecordValue;metadata:RecordValue;onViewSource?:(key:string)=>void}){
  const sources=reportSources({...metadata,analysis_report:report}),evidence=records(report.evidence);
  const strings=(value:unknown)=>Array.isArray(value)?value.map(String):[];
  const coverage=records(report.coverage),reviewed=report.acceptance_version===1;
  const shownTables=new Set<string>();
  const labels:Record<string,string>={answered:'已回答',linked:'已提供结果，尚有缺口',needs_input:'需要补充数据或口径',unanswered:'尚未回答'};
  return <section className="analysis-narrative" data-print-report aria-label="结论、理由与分析证据">
    <div className="report-toolbar no-print">
      <button type="button" className="report-print-button" onClick={()=>typeof window!=='undefined'&&window.print()} title="打开打印预览，可另存为 PDF">
        <Printer size={14}/><span>打印本页报告</span>
      </button>
      <small>浏览器打印友好布局；可另存为 PDF。图表请先在右侧导出 PNG 后自行附上。</small>
    </div>
    {report.summary?<div className="report-summary"><MessageMarkdown content={String(report.summary)}/></div>:null}
    {coverage.length>0&&<details className="report-coverage" open={reviewed&&coverage.some(item=>item.status!=='answered')}>
      <summary>{reviewed?`${coverage.filter(item=>item.status==='answered').length}/${coverage.length} 项已回答`:'历史证据记录'}</summary>
      <ul>{coverage.map((item,i)=><li key={String(item.requirement_id||i)} data-status={String(item.status)}><strong>{String(item.requirement)}</strong><span>{reviewed?labels[String(item.status)]||'尚未核对':'历史引用，未按当前规则验收'}</span>{item.reason?<p>{String(item.reason)}</p>:null}
        {strings(item.evidence_ids).length>0&&<details className="report-evidence"><summary>查看本项依据</summary>{evidence.filter(card=>strings(item.evidence_ids).includes(String(card.id))).map(card=><p key={String(card.id)}><strong>[{String(card.id)}]</strong> {String(card.text)}</p>)}</details>}
      </li>)}</ul>
    </details>}
    {strings(report.warnings).length>0&&<div className="report-warnings">{strings(report.warnings).map((warning,i)=><p key={i}>{warning}</p>)}</div>}
    {records(report.findings).map((finding,index)=>{
      const linked=sources.filter(source=>strings(finding.source_keys).includes(source.key));
      const refs=evidence.filter(card=>strings(finding.evidence_ids).includes(String(card.id)));
      return <section className="report-finding" key={index} data-finding-index={index}>
        <h3>{index+1}. {String(finding.title)}</h3>
        {finding.reason?<div className="report-reason"><MessageMarkdown content={String(finding.reason)}/></div>:null}
        {linked.map(source=>{
          const table=source.table;
          const cited=refs.filter(card=>String(card.id).startsWith(source.key+'.'));
          const rowIds=cited.filter(card=>card.kind==='row').map(card=>Number(String(card.id).match(/\.R(\d+)$/)?.[1])-1);
          const metricLabels=cited.flatMap(card=>card.metric?[businessLabel(String(card.metric))]:record(card.comparison).label?[String(record(card.comparison).label)]:[]);
          const selectedRows=records(table.rows).filter((row,i)=>table.kind==='comparison'?metricLabels.includes(String(row['指标'])):rowIds.includes(i));
          const tableRows=selectedRows.length&&!cited.some(card=>card.kind==='contribution')?selectedRows:records(table.rows);
          const tableKey=source.key+JSON.stringify(tableRows);
          const showTable=finding.kind!=='method'&&!shownTables.has(tableKey);
          if(showTable)shownTables.add(tableKey);
          return <div className="report-source" key={source.key}>
            {showTable&&<><div className="report-table" aria-label={`结论${index+1}的数据依据`}><ResultTable columns={strings(table.columns)} rows={tableRows} formatValue={(value,column,row)=>{
              if(typeof value!=='number')return;
              const scale=record(table.formats)[column];
              if(scale==='ratio'||scale==='percent'){const percent=value*(scale==='ratio'?100:1);return (percent!==0&&Math.abs(percent)<.01?percent.toLocaleString('zh-CN',{maximumSignificantDigits:4}):percent.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}))+'%';}
              if(table.kind==='comparison'&&String(row['指标']).includes('（BRL）')&&(Math.abs(value)>=.01||value===0))return value.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
            }}/></div>
            <p className="report-table-caption">{tableRows.length<records(table.rows).length?`本节列出对应证据的 ${tableRows.length} 行，完整表可在右侧查看。`:String(table.caption||'')}</p></>}
            {onViewSource&&<button type="button" className="report-source-link" data-source-key={source.key} onClick={()=>onViewSource(source.key)}>
              {source.hasChart?'打开完整图表':'查看完整数据与执行依据'}<ArrowUpRight size={13}/></button>}
          </div>;
        })}
        {refs.length>0&&<details className="report-evidence"><summary>数字依据 · {refs.length} 条</summary>{refs.map(card=><p key={String(card.id)}><strong>[{String(card.id)}]</strong> {String(card.text)}</p>)}</details>}
        {finding.recommendation?<div className="report-recommendation"><span>建议</span><MessageMarkdown content={String(finding.recommendation)}/></div>:null}
      </section>;
    })}
    {strings(report.hypotheses).length>0&&<div className="report-followup"><h4>值得追查的解释</h4><p className="report-table-caption">以下解释尚未由当前数据证实。</p>{strings(report.hypotheses).map((text,i)=><MessageMarkdown key={i} content={text}/>)}</div>}
    {strings(report.next_checks).length>0&&<div className="report-followup"><h4>下一步分析</h4>{strings(report.next_checks).map((text,i)=><MessageMarkdown key={i} content={text}/>)}</div>}
    <p className="report-scope">{String(report.scope||'')}</p>
    {strings(report.editorial_notes).length>0&&<details className="report-evidence"><summary>汇报依据核对记录</summary>{strings(report.editorial_notes).map((note,i)=><p key={i}>{note}</p>)}</details>}
  </section>;
}
