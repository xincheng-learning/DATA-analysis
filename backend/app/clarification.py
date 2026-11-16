"""Continue the latest saved question with its goal, answers and versioned evidence."""
import json
import re
from copy import deepcopy

from app.schemas import ClarificationQuestion, ClarificationReply
from app.session import SQLiteBackend, get_session_store
from app.config import settings


def group_decisions(brief,proposed=None):
    """Bind suggestions to actual unresolved parts; missing advice is not invented."""
    proposed=proposed.model_dump() if isinstance(proposed,ClarificationQuestion) else proposed or {}
    pending={}
    for spec in brief.get('acceptance',[]):
        if spec.get('intent_gap'):pending.setdefault(spec['intent_gap'],[]).append(spec)
    if not pending:return {**proposed,'decisions':[]} if proposed else proposed
    suggestions=proposed.get('decisions',[])
    decisions=[]
    for question,specs in pending.items():
        suggestion=next((item for item in suggestions if item['question']==question),{})
        if not suggestion and len(pending)==1 and proposed.get('question')==question:
            suggestion={key:proposed[key] for key in ('why','options') if key in proposed}
        options=suggestion.get('options',[])
        recommendation=suggestion.get('recommendation','')
        if options and recommendation not in options:recommendation=''
        decisions.append({'id':specs[0]['id'],'question':question,'options':options,'recommendation':recommendation,
            'why':suggestion.get('why') or '该口径尚未确定，会改变对应分析结果；已有确认的其他条件继续沿用。',
            'impact':suggestion.get('impact') or '影响：'+'；'.join(spec['requirement'] for spec in specs)[:990]})
    return {'question':decisions[0]['question'] if len(decisions)==1 else '请集中确认以下业务口径，再继续受影响的分析。',
        'why':proposed.get('why') or '建议仅供参考，由你逐项选择或修改；未确认前不会执行受影响的计算。',
        'options':decisions[0]['options'] if len(decisions)==1 else [],'decisions':decisions}


def prepare_reply(req):
    if req.cleaning_reply:return
    if settings.app_mode!='local' or not req.session_id:
        if req.clarification_reply:raise ValueError('请在问题所属的本地聊天中回答。')
        return
    backend = get_session_store()._backend
    if not req.session_id or not isinstance(backend, SQLiteBackend):
        if req.clarification_reply:
            raise ValueError('请在问题所属的本地聊天中回答。')
        return
    messages = backend.get_messages(req.session_id)
    source = next((message for message in reversed(messages) if message['role']=='assistant'),None)
    pending = source and source['metadata'].get('clarification')
    explicit = req.clarification_reply
    if not pending:
        if explicit:
            raise ValueError('请回答当前聊天的最新问题，原问题可能已回答。')
        return
    answered = any(message['id']>source['id'] and message['metadata'].get('clarification_reply',{}).get('message_id')==source['id'] for message in messages)
    if explicit and (explicit.message_id!=source['id'] or answered):
        raise ValueError('请回答最新问题，原问题已回答或不属于当前聊天。')
    if answered or not explicit and (req.images or req.quote or req.board or req.conversation or req.charts):
        return
    from app.tools.dataset_manager import get_dataset_manager
    from app.tools.data_workbench import workbench_snapshot
    data = get_dataset_manager().get(req.session_id)
    version = workbench_snapshot(data) if data and data.files else None
    answers=explicit.answers if explicit else {}
    confirmed=[{'question':pending['question'],'answer':req.question,'message_id':source['id']}]
    if answers:
        decisions=pending.get('decisions',[])
        if not decisions or set(answers)!={item['id'] for item in decisions}:
            raise ValueError('请逐项回答当前口径卡中的全部问题，不要遗漏或提交额外项目。')
        if source['metadata'].get('data_version')!=version:
            raise ValueError('数据版本已变化，请重新核对口径建议后再确认；尚未继续分析。')
        confirmed=[{'question':item['question'],'answer':answers[item['id']].strip(),'message_id':source['id'],'decision_id':item['id']} for item in decisions]
        question='本次口径确认：\n'+'\n\n'.join(item['question']+'\n我的决定：'+item['answer'] for item in confirmed)
        if len(question)>2000:raise ValueError('本次口径回答合计超过2000字，请精简后再提交；尚未继续分析。')
        req.question=question
    req.clarification_reply = ClarificationReply(message_id=source['id'],answers=answers)
    req._clarification_context = {'goal':pending.get('goal',source['content']),
        'answers':[*deepcopy(pending.get('answers',[])),*confirmed]}
    investigation = source['metadata'].get('investigation') or {}
    if source['metadata'].get('data_version')==version and investigation.get('steps'):
        req._investigation_resume = deepcopy(investigation)
        for step in req._investigation_resume['steps']:
            step.setdefault('source_message_id',source['id'])
    # Images remain available during repeated questions; references are revalidated by prepare_context.
    from app.schemas import ImageReference
    if not req.images:
        req.images = [ImageReference.model_validate(image) for image in source['metadata'].get('context_snapshot',{}).get('images',[])]


def model_question(req):
    context = req._clarification_context
    if not context:
        return req.question
    return ('原始分析目标：'+context['goal']+'\n使用者历次确认与更正（原文，不能当新查询证据）：\n'
        +json.dumps(context['answers'],ensure_ascii=False)+'\n请结合这些回答继续原目标；以使用者最新更正为准。'
        '这些明确回答是用户决定，卡片建议不是用户决定。若仍有会改变结果的新缺口，集中列出并说明理由和影响，不重复询问已确认内容。\n当前问题：'+req.question)


def complete_question(req, final):
    if settings.app_mode!='local':return
    context = req._clarification_context
    if context:
        final['clarification_context'] = deepcopy(context)
    investigation = final.get('investigation') or {}
    if final.get('route')!='clarify' and investigation.get('status')!='clarify' and not final.get('clarification'):
        return
    question = final.get('clarification') or investigation.get('clarification') or {
        'question':investigation.get('reason') or final.get('route_reason') or final.get('answer') or '请补充分析范围。'}
    question = ClarificationQuestion.model_validate(group_decisions(final.get('analysis_brief') or {},question)).model_dump()
    if settings.app_mode=='local' and req.session_id:
        from app.tools.dataset_manager import get_dataset_manager
        data=get_dataset_manager().get(req.session_id)
        columns={column for file in data.files for column in file.columns} if data else set()
        def field_label(match):
            if match[1] not in columns:return match[0]
            label=re.sub(r'[，,;；]?\s*(?:不包含|包含|不含|含|已扣除|扣除)(?:了)?(?:运费|税费|税|手续费|退款|优惠)[^，,;；]*','',match[2]).strip('，,;； ')
            return match[1]+'（'+label+'）' if label else match[1]
        def neutral(text):
            return re.sub(r'\b([A-Za-z_]\w*)[（(]([^）)]*)[）)]',field_label,text)
        question={**question,'question':neutral(question['question']),'why':neutral(question['why']),'options':[neutral(option) for option in question['options']]}
        question['decisions']=[{**item,**{key:neutral(item[key]) for key in ('question','why','recommendation','impact')},
            'options':[neutral(option) for option in item['options']]} for item in question['decisions']]
        if final.get('route')=='clarify':
            final['answer']=neutral(final.get('answer') or question['question'])
            final['route_reason']=neutral(final.get('route_reason') or question['question'])
    final['clarification'] = {**question,'goal':context.get('goal',req.question),'answers':deepcopy(context.get('answers',[]))}


def user_metadata(req):
    meta = {'context_snapshot':req._attachments} if req._attachments else {}
    if req.cleaning_reply:
        meta['cleaning_reply'] = req.cleaning_reply.model_dump()
    if req.clarification_reply:
        meta['clarification_reply'] = req.clarification_reply.model_dump()
    return meta or None
