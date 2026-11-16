"""Redact model-bound copies; keep local source data and history intact."""
import re

MASK = '[个人字段样例已隐藏]'
_EMAIL = re.compile(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}')
_PHONE = re.compile(r'(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)')
_IDENTITY = re.compile(r'(?<!\w)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\w)')
_PERSONAL_TEXT = re.compile(r'((?:收货地址|收件地址|详细地址|邮寄地址|配送地址|住址|姓名|收货人|收件人|联系人)\s*(?:[:：=]|是|为)\s*)[^\n,，;；"\'}]+')


def is_sensitive_column(name):
    label = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', str(name)).lower()
    if re.search(r'(?:数量|人数|_count)$', label):
        return False
    return bool(re.search(
        r'(^|_)(email|e_mail|phone|mobile|telephone|address|id_card|username)($|_)|'
        r'^(name|full_name|first_name|last_name)$|'
        r'(^|_)(customer|buyer|recipient|contact|person|receiver|consignee|shipping|billing|user|seller)_?(full_)?name($|_)|'
        r'姓名|手机|电话|邮箱|身份证|住址|收货人|收件人|联系人|收货地址|收件地址|详细地址|邮寄地址|配送地址', label))


def model_safe_text(text):
    if '@' in text:
        text = _EMAIL.sub(MASK,text)
    for pattern in (_PHONE, _IDENTITY):
        text = pattern.sub(MASK, text)
    return _PERSONAL_TEXT.sub(lambda match: match[1] + MASK, text)


def model_safe_rows(rows):
    return [{key: MASK if is_sensitive_column(key) else value for key, value in row.items()} for row in rows]


def model_safe_value(value):
    private = set()

    def remember(item):
        if isinstance(item, str | int) and len(str(item)) >= 2:
            private.add(str(item))
        elif isinstance(item, list):
            for child in item:
                remember(child)

    def collect(item, row=False):
        if isinstance(item, dict):
            # A saved chart may no longer carry its original query rows.
            if isinstance(item.get('data'), list) and isinstance(item.get('layout'), dict):
                layout = item['layout']
                for trace in item['data']:
                    if not isinstance(trace, dict):
                        continue
                    meta = trace.get('meta') or {}
                    for axis in ('x', 'y'):
                        title = layout.get(axis + 'axis', {}).get('title', '')
                        if isinstance(title, dict):
                            title = title.get('text', '')
                        column = meta.get(('y' if axis == 'x' else 'x') + '_column') if trace.get('orientation') == 'h' else meta.get(axis + '_column')
                        if is_sensitive_column(column or '') or is_sensitive_column(title):
                            remember(trace.get(axis, []))
                    if is_sensitive_column(meta.get('x_column') or ''):
                        remember(trace.get('labels', []))
            for key, child in item.items():
                if is_sensitive_column(key) and (key != 'name' or row):
                    remember(child)
                collect(child, key in {'rows', 'sample_rows', 'preview'})
        elif isinstance(item, list):
            for child in item:
                collect(child, row)

    collect(value)
    replacements = sorted(private, key=len, reverse=True)

    def redact(item, row=False):
        if isinstance(item, dict):
            if item.get('type')=='image_url' and isinstance(item.get('image_url'),dict) and str(item['image_url'].get('url','')).startswith('data:image/'):
                return {**item,'image_url':dict(item['image_url'])}
            return {key: MASK if is_sensitive_column(key) and (key != 'name' or row) else redact(child, key in {'rows', 'sample_rows', 'preview'})
                    for key, child in item.items()}
        if isinstance(item, list):
            return [redact(child, row) for child in item]
        if isinstance(item, str):
            for original in replacements:
                item = item.replace(original, MASK)
            return model_safe_text(item)
        if isinstance(item, int) and str(item) in private:
            return MASK
        return item

    return redact(value)
