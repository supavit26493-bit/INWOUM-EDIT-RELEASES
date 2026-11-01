"""Account model discovery and request compatibility, without generation or key storage."""
import copy, json, re, urllib.request, urllib.error

PRESETS=['gpt-6-luna','gpt-6.1-sol','gpt-6.1-terra','gpt-6-astra','gpt-6-sol',
 'gpt-5.6-luna','gpt-5.6-terra','gpt-5.6-sol',
 'gpt-5.5','gpt-5.4','gpt-5.4-mini','gpt-5.4-nano','gpt-5.4-pro',
 'gpt-5.2','gpt-5.2-pro','gpt-5.1','gpt-5','gpt-5-mini','gpt-5-nano','gpt-5-pro',
 'gpt-4.1','gpt-4.1-mini','gpt-4.1-nano','gpt-4o','gpt-4o-mini']

def model_info(model):
    if not isinstance(model,str) or not re.fullmatch(r'[a-zA-Z0-9_.:/-]{1,180}',model):
        raise ValueError('ชื่อโมเดลไม่ถูกต้อง')
    m=model.lower(); reason=''
    if any(part in m for part in ('audio','realtime','transcribe','tts','image','search','live','computer-use')):
        reason='โมเดลเฉพาะทาง ไม่รองรับการเรียบเรียงข้อความในโปรแกรมนี้'
    elif re.match(r'^gpt-(?:3|4(?:-|$)|4-turbo)',m) or m in ('gpt-4o-2024-05-13','chatgpt-4o-latest') or '-chat' in m:
        reason='รุ่นนี้ไม่รองรับรูปแบบคำตอบที่โปรแกรมต้องใช้ หรือเป็นโมเดลสำหรับ Chat'
    reasoning=bool(re.match(r'^gpt-[5-9]',m) or re.match(r'^o[134]',m))
    return dict(id=model,label=model,supported=not bool(reason),reason=reason,reasoning=reasoning)

MODELS=[model_info(m) for m in PRESETS]

def list_account_models(key):
    if not isinstance(key,str) or not key.strip():raise ValueError('ใส่ API key ในหน้าโปรแกรมก่อน')
    req=urllib.request.Request('https://api.openai.com/v1/models',headers={'Authorization':'Bearer '+key.strip()})
    try:
        with urllib.request.urlopen(req,timeout=30) as response:data=json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError(f'อ่านรายชื่อโมเดลไม่ได้ (HTTP {error.code}) ตรวจ key และสิทธิ์บัญชี') from None
    except (TimeoutError,urllib.error.URLError):raise ValueError('อ่านรายชื่อโมเดลไม่สำเร็จ กรุณาลองกดใหม่') from None
    ids=sorted({row['id'] for row in data.get('data',[]) if isinstance(row.get('id'),str) and row['id'].startswith('gpt-')})
    return [model_info(m) for m in ids]

def prepare_request(payload):
    """Do not change the chosen model, silently fall back, or retry paid requests."""
    result=copy.deepcopy(payload);model=result.get('model','');info=model_info(model)
    if not info['supported']:raise ValueError(info['reason'])
    if not info['reasoning']:result.pop('reasoning',None)
    elif re.match(r'^gpt-5-pro(?:-|$)',model):
        result['reasoning']=dict(effort='high')
    elif result.get('reasoning',{}).get('effort')=='default':result.pop('reasoning',None)
    if model.startswith('gpt-4o'):
        result['max_output_tokens']=min(result.get('max_output_tokens',16384),16384)
    elif model.startswith('gpt-4.1'):
        result['max_output_tokens']=min(result.get('max_output_tokens',32768),32768)
    return result
