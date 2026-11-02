import hashlib,json
from pathlib import Path
VERSION='0.15.28'
RULESET='sales-11-v4'
from model_catalog import MODELS
ROOT=Path(__file__).parent
def rules():return (ROOT/'rules'/'SALES-CONTRACT-2.0.txt').read_text('utf8')

def rules_for(config):
    if config.get('content_mode','sales')=='sales' and config.get('sales_style','original') in ('natural','natural_v2','sales_2_1','sales_2_2'):
        base=(ROOT/'rules'/'SALES-DEFAULT-2.1.txt').read_text('utf8')
        if config.get('sales_style')in ('sales_2_1','sales_2_2'):
            import sales_reserve
            extra=''
            if config.get('sales_style')=='sales_2_1':
                import optimize21
                extra=optimize21.rules()
            return base+sales_reserve.INSTRUCTIONS+extra
        return base
    return rules()

def cached_editorial_request(current,folder,stage):
    """Reuse a completed legacy request only for this explicitly compatible policy update."""
    request=folder/(stage+'-request.json');reply=folder/(stage+'-response.json')
    if not request.exists() or not reply.exists():return current
    old=json.loads(request.read_text('utf8'));left=dict(old);right=dict(current)
    previous=left.pop('instructions','');present=right.pop('instructions','')
    for value in (left,right):
        try:value['input']=json.loads(value['input'])
        except (KeyError,ValueError,TypeError):pass
    modern=(ROOT/'rules'/'SALES-DEFAULT-2.1.txt').read_text('utf8')
    if left==right and previous.replace(rules(),modern,1)==present:return old
    return current
def status(root=ROOT):
    path=root/'INSTALL-MANIFEST.json'
    if not path.exists():return dict(version=VERSION,ruleset=RULESET,integrity='unverified',mismatches=[])
    manifest=json.loads(path.read_text('utf8'));bad=[]
    for name,digest in manifest['files'].items():
        p=(root/name).resolve()
        if not p.is_relative_to(root.resolve()) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:bad.append(name)
    return dict(version=VERSION,ruleset=RULESET,integrity='mismatch' if bad else 'verified',mismatches=bad)
def configure(data):
    if data.get('sales_style')=='sales_2_2':raise ValueError('พักโหมด 2.2 แล้ว กรุณาเลือกโหมด 2.1')
    if data.get('content_mode','sales') not in ('sales','single'):
        raise ValueError('ประเภทงานไม่ถูกต้อง')
    if data.get('content_mode')=='single':
        if data.get('single_style','natural_v2') not in ('natural','natural_v2','original'):
            raise ValueError('รูปแบบตัดคลิปเดียวไม่ถูกต้อง')
        if type(data.get('single_max_seconds',0)) is not int or data.get('single_max_seconds',0) not in (0,180):
            raise ValueError('ความยาวคลิปเดียวต้องเป็นไม่จำกัดหรือไม่เกิน 3 นาที')
        if data.get('single_method','chronological') not in ('chronological','rearrange'):
            raise ValueError('วิธีตัดคลิปเดียวไม่ถูกต้อง')
        result=dict(data,single_style=data.get('single_style','natural_v2'),mode='ai',editorial_policy='story-v4',editorial_workflow='single-story-v1',
            phrase_granularity='lexical-review',ruleset='single-story-v1',
            editorial_rules_version='single-story-v1',editorial_request_version='single-story-v1',
            hook_policy='none',auto_export=True,silence_policy='speech-v4')
        for name in ('stop_after','approved_story_map_job','defer_map_ids','group_preview','refine_map_job'):
            result.pop(name,None)
        result.pop('episode_policy',None)
        if result['single_style'] in ('natural','natural_v2','sales_2_1','sales_2_2'):result['audio_track']=1
        if result['single_style'] in ('natural','natural_v2','sales_2_1','sales_2_2') and result.get('single_method','chronological')=='rearrange':result['episode_policy']='episode-continuity-v1'
        return result
    if data.get('sales_style','natural') not in ('natural','natural_v2','sales_2_1','sales_2_2','original'):raise ValueError('รูปแบบตัดคลิปขายของไม่ถูกต้อง')
    result=dict(data,sales_style=data.get('sales_style','sales_2_1'),editorial_policy='story-v4',editorial_workflow='sales-v8',editorial_rules_version=RULESET,
        editorial_request_version='sales-v8',hook_policy='sales-v8',ruleset=RULESET,
        auto_export=data.get('auto_export',True),silence_policy='speech-v4')
    result.setdefault('model','gpt-6.1-terra');result.setdefault('effort','high')
    if result['auto_export']:result.pop('stop_after',None)
    result.pop('episode_policy',None)
    if result['sales_style'] in ('natural','natural_v2','sales_2_1','sales_2_2'):result.update(episode_policy='episode-continuity-v1',audio_track=1)
    return result
