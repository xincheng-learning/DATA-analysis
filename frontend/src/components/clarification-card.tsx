"use client";
import {useId,useState} from 'react';
import type {Clarification} from '@/lib/api';

export function ClarificationCard({question,onAnswer,disabled,answered}:{question:Clarification;onAnswer?:(answer:string,answers?:Record<string,string>)=>Promise<boolean>;disabled?:boolean;answered?:string}){
  const [choice,setChoice]=useState(''),[text,setText]=useState(''),[sending,setSending]=useState(false),[submitted,setSubmitted]=useState(false),[error,setError]=useState('');
  const [choices,setChoices]=useState<Record<string,string>>({}),[custom,setCustom]=useState<Record<string,string>>({});
  const group=useId(),busy=disabled||sending||submitted||!!answered||!onAnswer;
  const decisions=question.decisions||[];
  const answers=Object.fromEntries(decisions.map(item=>[item.id,custom[item.id]?.trim()||choices[item.id]||'']));
  const combined=decisions.length?'本次口径确认：\n'+decisions.map(item=>item.question+'\n我的决定：'+answers[item.id]).join('\n\n'):[choice,text.trim()].filter(Boolean).join('\n补充：');
  const complete=decisions.length?Object.values(answers).every(Boolean)&&combined.length<=2000:!!(choice||text.trim());
  async function submit(event:React.FormEvent){
    event.preventDefault();if(busy||!complete)return;
    setSending(true);setError('');
    try{const accepted=await onAnswer!(combined,decisions.length?answers:undefined);if(accepted)setSubmitted(true);else setError('尚未提交成功，回答已保留，可重试。');}
    catch(reason){setError(reason instanceof Error?reason.message:'提交失败，回答已保留。');}
    finally{setSending(false);}
  }
  return <form className="clarification-card" onSubmit={submit} aria-label={decisions.length?'业务口径集中确认':'需要你确认的问题'}><fieldset disabled={busy}>
    <legend>{question.question}</legend>{question.why&&<p>{question.why}</p>}
    {question.answers?.length>0&&<details><summary>已确认 {question.answers.length} 项，后续分析沿用</summary>{question.answers.map((answer,index)=><p key={index}><strong>{answer.question}</strong><br/>{answer.answer}</p>)}</details>}
    {decisions.length?<>
      <p>建议供你判断，不会默认采用。请逐项选择或修改，再一次提交。</p>
      {decisions.map((item,index)=><fieldset className="clarification-decision" key={item.id}>
        <legend>{index+1}. {item.question}</legend>
        <p><strong>建议：</strong>{item.recommendation||'目前没有足够依据推荐，请按业务定义决定。'}</p>
        <p><strong>理由：</strong>{item.why}</p><p><strong>影响：</strong>{item.impact}</p>
        {item.options.map((option,i)=><label className="clarification-choice" key={i}><input type="radio" name={`${group}-${item.id}`} value={option} checked={choices[item.id]===option} onChange={()=>setChoices(previous=>({...previous,[item.id]:option}))}/><span>{option}</span></label>)}
        {!answered&&onAnswer&&<label className="clarification-free">自行填写（填写后替代上方选项）<textarea aria-label={`自行决定 ${item.id}：${item.question}`} value={custom[item.id]||''} onChange={event=>setCustom(previous=>({...previous,[item.id]:event.target.value}))} maxLength={1000} rows={2} placeholder="也可以填写需要补充的资料或希望调整的方向…"/></label>}
      </fieldset>)}
      {!answered&&onAnswer&&<p>已填写 {Object.values(answers).filter(Boolean).length} / {decisions.length} 项。{combined.length>2000?'回答合计超过2000字，请精简后提交。':'受影响的计算将在你确认后继续。'}</p>}
    </>:<>{question.options.map((option,index)=><label className="clarification-choice" key={index}><input type="radio" name={group} value={option} checked={choice===option} onChange={()=>setChoice(option)}/><span>{option}</span></label>)}
      {!answered&&onAnswer&&<label className="clarification-free">自行回答或补充<textarea aria-label="回答或补充说明" value={text} onChange={event=>setText(event.target.value)} rows={2} placeholder="可以选择上面的选项，也可以直接说明你的口径…"/></label>}
    </>}
    {!answered&&onAnswer&&<button type="submit" className="panel-action" disabled={busy||!complete}>{sending?'正在提交…':submitted?'已提交，继续分析中':decisions.length?'确认口径并继续分析':'提交回答并继续分析'}</button>}
  </fieldset>{answered&&<p className="clarification-confirmed">已回答：{answered}</p>}{!onAnswer&&!answered&&<p>历史问题，当前无需回答。</p>}{error&&<p role="alert" className="text-destructive">{error}</p>}</form>;
}
