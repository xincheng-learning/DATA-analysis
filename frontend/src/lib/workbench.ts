import type { CleaningRule, CommerceFact, CleaningPreview } from './api';

export const cleaningOperations = {trim:'去除首尾空白',null_tokens:'指定文本设为空值',fill:'填充缺失值',parse_date:'统一日期格式',deduplicate:'按业务键去重',parse_number:'文本转数值',map_values:'统一类别名称'};

export function makeCleaningRule(operation: CleaningRule['operation'], column: string, value: string,
  format: string, keys: string[], keep: 'first' | 'last', confirmed: boolean, numeric: boolean, numberOptions:Partial<CleaningRule>={}): CleaningRule {
  if (operation === 'parse_number') {
    if (numberOptions.decimal_separator && numberOptions.decimal_separator===numberOptions.thousands_separator) throw new Error('小数与千分位符号不能相同。');
    return {...numberOptions,operation,column};
  }
  if (operation === 'map_values') {
    let mapping:unknown;
    try {mapping=value.trim().startsWith('{')?JSON.parse(value):Object.fromEntries(value.split('\n').filter(line=>line.trim()).map(line=>{const parts=line.split('→').map(v=>v.trim());if(parts.length!==2||!parts[0])throw new Error();return parts;}));} catch {throw new Error('类别映射请每行填写“原值 → 目标值”。');}
    if (!mapping||typeof mapping!=='object'||Array.isArray(mapping)||!Object.keys(mapping).length||Object.values(mapping).some(v=>typeof v!=='string')) throw new Error('类别映射需要原值与文本目标值。');
    return {operation,column,mapping:mapping as Record<string,string>};
  }
  if (operation === 'deduplicate') {
    if (!keys.length) throw new Error('请选择一行数据的业务键。');
    return { operation, keys, keep, confirmed };
  }
  if (operation === 'fill') {
    if (!confirmed) throw new Error('请先确认填充值符合业务含义。');
    if (numeric && (!value.trim() || !Number.isFinite(Number(value)))) throw new Error('请填写有效数值。');
    return { operation, column, value: numeric ? Number(value) : value, confirmed };
  }
  if (operation === 'parse_date') return { operation, column, format };
  if (operation === 'null_tokens') return { operation, column, tokens: value.split(/[,\n]/).map(v => v.trim()).filter(Boolean) };
  return { operation, column };
}

export function cleaningRuleText(rule:CleaningRule) {
  const details=rule.operation==='map_values'?Object.entries(rule.mapping||{}).map(([a,b])=>`${a} → ${b}`).join('；')
    :rule.operation==='parse_number'?`小数 ${rule.decimal_separator||'.'} · 千分位 ${rule.thousands_separator||'无'}${rule.currency_token?' · '+rule.currency_token:''}${rule.failure_policy==='null'?' · 失败转空值':''}`
    :rule.operation==='fill'?`填入 ${String(rule.value)}`:rule.operation==='null_tokens'?(rule.tokens||[]).map(v=>v===''?'空文本':v).join('、')
    :rule.operation==='parse_date'?rule.format:rule.operation==='deduplicate'?`保留${rule.keep==='last'?'最后':'第一'}条`:'';
  return `${cleaningOperations[rule.operation]} · ${rule.column||(rule.keys||[]).join(' + ')}${details?' · '+details:''}`;
}

export function metricChanges(before: CommerceFact[], after: CommerceFact[]) {
  return after.filter(f => before.some(b => b.id === f.id && b.value !== f.value)).map(f => ({
    label: f.label, unit: f.unit, before: before.find(b => b.id === f.id)?.value ?? null, after: f.value,
  }));
}

export function cleaningNullChanges(preview:CleaningPreview){
  return Object.entries(preview.before_null_counts||{}).filter(([name,count])=>count!==preview.after_null_counts?.[name])
    .map(([name,count])=>({'字段':name,'处理前缺失':count,'处理后缺失':preview.after_null_counts[name],'变化':preview.after_null_counts[name]-count}));
}
