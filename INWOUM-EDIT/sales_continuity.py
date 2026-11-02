"""Sales Default: continuous spoken phrases, with exact local boundary deduplication."""
import copy,json,re
from pathlib import Path

STYLE='natural'
POLICY='sales-continuity-v1'
REACTIONS={'จริงเหรอ','จริงหรอ','ว้าว','โอ้โห','โอ้','ใช่ครับ','ได้ครับ','ไม่ใช่','โอเค'}
INSTRUCTIONS='''
SALES STYLE: DEFAULT / NATURAL CONTINUITY (overrides only word-level assembly).
Reorder complete spoken clauses/passages, NOT isolated words/syllables collected from distant takes.
Within a passage keep its original word order and a continuous first_word..last_word range.
Trim head/tail fillers, abandoned starts and repeated boundary words when safe. Short source reactions
are allowed when they are meaningful complete utterances. Do not use a standalone word to patch grammar,
change a product name or complete a different take. Choose a complete alternative clause instead.
Example: take A 'ชาเขียวนี่มันอร่อยดี', take B 'ดีนะผมชอบซื้อกินประจำ' -> use all A,
trim only the duplicate leading 'ดี' from B, keep the rest of B continuously: 'ชาเขียวนี่มันอร่อยดีนะผมชอบซื้อกินประจำ'.
Do not remove meaningful in-source Thai reduplication. Preserve negation, conditions, uncertainty and
the price policy. Prefer natural delivery over the shortest possible transcript. FULL + focused clips,
sales hooks and <=180 seconds remain required. Never invent speech or timestamps.
'''
SINGLE_INSTRUCTIONS=INSTRUCTIONS.replace('SALES STYLE: DEFAULT / NATURAL CONTINUITY','SINGLE STYLE: DEFAULT / NATURAL CONTINUITY').replace(
    'the price policy. Prefer natural delivery over the shortest possible transcript. FULL + focused clips,\nsales hooks and <=180 seconds remain required.',
    'the selected single-story MODE and duration setting. Prefer natural delivery over the shortest possible transcript.\nReturn one non-sales clip. Prices and numbers are allowed. Chronological mode keeps full units in source order;\nonly rearrange mode may trim repeated boundary words and reorder clauses.')

def enabled(config):return config.get('content_mode','sales')=='sales' and config.get('sales_style','original') in (STYLE,'natural_v2','sales_2_1','sales_2_2')

def instructions_for(config,single=False):
    base=SINGLE_INSTRUCTIONS if single else INSTRUCTIONS
    import default02
    return default02.instructions(base) if default02.enabled(config) else base
def norm(text):return re.sub(r'[\s.,!?…ๆ]+','',text)

def clean(plan,catalog):
    """Never split an utterance internally; remove exact repetitions only at joins."""
    units={u['id']:u for u in catalog};out=copy.deepcopy(plan);changes=[]
    from speech_cleanup import REDUPLICATION
    for edit in out['edits']:
        before=copy.deepcopy(edit)
        selections=[]
        for source in edit['selections']:
            current=copy.deepcopy(source);u=units[current['unit_id']]
            if selections and u['words'] and not u.get('protected'):
                previous=selections[-1];v=units[previous['unit_id']]
                if v['words'] and not v.get('protected'):
                    left=v['words'][previous['first_word']:previous['last_word']+1]
                    right=u['words'][current['first_word']:current['last_word']+1]
                    same_contiguous=previous['unit_id']==current['unit_id'] and previous['last_word']+1==current['first_word']
                    for size in range(min(6,len(left),len(right)),0,-1):
                        text=[norm(w['text']) for w in right[:size]]
                        if not all(text) or text!=[norm(w['text']) for w in left[-size:]]:continue
                        if same_contiguous:continue
                        gap=right[0]['start']-left[-1]['end']
                        if size==1 and text[0] in REDUPLICATION and -.001<=gap<=.18:continue
                        changes.append(dict(name=edit['name'],unit_id=current['unit_id'],removed_first_word=current['first_word'],
                                            removed_last_word=current['first_word']+size-1,text=''.join(text),kind='boundary_duplicate'))
                        current['first_word']+=size
                        break
            if current['first_word']<=current['last_word']:selections.append(current)
        if not selections:raise ValueError('คลิปไม่มีวลีเหลือหลังตัดคำซ้ำตรงรอยต่อ')
        edit['selections']=selections
        from production_cleanup import hook_prefix_count
        count=hook_prefix_count(before,selections)
        if count is not None:edit['hook_count']=count
    used={s['unit_id'] for e in out['edits'] for s in e['selections']}
    for row in out.get('source_review',[]):
        if row['unit_id'] not in used and row['decision']=='used':row.update(decision='duplicate',reason='ตัดวลีที่ซ้ำตรงรอยต่อ',review_source=POLICY)
    return out,changes

def findings(plan,catalog):
    units={u['id']:u for u in catalog};result=[]
    for ei,edit in enumerate(plan['edits']):
        rows=edit['selections']
        for si,s in enumerate(rows):
            u=units[s['unit_id']];words=u['words']
            if not words or u.get('protected'):continue
            selected=words[s['first_word']:s['last_word']+1];text=''.join(w['text'] for w in selected)
            if len(selected)>1 or len(norm(text))>12 or norm(text) in REACTIONS:continue
            if selected[-1]['end']-selected[0]['start']>=.65:continue
            connected=False
            for neighbor,before in [(rows[si-1] if si else None,True),(rows[si+1] if si+1<len(rows) else None,False)]:
                if not neighbor:continue
                nw=units[neighbor['unit_id']]['words']
                if not nw:continue
                gap=selected[0]['start']-nw[neighbor['last_word']]['end'] if before else nw[neighbor['first_word']]['start']-selected[-1]['end']
                if -.001<=gap<=.25:connected=True
            if not connected:result.append(dict(edit_index=ei,selection_index=si,unit_id=u['id'],text=text,issue='isolated_word_from_discontinuous_source'))
    return result

def repair(plan,catalog,folder,key,base,progress,content_mode='sales'):
    """One targeted cached request if Default still contains isolated word islands."""
    from engine import save
    from planner import obj
    from parallel_requests import call
    import selection_bounds
    import runtime_contract
    bad=findings(plan,catalog)
    if not bad:return plan,[]
    if content_mode=='single':
        import single_repair
        return single_repair.repair(plan,catalog,folder,key,base,progress,bad)
    fields={};data=[];by_id={u['id']:u for u in catalog}
    for index in sorted({r['edit_index'] for r in bad}):
        edit=plan['edits'][index];ids={s['unit_id'] for s in edit['selections']};units=[by_id[uid] for uid in sorted(ids)]
        schema=obj(dict(selections=dict(type='array',minItems=1,items=obj(dict(unit_id=dict(type='string'),first_word=dict(type='integer'),last_word=dict(type='integer')))),reason=dict(type='string')))
        # Shared schema bounds must use one root $defs for all edit fields.
        fields['e'+str(index)]=schema
        data.append(dict(id='e'+str(index),name=edit['name'],purpose=edit['purpose'],selections=edit['selections'],
            issues=[r for r in bad if r['edit_index']==index],units=[dict(id=u['id'],phrases={str(i):w['text'] for i,w in enumerate(u['words'] or [u])}) for u in units]))
    schema=selection_bounds.bounded_schema(obj(fields),catalog)
    if plan.get('episode_policy'):
        for choice in schema['$defs']['source_selection']['anyOf']:
            choice['properties']['event_id']=dict(type='string',enum=[e['id'] for e in plan['events']]);choice['required'].append('event_id')
    rules=base['instructions'] if content_mode=='single' or plan.get('cut_style') in ('natural_v2','sales_2_1','sales_2_2') else runtime_contract.rules()+INSTRUCTIONS
    request=copy.deepcopy(base);request.update(input=json.dumps(data,ensure_ascii=False,separators=(',',':')),max_output_tokens=14000,
        instructions=rules+'\nRepair ONLY the listed edits. Replace word islands with complete clauses from the SUPPLIED units. Preserve supported topics, caveats, protected units and the selected mode/duration. Return selections and a short Thai reason per edit. Do not change other versions.')
    request['text']['format'].update(name='sales_continuity_repair',schema=schema)
    if plan.get('episode_policy'):
        request['input']=json.dumps(dict(events=plan['events'],affected_edits=data),ensure_ascii=False,separators=(',',':'))
        request['instructions']+=' Preserve event_id tags and event order/context from the supplied events. Do not interleave events.'
    progress(message=f'Default: ตรวจพบคำโดด {len(bad)} จุด · ให้ AI แก้เฉพาะท่อน · เพิ่มได้ 1 API')
    stage='single-continuity-repair-v1' if content_mode=='single' else 'sales-continuity-repair-v1'
    if (folder/'draft-batches.json').exists():
        import batched_sales_draft
        answer=batched_sales_draft.correction(request,folder,key,stage)
    else:answer=call(request,folder,key,stage,max_calls=4)
    if set(answer)!=set(fields):raise ValueError('คำตอบแก้วลีไม่ครบเวอร์ชัน')
    fixed=copy.deepcopy(plan)
    for label,row in answer.items():
        index=int(label[1:]);allowed={s['unit_id'] for s in plan['edits'][index]['selections']}
        if not isinstance(row.get('reason'),str) or not row['reason'].strip():raise ValueError('ไม่มีเหตุผลแก้วลี')
        if not row.get('selections') or any(s['unit_id'] not in allowed for s in row['selections']):raise ValueError('คำตอบแก้วลีอ้างท่อนนอกบริบทที่ส่งไป')
        fixed['edits'][index]['selections']=row['selections']
        from production_cleanup import hook_prefix_count
        count=hook_prefix_count(plan['edits'][index],row['selections'])
        if count is not None:fixed['edits'][index]['hook_count']=count
        if selection_bounds.issues(fixed,catalog):raise ValueError('ขอบคำหลังแก้วลีไม่ถูกต้อง')
        import quality_v8,editorial
        old_text=editorial.assembled_scripts(dict(edits=[plan['edits'][index]]),catalog)[0]['assembled_text']
        new_text=editorial.assembled_scripts(dict(edits=[fixed['edits'][index]]),catalog)[0]['assembled_text']
        if content_mode=='sales' and quality_v8.price_findings(new_text) and not quality_v8.price_findings(old_text):raise ValueError('การแก้วลีเพิ่มคำราคาที่ห้ามใช้ จึงยังไม่ส่งออก')
        for uid in allowed:
            if not by_id[uid].get('protected'):continue
            before={i for s in plan['edits'][index]['selections'] if s['unit_id']==uid for i in range(s['first_word'],s['last_word']+1)}
            after={i for s in row['selections'] if s['unit_id']==uid for i in range(s['first_word'],s['last_word']+1)}
            if not before<=after:raise ValueError('การแก้วลีตัดช่วงที่ผู้ใช้ระบุให้เก็บ')
    if selection_bounds.issues(fixed,catalog):raise ValueError('ขอบคำหลังแก้วลีไม่ถูกต้อง')
    if findings(fixed,catalog):raise ValueError('Default ยังมีคำเดี่ยวที่กระโดดจากต้นฉบับ กรุณาตรวจแผนที่เก็บไว้')
    used={s['unit_id'] for edit in fixed['edits'] for s in edit['selections']}
    for row in fixed.get('source_review',[]):
        if row['unit_id'] not in used and row['decision']=='used':row.update(decision='incomplete',reason='Default เลือกวลีสมบูรณ์แทนคำโดด',review_source=POLICY)
    save(folder/('single-continuity-repair.json' if content_mode=='single' else 'sales-continuity-repair.json'),dict(findings=bad,answer=answer,manual_edits=False))
    return fixed,bad
