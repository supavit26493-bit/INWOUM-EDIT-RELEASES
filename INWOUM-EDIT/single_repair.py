"""Patch only local word-island windows in single clips; keep other selections exact."""
import copy
import json


def windows(plan, bad):
    result=[]
    for ei in sorted({r['edit_index'] for r in bad}):
        rows=plan['edits'][ei]['selections'];spans=[]
        for si in sorted({r['selection_index'] for r in bad if r['edit_index']==ei}):
            a,b=max(0,si-2),min(len(rows),si+3)
            if spans and a<=spans[-1][1]:spans[-1][1]=max(b,spans[-1][1])
            else:spans.append([a,b])
        for a,b in spans:result.append(dict(id=f'e{ei}_s{a}_{b}',edit_index=ei,start=a,end=b))
    return result


def request_for(plan,catalog,bad,base):
    from planner import obj
    import selection_bounds
    import transport21
    patches=windows(plan,bad);by_id={u['id']:u for u in catalog};fields={};data=[];ids=set()
    for patch in patches:
        edit=plan['edits'][patch['edit_index']];a,b=patch['start'],patch['end'];rows=edit['selections'][a:b]
        allowed={s['unit_id'] for s in rows};ids.update(allowed)
        fields[patch['id']]=obj(dict(selections=dict(type='array',minItems=1,items=obj(dict(
            unit_id=dict(type='string'),first_word=dict(type='integer'),last_word=dict(type='integer')))),reason=dict(type='string')))
        data.append(dict(id=patch['id'],name=edit['name'],purpose=edit['purpose'],selections=rows,
            issues=[r for r in bad if r['edit_index']==patch['edit_index'] and a<=r['selection_index']<b],
            before=[dict(selection=s,text=''.join(w['text'] for w in (by_id[s['unit_id']]['words'] or [by_id[s['unit_id']]])[s['first_word']:s['last_word']+1])) for s in edit['selections'][max(0,a-2):a]],
            after=[dict(selection=s,text=''.join(w['text'] for w in (by_id[s['unit_id']]['words'] or [by_id[s['unit_id']]])[s['first_word']:s['last_word']+1])) for s in edit['selections'][b:b+2]],
            units=[dict(id=uid,protected=bool(by_id[uid].get('protected')),
                phrases=[w['text'] for w in (by_id[uid]['words'] or [by_id[uid]])]) for uid in sorted(allowed)]))
    schema=selection_bounds.bounded_schema(obj(fields),[by_id[uid] for uid in sorted(ids)])
    events=sorted({s['event_id'] for p in patches for s in plan['edits'][p['edit_index']]['selections'][p['start']:p['end']] if 'event_id' in s})
    if plan.get('episode_policy'):
        # Define the event enum ONCE, rather than repeating it per source unit.
        schema['$defs']['repair_event']=dict(type='string',enum=events)
        for branch in schema['$defs']['source_selection']['anyOf']:
            branch['properties']['event_id']={'$ref':'#/$defs/repair_event'};branch['required'].append('event_id')
    schema=transport21.compact_bounds(schema)
    # Extremely large local windows still use the same authoritative local bounds checks.
    if len(ids)+len(events)>900:
        item=obj(dict(unit_id=dict(type='string'),first_word=dict(type='integer',minimum=0),last_word=dict(type='integer',minimum=0)))
        if plan.get('episode_policy'):item['properties']['event_id']=dict(type='string');item['required'].append('event_id')
        schema['$defs']={'source_selection':item}
    event_ids=set(events)
    relevant=[dict(id=e['id'],subject=e.get('subject',''),requires=e.get('requires',[])) for e in plan.get('events',[]) if e['id'] in event_ids]
    req=copy.deepcopy(base)
    req.update(input=json.dumps(dict(patches=data,events=relevant),ensure_ascii=False,separators=(',',':')),
        max_output_tokens=min(14000,max(2500,500+100*sum(p['end']-p['start'] for p in patches))),
        instructions=base['instructions']+'\nLOCAL SINGLE-CLIP REPAIR: Return ONLY the named local patches, not the full clip. '
        'Replace isolated word islands with supported complete clauses, or omit an unnecessary island. '
        'Only source units inside each patch may be selected. before/after are immutable context, not output. '
        'Phrase arrays use zero-based inclusive indices. Preserve protected words, negation, conditions, '
        'event tags, event sequence, the selected single-clip method and duration. No new speech or timestamps. '
        'Keep sound passages unchanged. Supply a short Thai reason per patch.')
    req['text']['format'].update(name='single_local_continuity_repair',schema=schema)
    return req,patches


def apply(plan,catalog,patches,answer):
    import selection_bounds
    from sales_continuity import findings
    if not isinstance(answer,dict) or set(answer)!={p['id'] for p in patches}:raise ValueError('คำตอบซ่อมคลิปเดี่ยวไม่ตรงจุดที่ขอ')
    fixed=copy.deepcopy(plan);units={u['id']:u for u in catalog}
    # Descending indices keep earlier windows stable when the replacement length changes.
    for p in sorted(patches,key=lambda p:(p['edit_index'],p['start']),reverse=True):
        rows=plan['edits'][p['edit_index']]['selections'][p['start']:p['end']];value=answer[p['id']]
        allowed={s['unit_id'] for s in rows};replacements=value.get('selections')
        if not isinstance(value.get('reason'),str) or not value['reason'].strip():raise ValueError('ขาดเหตุผลซ่อมคลิปเดี่ยว')
        if not isinstance(replacements,list) or not replacements or any(not isinstance(s,dict) or s.get('unit_id') not in allowed for s in replacements):raise ValueError('ซ่อมคลิปเดี่ยวอ้างท่อนนอกบริบท')
        if selection_bounds.issues(dict(edits=[dict(selections=replacements)]),catalog):raise ValueError('ขอบคำหลังซ่อมคลิปเดี่ยวไม่ถูกต้อง')
        if plan.get('episode_policy'):
            pairs={(s['unit_id'],s.get('event_id')) for s in rows}
            if any((s['unit_id'],s.get('event_id')) not in pairs for s in replacements):raise ValueError('ซ่อมคลิปเดี่ยวเปลี่ยนบริบทเหตุการณ์')
            order=list(dict.fromkeys(s['event_id'] for s in rows));positions=[order.index(s['event_id']) for s in replacements]
            if positions!=sorted(positions):raise ValueError('ซ่อมคลิปเดี่ยวสลับลำดับเหตุการณ์')
        for uid in allowed:
            if not units[uid].get('protected'):continue
            def covered(selections):return {i for s in selections if s['unit_id']==uid for i in range(s['first_word'],s['last_word']+1)}
            if not covered(rows)<=covered(replacements):raise ValueError('ซ่อมคลิปเดี่ยวตัดท่อนที่ระบุให้เก็บ')
        fixed['edits'][p['edit_index']]['selections'][p['start']:p['end']]=copy.deepcopy(replacements)
    if selection_bounds.issues(fixed,catalog) or findings(fixed,catalog):raise ValueError('คลิปเดี่ยวยังมีคำโดดหรือขอบคำผิด จึงยังไม่ส่งออก')
    used={s['unit_id'] for e in fixed['edits'] for s in e['selections']}
    for row in fixed.get('source_review',[]):
        if row['unit_id'] not in used and row['decision']=='used':row.update(decision='incomplete',reason='เลือกวลีสมบูรณ์แทนคำโดด',review_source='single-local-repair-v2')
    return fixed


def repair(plan,catalog,folder,key,base,progress,bad):
    from parallel_requests import call
    from engine import save
    req,patches=request_for(plan,catalog,bad,base)
    progress(message=f'คลิปเดี่ยว: ซ่อมคำโดด {len(bad)} จุดใน {len(patches)} บริเวณ · คงส่วนอื่นตามแผนเดิม · เพิ่มได้ 1 API')
    answer=call(req,folder,key,'single-local-continuity-repair-v2',max_calls=4)
    fixed=apply(plan,catalog,patches,answer)
    save(folder/'single-continuity-repair.json',dict(policy='single-local-repair-v2',findings=bad,patches=patches,answer=answer,manual_edits=False))
    return fixed,bad
