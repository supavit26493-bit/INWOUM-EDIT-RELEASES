"""Explain known HTTP failures without echoing remote text, requests or credentials."""
import json


def message(error):
    code='';param=''
    try:
        data=json.loads(error.read(65536));detail=data.get('error',{})
        if isinstance(detail,dict):code=detail.get('code','');param=detail.get('param','')
    except (ValueError,TypeError,AttributeError,OSError):pass
    if error.code==400:
        if code in ('invalid_json_schema','invalid_schema') or isinstance(param,str) and param.startswith(('text.format','response_format')):
            reason='รูปแบบคำตอบที่โปรแกรมส่งไม่ถูกต้องหรือซับซ้อนเกินข้อจำกัด ต้องปรับคำขอ ไม่จำเป็นต้องเปลี่ยน API key'
        elif code=='context_length_exceeded':reason='ข้อความและคำตอบที่ขอรวมกันยาวเกินบริบทที่โมเดลรองรับ ต้องลดหรือแบ่งคำขอ'
        elif param in ('model','reasoning.effort','max_output_tokens','service_tier','prompt_cache_options'):
            reason='โมเดลหรือค่าตั้งในคำขอไม่รองรับ ต้องตรวจค่าตั้งของโปรแกรม'
        else:reason='คำขอหรือค่าตั้งที่โปรแกรมส่งไม่ถูกต้อง ไม่ใช่หลักฐานว่า API key ผิดหรือเครดิตหมด'
    elif error.code==401:reason='API key ยืนยันตัวตนไม่ผ่าน กรุณาใส่ key ที่ใช้งานได้และกดบันทึก'
    elif error.code==403:reason='บัญชีหรือโปรเจกต์ไม่มีสิทธิ์เข้าถึงคำขอนี้'
    elif error.code==429:
        reason='โควตาหรือวงเงินบัญชีไม่เพียงพอ' if code=='insufficient_quota' else 'คำขอชนขีดจำกัดการใช้งาน กรุณารอสักครู่และตรวจโควตา'
    elif error.code>=500:reason='บริการ OpenAI ขัดข้องชั่วคราว คำขออาจมีค่าใช้จ่ายแล้ว'
    else:reason='บริการปฏิเสธคำขอ กรุณาตรวจการเชื่อมต่อและค่าตั้ง'
    return f'OpenAI HTTP {error.code}: {reason} · เก็บผลเดิมไว้ ไม่ส่งซ้ำอัตโนมัติ'
