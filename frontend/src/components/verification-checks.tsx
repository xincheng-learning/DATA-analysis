import type {VerificationCheck} from '@/lib/api';
import {buildDeliveryTrust,deliveryOutcome,deliverySources,deliveryWorkflow,shouldShowDeliveryTrust,type TrustLayer,type TrustTone} from '@/lib/delivery-trust';

const labels:Record<string,string>={passed:'通过',warning:'需关注',failed:'未通过',not_checked:'未覆盖'};
const toneClass:Record<TrustTone,string>={ok:'trust-ok',partial:'trust-partial',warn:'trust-warn',fail:'trust-fail',unknown:'trust-unknown'};

export function VerificationWarnings({checks}:{checks:VerificationCheck[]}){
  return <div className="check-warnings">{checks.filter(check=>check.status==='warning'||check.status==='failed').map((check,index)=><p className="conclusion-warning" key={index}>{check.detail}</p>)}</div>;
}

export function TrustStrip({layers,unverifiedWarning}:{layers:TrustLayer[];unverifiedWarning?:string}){
  if(!layers.length)return null;
  return <div className="delivery-trust" aria-label="交付可信状态">
    <div className="delivery-trust-chips">{layers.map(layer=><span key={layer.id} className={`delivery-trust-chip ${toneClass[layer.tone]}`} data-trust={layer.id} data-tone={layer.tone}>
      <strong>{layer.label}</strong><span>{layer.status}</span>
    </span>)}</div>
    {unverifiedWarning?<p className="conclusion-warning delivery-trust-warning" role="status">{unverifiedWarning}</p>:null}
    <p className="delivery-trust-note">问题覆盖按本轮拆解统计，可能遗漏原意；结果核对只证明符合声明计划。结论解释与因果适用性尚未独立核验。</p>
  </div>;
}

export function DeliveryTrustPanel({checks,report,metadata}:{checks:VerificationCheck[];report?:Record<string,unknown>|null;metadata?:Record<string,unknown>|null}){
  if(!shouldShowDeliveryTrust({checks,report,metadata}))return null;
  const trust=buildDeliveryTrust({checks,report,metadata});
  const outcome=deliveryOutcome(metadata||{},report);
  const sources=deliverySources(metadata,report);
  const workflow=deliveryWorkflow(metadata||{},report);
  const coverage=workflow.coverage;
  const preparationLabels:Record<string,string>={ready:'可继续',limited:'限定范围继续',needs_decision:'待人工判断',needs_input:'需补资料',blocked:'暂停受影响部分',not_applicable:'本项不适用'};
  const clarification=metadata?.clarification as Record<string,unknown>|undefined;
  return <section aria-label="本轮交付依据">
    {outcome&&<div className="delivery-outcome" data-delivery-status={outcome.status} role="status"><strong>{outcome.label}</strong><p>{outcome.detail}</p></div>}
    <details className="verification-checks" open={workflow.preparation.some(item=>!['ready','not_applicable'].includes(String(item.status)))||!!clarification}>
      <summary>数据准备与人工判断</summary>
      <h4>数据准备</h4>
      {workflow.preparation.length?workflow.preparation.map((item,index)=><p key={index}><strong>{String(item.requirement||item.requirement_id||'本轮要求')}</strong> · {preparationLabels[String(item.status)]||'未核验'}：{String(item.reason||'未记录原因')}</p>):<p>未记录任务级准备结果；整表体检或执行成功不能代替本项。</p>}
      {workflow.contexts.map((context,index)=>{const readiness=context.readiness as Record<string,unknown>,input=readiness.input_coverage as {input_rows:number;missing_rows:Record<string,number>}|null;
        return <div key={index}>{context.data_version?<p>准备所用版本：{String(context.data_version).slice(0,12)}</p>:null}
          {!!context.data_version&&!!metadata?.data_version&&context.data_version!==metadata.data_version&&<p className="conclusion-warning">此准备记录属于历史数据版本，不能代替当前版本核验。</p>}
          {input?<><p>筛选输入 {input.input_rows} 行</p>{Object.entries(input.missing_rows).map(([field,count])=><p key={field}>{field}：{count} 行未知</p>)}</>:<p>筛选输入与缺失覆盖未独立核验。</p>}</div>;})}
      <h4>人工判断</h4>
      {clarification?<><p>{String(clarification.question)}</p><p>{String(clarification.why||'请在确认卡片中判断后继续，尚未默认采用建议。')}</p></>:null}
      {workflow.pending.filter(item=>item.status!=='blocked').map((item,index)=><p key={index}>{String(item.requirement||'本轮要求')}：{String(item.reason)}</p>)}
      {workflow.declarations.map((text,index)=><p key={index}>本轮沿用的用户声明：{text}</p>)}
      {!clarification&&!workflow.pending.some(item=>item.status!=='blocked')&&!workflow.declarations.length&&<p>未记录待确认项或已确认声明；这不表示全部业务口径已获人工确认。</p>}
      <small>建议与理由在确认卡片中展示，由分析师决定；来源完整性仍需结合业务判断。</small>
      {!!metadata?.cleaning&&<p>清洗前后指标影响与实际应用状态见下方清洗卡片；预览试算不代表已切换数据版本。</p>}
    </details>
    <h4>执行结果与核验范围</h4>
    <TrustStrip layers={trust.layers} unverifiedWarning={trust.unverifiedWarning}/>
    <details className="verification-checks"><summary>查看本轮核对范围与缺口</summary>
      {coverage.map((item,index)=><p key={index}><strong>{String(item.requirement||item.requirement_id||'本轮问题')}</strong>：{String(item.reason||'尚无逐项核对说明')}</p>)}
      {sources.map((source,index)=><div key={index}><strong>结果 {index+1}</strong>{Array.isArray(source.checks)&&source.checks.length?
        <VerificationChecks checks={source.checks as VerificationCheck[]}/>:<p>没有本项核对回执，其他步骤通过不能代替本项。</p>}</div>)}
      {!sources.length&&checks.length>0?<VerificationChecks checks={checks}/>:null}
      <small>业务口径需有本次计算对应的指标核对回执；字典已确认、模板可用和计算成功均不能代替。</small>
    </details></section>;
}

export function VerificationChecks({checks}:{checks:VerificationCheck[]}){
  if(!checks.length)return null;
  return <details className="verification-checks"><summary>自动核对 · {checks.length} 项（查看范围）</summary>{checks.map((check,index)=><div key={index}><strong>{labels[check.status]||check.status}</strong><p>{check.detail}</p>{check.scope&&<small>{check.scope}</small>}</div>)}<small>只证明列出的检查与范围；时间、币种、退款和业务关联口径仍按本次确认处理。</small></details>;
}
