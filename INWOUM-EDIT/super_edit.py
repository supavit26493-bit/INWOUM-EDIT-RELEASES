"""Local Super timing and revision editing. No transcription or model client."""
import copy,json,math,unicodedata
from pathlib import Path
import bootstrap
from super_font import Metrics

TIMING_POLICY='later-in-shorter-out-v1'
START_DELAY=.3
END_TRIM=1.0

def adjust_times(plan,project):
    result=copy.deepcopy(plan)
    if result.get('timing_adjustment',{}).get('policy')==TIMING_POLICY:return result
    fps=project['fps']
    for item in result['items']:
        first,last=round(item['start']*fps),round(item['end']*fps)
        # Maintain at least 1.5 seconds; never move outside the original window.
        minimum=min(last-first,max(1,math.ceil(1.5*fps)))
        first=min(first+round(START_DELAY*fps),last-minimum)
        last=max(first+minimum,last-round(END_TRIM*fps))
        item['start'],item['end']=float(first/fps),float(last/fps)
    result['timing_adjustment']=dict(policy=TIMING_POLICY,start_delay=START_DELAY,end_trim=END_TRIM,minimum_seconds=1.5)
    return result

def validate_export(plan,project):
    items=plan.get('items')
    if not isinstance(items,list) or not items:raise ValueError('ต้องเหลือ Super อย่างน้อย 1 ข้อความ')
    metrics=Metrics(bootstrap.ROOT/'web/fonts/Kanit-Bold.ttf');previous=0
    for i,row in enumerate(items,1):
        if not isinstance(row,dict):raise ValueError('รายการ Super ไม่ถูกต้อง')
        a,b=row.get('start'),row.get('end');text=row.get('text')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,b)):raise ValueError(f'Super {i}: เวลาไม่ถูกต้อง')
        first,last=round(a*project['fps']),round(b*project['fps'])
        if not 0<=a<b<=project['seconds']+.001 or not previous<=first<last<=project['duration']:raise ValueError(f'Super {i}: เวลาซ้อน สั้นกว่า 1 เฟรม หรือเกินคลิป')
        if not isinstance(text,str) or not text.strip() or any(ord(c)<32 for c in text):raise ValueError(f'Super {i}: ต้องเป็นข้อความบรรทัดเดียว')
        if sum(unicodedata.category(c) not in ('Mn','Mc','Me') for c in text)>46 or metrics.width(text,project['font_size'])>project['width']*.8:raise ValueError(f'Super {i}: ข้อความยาวเกินพื้นที่บรรทัดเดียว กรุณาย่อข้อความ')
        row['start'],row['end']=float(first/project['fps']),float(last/project['fps'])
        previous=last
    return plan

def prepare(folder,payload):
    from super_mode import load_project,validate_config
    if not isinstance(payload,dict):raise ValueError('ข้อมูลแก้ไข Super ไม่ถูกต้อง')
    base=json.loads((folder/'super-result.json').read_text('utf8'))
    if payload.get('base_revision')!=base['revision']:raise ValueError('งานนี้มี revision ใหม่แล้ว กรุณาเปิดผลล่าสุดก่อนแก้')
    config=json.loads((folder/'config.json').read_text('utf8'))
    if config.get('content_mode')!='super':raise ValueError('งานนี้ไม่ใช่งาน Super')
    project=load_project(validate_config(config))
    identity=folder/'super-source.json'
    if not identity.exists() or json.loads(identity.read_text('utf8'))!=project['identity']:raise ValueError('ต้นฉบับเปลี่ยนจากงานเดิม กรุณาตรวจ XML และสื่อก่อนสร้างใหม่')
    rows=payload.get('items')
    if not isinstance(rows,list) or not 0<len(rows)<=len(base['items']):raise ValueError('ต้องเหลือ Super อย่างน้อย 1 ข้อความ และแก้เฉพาะรายการเดิม')
    seen=set();items=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('รายการ Super ไม่ถูกต้อง')
        index=row.get('index')
        if type(index)!=int or not 0<=index<len(base['items']) or index in seen:raise ValueError('รายการ Super ซ้ำหรือไม่อยู่ใน revision เดิม')
        seen.add(index);item=copy.deepcopy(base['items'][index])
        for key in ('text','start','end'):item[key]=row.get(key)
        items.append(item)
    plan=dict(items=items,edit_info=dict(edited=True,parent_revision=base['revision'],api_calls=0))
    if base.get('timing_adjustment'):plan['timing_adjustment']=base['timing_adjustment']
    elif payload.get('timing_policy')==TIMING_POLICY:
        plan['timing_adjustment']=dict(policy=TIMING_POLICY,start_delay=START_DELAY,end_trim=END_TRIM,minimum_seconds=1.5)
    validate_export(plan,project)
    return plan,project

def rebuild(folder,payload,emit=lambda **e:None,cancelled=lambda:False):
    from super_mode import export
    from engine import Progress
    plan,project=prepare(folder,payload)
    progress=Progress(folder,emit,cancelled)
    with progress.stage('export','สร้าง XML จาก Super ที่แก้เอง · ไม่เรียก API'):
        result=export(plan,project,folder)
    progress(stage='done',message=f'สร้าง XML ใหม่จาก Super {result["count"]} ข้อความแล้ว · ไม่เรียก API',result=dict(super=True,super_count=result['count'],super_file=result['combined_file'],super_text_file=result['text_file'],revision=result['revision']))
    return result
