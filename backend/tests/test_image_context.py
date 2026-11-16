from app.privacy import model_safe_value, MASK


def test_image_payload_is_not_processed_as_plain_text_or_corrupted_by_redaction():
    url='data:image/png;base64,aaa13812345678bbb'
    blocks=[{'type':'text','text':'客户邮箱 private@example.test'},{'type':'image_url','image_url':{'url':url,'detail':'high'}}]
    safe=model_safe_value(blocks)
    assert safe[1]['image_url']['url']==url
    assert 'private@example.test' not in safe[0]['text'] and MASK in safe[0]['text']
