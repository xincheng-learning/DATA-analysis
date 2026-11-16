"use client";

import { useEffect, useRef, useState } from 'react';
import { Dialog } from '@base-ui/react/dialog';
import { ArrowRight, Check, Database, Plus, RotateCcw, ScanLine, X } from 'lucide-react';
import { workbenchRequest, type WorkbenchSnapshot, type CleaningPreview, type CleaningRule, type DataTable,type AnalysisTask } from '@/lib/api';
import { makeCleaningRule, metricChanges, cleaningOperations, cleaningRuleText, cleaningNullChanges } from '@/lib/workbench';
import { ResultTable } from './data-panel';
import { RelationshipWorkbench } from './relationship-workbench';
import {TemporalEvidence} from './temporal-evidence';
import {CleaningAnalysisImpact} from './cleaning-analysis-impact';

const operations = cleaningOperations;
const cell = (value: unknown) => value == null ? '未知 / 空值' : String(value);

export function DataWorkbench({ sessionId, disabled, onClose, onChanged,onTaskStarted,initialTab='cleaning' }: {
  sessionId: string; disabled: boolean; onClose: () => void; onChanged: (tables: DataTable[]) => void;
  onTaskStarted?:(task:AnalysisTask)=>void;
  initialTab?:'cleaning'|'relationships';
}) {
  const [data, setData] = useState<WorkbenchSnapshot | null>(null);
  const [tab, setTab] = useState<'cleaning' | 'relationships'>(initialTab);
  const [tableName, setTableName] = useState('');
  const [rules, setRules] = useState<CleaningRule[]>([]);
  const [operation, setOperation] = useState<CleaningRule['operation']>('trim');
  const [column, setColumn] = useState('');
  const [value, setValue] = useState('');
  const [format, setFormat] = useState('%Y/%m/%d');
  const [keys, setKeys] = useState<string[]>([]);
  const [keep, setKeep] = useState<'first' | 'last'>('first');
  const [confirmed, setConfirmed] = useState(false);
  const [numberOptions,setNumberOptions]=useState<Partial<CleaningRule>>({decimal_separator:'.',thousands_separator:'',currency_token:'',failure_policy:'reject'});
  const [preview, setPreview] = useState<CleaningPreview | null>(null);
  const [busy, setBusy] = useState('读取');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const alive = useRef(false);
  const previewSection=useRef<HTMLElement>(null);
  useEffect(()=>{if(preview)previewSection.current?.scrollIntoView({block:'start',behavior:'instant'});},[preview]);
  useEffect(() => {
    alive.current = true;
    workbenchRequest<WorkbenchSnapshot>(sessionId).then(result => {
      if (!alive.current) return;
      setData(result); setTableName(result.tables[0]?.table_name || ''); setColumn(result.tables[0]?.columns[0] || '');
    }).catch(err => { if (alive.current) setError(err.message); })
      .finally(() => { if (alive.current) setBusy(''); });
    return () => { alive.current = false; };
  }, [sessionId]);
  const table = data?.tables.find(t => t.table_name === tableName);
  const blocked = disabled || !!busy;
  function edit() { setPreview(null); setError(''); setNotice(''); }
  function selectTable(name: string) {
    setTableName(name); setColumn(data?.tables.find(t => t.table_name === name)?.columns[0] || '');
    setRules([]); setKeys([]); setConfirmed(false); edit();
  }
  function addRule() {
    if (!table) return;
    try {
      const numeric = /INT|FLOAT|DOUBLE|DECIMAL|NUMERIC/i.test(table.dtypes[column] || '');
      const rule = makeCleaningRule(operation, column, value, format, keys, keep, confirmed, numeric, numberOptions);
      setRules(previous => [...previous, rule]); edit(); setConfirmed(false);
    } catch (err) { setError((err as Error).message); }
  }
  async function run(action: 'preview' | 'apply' | 'undo') {
    if (!data || blocked) return;
    setBusy(action); setError(''); setNotice('');
    try {
      if (action === 'preview') {
        const result = await workbenchRequest<CleaningPreview>(sessionId, 'cleaning/preview', { table: tableName, snapshot: data.snapshot, rules });
        if (alive.current) setPreview(result);
      } else {
        const result = await workbenchRequest<WorkbenchSnapshot>(sessionId, `cleaning/${action}`,
          action === 'apply' ? { preview_id: preview?.preview_id } : { table: tableName, snapshot: data.snapshot });
        if (!alive.current) return;
        setData(result); setPreview(null); setRules([]); onChanged(result.tables);
        setNotice(action === 'apply' ? '已应用新版本，原始数据和历史分析已保留。' : '已撤销上一步清洗，后续分析将使用恢复后的数据。');
      }
    } catch (err) { if (alive.current) { setError((err as Error).message); setPreview(null); } }
    finally { if (alive.current) setBusy(''); }
  }
  const changes = preview ? metricChanges(preview.metrics_before.facts, preview.metrics_after.facts) : [];
  return <Dialog.Root open onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <Dialog.Portal><Dialog.Backdrop className="fixed inset-0 z-50 bg-black/45 backdrop-blur-sm" />
      <Dialog.Popup className={`cleaning-dialog${tab==='relationships'?' structure-dialog':''}`}>
        <header className="cleaning-header"><div><Dialog.Title>{tab==='relationships'?'数据结构与关系':'体检与清洗'}</Dialog.Title><Dialog.Description>{tab==='relationships'?'查看导入表的字段、数据和关联；AI 建议经核对后再确认。':'查看原值，预览后应用；每次应用都可撤销。'}</Dialog.Description></div>
          <button className="toolbar-button" aria-label="关闭数据工作台" disabled={!!busy} onClick={onClose}><X size={18} /></button></header>
        <div className="cleaning-body"><aside className="cleaning-tables"><p className="eyebrow">会话中的数据</p>{data?.tables.map(t => <button disabled={tab==='cleaning'?blocked:busy==='读取'} key={t.table_name} aria-pressed={tableName === t.table_name} onClick={() => selectTable(t.table_name)}><Database size={15} /><span><strong>{t.table_name}</strong><small>{t.rows.toLocaleString()} 行 · {t.columns.length} 个字段</small></span></button>)}
          <p className="cleaning-note">原始数据保留<br />变更仅作用于当前会话</p></aside>
          <main className="cleaning-main">
            <nav className="cleaning-tabs" aria-label="数据工作台视图"><button disabled={blocked} aria-pressed={tab === 'cleaning'} onClick={() => setTab('cleaning')}>数据体检与清洗</button><button disabled={blocked} aria-pressed={tab === 'relationships'} onClick={() => setTab('relationships')}>数据结构与关系</button></nav>
            {tab === 'relationships' && data && <RelationshipWorkbench key={data.snapshot} sessionId={sessionId} data={data} disabled={disabled} selectedTable={tableName} onSelectTable={selectTable} onUpdated={setData} onTaskStarted={onTaskStarted} onBusyChange={busy => setBusy(busy ? '关联检查' : '')} />}
            {busy === '读取' && <p role="status">正在读取数据体检…</p>}
            {error && !table && <p className="cleaning-alert" role="alert">{error}<button disabled={blocked} onClick={() => window.location.reload()}>刷新页面重试</button></p>}
            {notice && <p className="cleaning-success" role="status"><Check size={16} />{notice}</p>}
            {tab === 'cleaning' && data && table && <>
              <div className="cleaning-overview"><div><span>当前数据</span><strong>{table.rows.toLocaleString()} <small>行</small></strong></div><div><span>缺失单元格</span><strong>{Object.values(table.null_counts).reduce((sum, n) => sum + n, 0).toLocaleString()}</strong></div><div><span>已应用清洗次数</span><strong>{Math.max(0, table.history.length - 1)}</strong></div><div><span>当前版本</span><code>{table.data_version.slice(0, 10)}</code><small>{table.original_available ? '原文件已保留' : '旧导入基线，无原文件'}</small></div></div>
              <section><div className="cleaning-section-title"><h3><ScanLine size={16} />01 · 数据体检</h3><span>当前活动版本</span></div>
                <details className="cleaning-history"><summary>查看已应用的清洗记录 · {Math.max(0,table.history.length-1)} 次</summary><ol>{table.history.map((entry,index)=><li key={entry.version}><strong>{index===0?'导入基线':`版本 ${index}`} · {entry.version.slice(0,10)}</strong>{entry.rules.map((rule,i)=><p key={i}>{cleaningRuleText(rule)}</p>)}</li>)}</ol></details>
                <div className="quality-fields">{table.columns.map(name => <button disabled={blocked} key={name} onClick={() => { setColumn(name); edit(); }} aria-pressed={column === name}><strong>{name}</strong><span>{table.dtypes[name]}</span><small className={table.null_counts[name] ? 'has-missing' : ''}>缺失 {table.null_counts[name] || 0}</small></button>)}</div>
                <TemporalEvidence key={`${tableName}:${data.snapshot}`} sessionId={sessionId} table={tableName} snapshot={data.snapshot} currentSnapshot={data.snapshot} checks={table.temporal_checks} summary={table.temporal_summary} disabled={blocked}/>
                <details className="cleaning-source"><summary>查看原始值预览（前 5 行）</summary><ResultTable key={`${tableName}:${data?.snapshot}`} rows={table.preview} columns={table.columns} /></details>
              </section>
              <section><div className="cleaning-section-title"><h3>02 · 设置清洗步骤</h3><span>金额不默认填零</span></div>
                <fieldset disabled={blocked} className="cleaning-editor">
                  <label>处理方式<select aria-label="处理方式" value={operation} onChange={e => { setOperation(e.target.value as CleaningRule['operation']); setConfirmed(false); edit(); }}>{Object.entries(operations).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
                  {operation !== 'deduplicate' ? <label>处理字段<select aria-label="处理字段" value={column} onChange={e => { setColumn(e.target.value); edit(); }}>{table.columns.map(name => <option key={name}>{name}</option>)}</select></label> : <div className="cleaning-keys"><span>一行数据由哪些字段唯一确定？</span>{table.columns.map(name => <label key={name}><input type="checkbox" checked={keys.includes(name)} onChange={e => { setKeys(previous => e.target.checked ? [...previous, name] : previous.filter(k => k !== name)); edit(); }} />{name}</label>)}</div>}
                  {(operation === 'fill' || operation === 'null_tokens') && <label>{operation === 'fill' ? '明确填充值' : '哪些文本代表缺失（用逗号分隔）'}<input aria-label={operation === 'fill' ? '明确填充值' : '空值标记'} value={value} onChange={e => { setValue(e.target.value); edit(); }} placeholder={operation === 'fill' ? '例如：未知' : '例如：NULL, 未填写'} /></label>}
                  {operation === 'parse_date' && <label>原始日期格式<input aria-label="原始日期格式" value={format} onChange={e => { setFormat(e.target.value); edit(); }} /><small>例如 2018/01/02 使用 %Y/%m/%d；转换失败会显示为空值，并在预览中计数。</small></label>}
                  {operation==='map_values'&&<label>类别对应关系<textarea aria-label="类别对应关系" rows={3} value={value} onChange={e=>{setValue(e.target.value);edit();}} placeholder={'华东地区 → 华东\n华东区 → 华东'}/><small>每行一组“原值 → 目标值”，只替换完全相同的文本。</small></label>}
                  {operation==='parse_number'&&<>
                    <label>小数符号<select aria-label="小数符号" value={numberOptions.decimal_separator} onChange={e=>{setNumberOptions({...numberOptions,decimal_separator:e.target.value as '.'|','});edit();}}><option value=".">小数点 .</option><option value=",">小数逗号 ,</option></select></label>
                    <label>千分位符号<select aria-label="千分位符号" value={numberOptions.thousands_separator} onChange={e=>{setNumberOptions({...numberOptions,thousands_separator:e.target.value as CleaningRule['thousands_separator']});edit();}}><option value="">无</option><option value=",">逗号 ,</option><option value=".">点 .</option><option value=" ">空格</option></select></label>
                    <label>已有币种标记（可留空）<input aria-label="已有币种标记" value={numberOptions.currency_token} onChange={e=>{setNumberOptions({...numberOptions,currency_token:e.target.value});edit();}} placeholder="例如 R$"/></label>
                    <label>转换失败时<select aria-label="数值转换失败策略" value={numberOptions.failure_policy} onChange={e=>{setNumberOptions({...numberOptions,failure_policy:e.target.value as 'reject'|'null'});edit();}}><option value="reject">停止，先检查格式</option><option value="null">允许转为空值（预览中核对）</option></select><small>保留金额精度，不换汇；订单号、邮编与前导零编码不转换。</small></label>
                  </>}
                  {operation === 'deduplicate' && <label>同键记录保留哪条<select value={keep} onChange={e => { setKeep(e.target.value as 'first' | 'last'); edit(); }}><option value="first">保留原始顺序第一条</option><option value="last">保留原始顺序最后一条</option></select></label>}
                  {(operation === 'fill' || operation === 'deduplicate') && <label className="cleaning-confirm"><input type="checkbox" checked={confirmed} onChange={e => { setConfirmed(e.target.checked); edit(); }} />{operation === 'fill' ? '我确认填充值符合该字段的业务含义' : '我确认这些字段代表业务唯一键，并接受同键不同值时的保留策略'}</label>}
                  <button className="toolbar-button" onClick={addRule} disabled={rules.length >= 20}><Plus size={14} />加入步骤</button>
                </fieldset>
                <ol className="cleaning-rules">{rules.map((rule, index) => <li key={index}><span>{index + 1}</span><strong>{operations[rule.operation]}</strong><code>{rule.column || rule.keys?.join(' + ')}</code><button aria-label={`移除步骤 ${index + 1}`} disabled={blocked} onClick={() => { setRules(rules.filter((_, i) => i !== index)); edit(); }}><X size={14} /></button></li>)}</ol>
              </section>
              {preview && <section className="cleaning-preview" ref={previewSection}><div className="cleaning-section-title"><h3>03 · 核对处理影响</h3><span>尚未应用</span></div><div className="cleaning-impact"><strong>{preview.before_rows.toLocaleString()} <ArrowRight size={16} /> {preview.after_rows.toLocaleString()} 行</strong><span>影响 {preview.affected_rows} 行</span><span>转换失败 {preview.conversion_failures} 条</span><span>同键不同值 {preview.conflicting_keys} 组</span></div>
                <p className="cleaning-note">以下为全部待执行步骤合并后的影响；对照样例展示 {preview.examples.length} / {preview.affected_rows} 个受影响行，不代表全量明细。</p>
                {cleaningNullChanges(preview).length>0&&<div className="cleaning-null-changes"><h4>字段缺失变化</h4><ResultTable rows={cleaningNullChanges(preview)} columns={['字段','处理前缺失','处理后缺失','变化']}/></div>}
                {!!preview.type_changes?.length&&<div className="cleaning-null-changes"><h4>字段值类型变化</h4><ResultTable rows={preview.type_changes.map(item=>({'字段':item.column,'处理前类型':item.before,'处理后类型':item.after}))} columns={['字段','处理前类型','处理后类型']}/><p className="cleaning-note">类型取自实际值。文本“0”转成数值零也计入影响行；金额在对照样例中用文本保留精度。</p></div>}
                {!!preview.conversion_failures && <p className="cleaning-alert">转换失败的日期或数值将变为空值，请检查格式和下方样例后决定是否应用。</p>}
                <div className="cleaning-diff">{preview.examples.map((example, index) => <div key={index}><p>样例 {index + 1}</p><div><span>处理前</span><pre>{JSON.stringify(example.before, null, 2)}</pre></div><div><span>处理后</span><pre>{example.after ? JSON.stringify(example.after, null, 2) : '该行将被移除'}</pre></div></div>)}</div>
                <h4>指标影响 · Olist / BRL 模板试算</h4>{!data?.commerce.confirmed && <p className="cleaning-note">尚未确认业务口径，以下模板试算不代表已验证的经营指标。</p>}{changes.map(change => <p key={change.label}>{change.label}：{cell(change.before)} → {cell(change.after)} {change.unit}</p>)}
                {!changes.length && <p className="cleaning-note">{preview.metrics_after.profile ? '已识别的电商指标没有变化。' : '当前数据未映射 Olist 指标，无法计算电商指标影响。'}</p>}
                {preview.metrics_after.warnings.map(warning => <p className="cleaning-note" key={warning}>{warning}</p>)}
                <CleaningAnalysisImpact impact={preview.analysis_impact}/>
              </section>}
            </>}
            {table?.analysis_impact&&!preview&&<CleaningAnalysisImpact impact={table.analysis_impact} state="applied" currentSnapshot={data?.snapshot}/>}
          </main></div>
        {tab==='cleaning'?<footer className="cleaning-footer"><button className="toolbar-button" disabled={blocked || !table?.can_undo} onClick={() => run('undo')}><RotateCcw size={14} />撤销上一步</button><span role={error?'alert':undefined} className={error?'cleaning-inline-warning':undefined}>{error || (disabled ? '分析进行中，数据暂不可修改' : busy ? '正在处理，请稍候…' : '预览不会修改数据')}</span><button className="toolbar-button" disabled={blocked || !rules.length} onClick={() => run('preview')}>预览影响</button><button className="cleaning-apply" disabled={blocked || !preview} onClick={() => run('apply')}><Check size={15} />确认应用</button></footer>:<footer className="cleaning-footer"><span>表数据只读查看；关系确认保存在当前数据版本中。</span></footer>}
      </Dialog.Popup></Dialog.Portal>
  </Dialog.Root>;
}
