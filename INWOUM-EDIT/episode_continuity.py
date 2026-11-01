"""Default-only text event blocks, checked inside the existing planning calls."""
import copy,json,re
from planner import obj
POLICY='episode-continuity-v1'
ORDER_POLICY='declared-event-order-v2'

INSTRUCTIONS='''
DEFAULT EVENT CONTINUITY: Build coherent events, not a bag of useful statements.
An event is one subject/action with its own setup -> action -> result/reaction. Distinguish a phone CASE
from CAMERA FILM even if both concern the same phone. Finish one event before introducing another;
never insert film installation into the middle of a case demonstration. Combining topics is allowed
when each event has adequate spoken introduction and a complete payoff. Prefer original local order.
Declare compact events once for all versions (id, subject, unit_ids, keep_order, requires). List unit_ids
in the intended NARRATIVE order (setup -> action -> result), not necessarily source timestamp order.
Whole alternate takes can come from earlier or later timestamps if they fit the correct narrative stage.
unit_ids are source units that actually contain this event. A mixed unit may belong to multiple events; tag only the
selected clauses about the correct subject. keep_order=true for demonstrations, causal explanations
and setup/action/result sequences. requires lists prerequisite event IDs needed to understand this event.
Use one consistent event ID for the same event in every version. Each selection carries its event_id.
After the optional opening hook, each event must form one uninterrupted run; no case -> film -> case.
hook_count is how many leading selections form ONE meaningful sales hook (0..3); single-story mode uses 0.
A hook may preview a later event, but cannot omit context or misrepresent what is being demonstrated.
References such as 'อันนี้', 'ตรงนี้', 'พอติดแล้ว' need prior context in the same event or a required event.
If the referent/action cannot be inferred from text, keep the passage with its original introduction and
neighbors, or use another complete take/version. Do not guess the image. Never invent linking narration.
Prefer preserving a continuous explanation over repeatedly jumping between source times for punchiness.
'''
MAP_INSTRUCTIONS='''
DEFAULT EVENT MAP: group each subject and its setup/action/result into a coherent event. A phone case
and camera-film installation are distinct events. continuity_links.segment_ids are chronological
dependencies to preserve, not merely similar topics. Do not interleave demonstrations of different
objects. Full/focused/combined versions remain allowed; introduce and finish each event before moving on.
Keep ambiguous pronouns/actions next to their source context. No image inference or invented bridges.
'''

def enabled(config):return config.get('episode_policy')==POLICY and config.get('single_style' if config.get('content_mode')=='single' else 'sales_style','original') in ('natural','natural_v2','sales_2_1','sales_2_2')

def schema(base,catalog,single=False):
    from selection_bounds import bounded_schema
    result=bounded_schema(base,catalog)
    text=dict(type='string');strings=dict(type='array',items=text)
    result['properties']['events']=dict(type='array',minItems=1,items=obj(dict(id=text,subject=text,
        unit_ids=dict(type='array',minItems=1,items=dict(type='string',enum=[u['id'] for u in catalog])),
        keep_order=dict(type='boolean'),requires=strings)))
    result['required'].append('events')
    for choice in result['$defs']['source_selection']['anyOf']:
        choice['properties']['event_id']=text;choice['required'].append('event_id')
    edits=result['properties']['edits']
    choices=[edits['items']] if edits.get('type')=='array' else list(edits['properties'].values())
    for edit in choices:
        edit['properties']['hook_count']=dict(type='integer',minimum=0,maximum=0 if single else 3)
        edit['required'].append('hook_count')
    return result

def edit_rows(raw):return list(raw['edits'].values()) if isinstance(raw.get('edits'),dict) else raw.get('edits',[])

def recover_single_checkpoint(folder,request,catalog,progress):
    """Recheck the known timestamp-order failure using verified completed caches only.

    Engine has already validated the prepared source. No new API request or plan
    editing occurs here; only the local order-policy metadata changes.
    """
    failed_path=folder/'episode-failed-check.json'
    if not failed_path.exists():return None
    failure=json.loads(failed_path.read_text('utf8'))
    if not failure.get('findings') or any(r.get('issue')!='event_action_order' for r in failure['findings']):return None
    from planner import fingerprint
    from model_catalog import prepare_request
    from engine import save
    ledger=json.loads((folder/'api-usage.json').read_text('utf8'))
    def completed(stage):
        saved=json.loads((folder/(stage+'-request.json')).read_text('utf8'))
        reply=json.loads((folder/(stage+'-response.json')).read_text('utf8'))
        digest=fingerprint(saved)
        if reply.get('request_hash')!=digest or not any(r.get('stage')==stage and r.get('status')=='completed' and r.get('request_hash')==digest for r in ledger):
            raise ValueError('ตรวจยืนยันคำตอบเดิมไม่ผ่าน จึงไม่ส่ง API ซ้ำอัตโนมัติ')
        return saved,reply['response'],digest
    saved,raw,source_hash=completed('single-story')
    current=prepare_request(request)
    if json.loads(saved['input'])!=json.loads(current['input']) or any(saved.get(k)!=current.get(k) for k in ('model','reasoning')):
        raise ValueError('ข้อความหรือการตั้งค่าไม่ตรงงานเดิม กรุณาสร้างงานใหม่ ไม่ส่ง API ซ้ำในงานนี้')
    _,answer,repair_hash=completed('episode-join-repair-v1')
    fixed=copy.deepcopy(raw)
    expected={str(r['edit_index']) for r in failure['findings']}|{'unused'}
    if set(answer)!=expected:raise ValueError('คำตอบแก้ลำดับเดิมไม่ครบ จึงไม่ส่ง API ซ้ำ')
    for label in expected-{'unused'}:fixed['edits'][int(label)]=answer[label]
    fixed['unused']=answer['unused']
    if fixed!=failure['candidate']:raise ValueError('แผนที่หยุดไม่ตรงคำตอบ API เดิม จึงไม่กู้ทับการแก้ไข')
    fixed['episode_order_policy']=ORDER_POLICY
    remaining=findings(fixed,catalog,single=True)
    if remaining:raise ValueError('คำตอบเดิมยังมีข้อขัดแย้งด้านเรื่อง จึงไม่เรียก API ซ้ำอัตโนมัติ')
    save(folder/'episode-cache-recovery.json',dict(policy=ORDER_POLICY,source_request_hash=source_hash,
        repair_request_hash=repair_hash,api_calls_added=0,selections_unchanged=True,
        reason='Validate the declared narrative unit order instead of monotonic source timestamps'))
    progress(message='ใช้คำตอบ API เดิมที่บันทึกครบ · ตรวจตามลำดับเรื่องใหม่ · ไม่เรียก API เพิ่ม')
    return fixed


REFERENCE_POLICY='reference-context-v2'

def reference_review(text):
    """Do not mistake a demonstrative for proof of a missing antecedent.

    Copular introductions can name their referent in the selected words themselves.
    Other deictics are uncertain from text alone: report them, never ask AI to
    rewrite a coherent version solely because a regex matched its first word.
    Temporal result/action references remain blocking without prior event context.
    """
    text=re.sub(r'\s+','',text)
    if re.match(r'^(?:พอติดแล้ว|หลังติด|ติดแล้ว)',text):return 'missing_action_context'
    m=re.match(r'^(?:อันนี้|ตรงนี้)(?:คือ|เป็น)(.+)',text)
    if m and len(m[1])>=4 and not re.match(r'^(?:แบบ|อย่าง|ยังไง|อะไร|อัน|สิ่งนี้|ที่เดิม|เหมือน|ดี|ได้|มาก)',m[1]):return 'self_contained_introduction'
    if re.match(r'^(?:อันนี้|ตรงนี้)',text):return 'uncertain_reference'
    return None

def reference_warnings(raw,catalog):
    units={u['id']:u for u in catalog};rows=[]
    for ei,edit in enumerate(edit_rows(raw)):
        seen=set()
        for si,pick in enumerate(edit['selections']):
            eid=pick.get('event_id');u=units.get(pick['unit_id'])
            if u is None:continue
            words=u.get('words',[]);text=''.join(w['text'] for w in words[pick['first_word']:pick['last_word']+1]) if words else u['text']
            if eid not in seen and reference_review(text)=='uncertain_reference':
                rows.append(dict(edit_index=ei,selection_index=si,event=eid,issue='reference_needs_human_review',severity='warning',text=text,reason='Text alone does not establish whether the referent is missing; selections unchanged'))
            seen.add(eid)
    return rows

def recover_reference_checkpoint(raw,catalog,folder,request,progress,single=False):
    """Revalidate an exact completed repair; never alter cached selections or call AI."""
    paths=[folder/n for n in ('episode-failed-check.json','episode-join-repair-v1-request.json','episode-join-repair-v1-response.json','api-usage.json')]
    if not all(p.exists() for p in paths):return None
    failure,saved,reply,ledger=[json.loads(p.read_text('utf8')) for p in paths]
    if not failure.get('findings') or any(r.get('issue')!='unintroduced_reference' for r in failure['findings']):return None
    from planner import fingerprint
    from model_catalog import prepare_request
    current=prepare_request(request)
    digest=fingerprint(saved)
    if reply.get('request_hash')!=digest or not any(r.get('stage')=='episode-join-repair-v1' and r.get('status')=='completed' and r.get('request_hash')==digest for r in ledger):return None
    data=json.loads(saved['input'])
    if data.get('original_input')!=json.loads(request['input']) or data.get('events')!=raw.get('events') or any(saved.get(k)!=current.get(k) for k in ('model','reasoning')) or not saved.get('instructions','').startswith(request.get('instructions','')):return None
    answer=reply['response'];affected=data.get('affected_edits',{})
    if set(answer)!=set(affected)|({'unused'} if single else set()):return None
    rows=edit_rows(raw);keys=list(raw['edits']) if isinstance(raw['edits'],dict) else list(range(len(rows)))
    if any(not label.isdigit() or int(label)>=len(rows) or rows[int(label)]!=prior for label,prior in affected.items()):return None
    fixed=copy.deepcopy(raw)
    for label in affected:fixed['edits'][keys[int(label)]]=answer[label]
    if single:fixed['unused']=answer['unused']
    if fixed!=failure.get('candidate') or findings(fixed,catalog,single):return None
    validate_repair(raw,fixed,catalog,{int(i) for i in affected},single)
    from engine import save
    save(folder/'episode-reference-recovery.json',dict(policy=REFERENCE_POLICY,repair_request_hash=digest,api_calls_added=0,selections_unchanged=True))
    progress(message='ตรวจบริบทใหม่จากคำตอบเดิมที่ยืนยันแล้ว · ไม่เปลี่ยนแผน · ไม่เรียก API เพิ่ม')
    return fixed


def findings(raw,catalog,single=False):
    from selection_bounds import issues
    edits=edit_rows(raw);bounds=issues(dict(edits=edits),catalog)
    if bounds:return [dict(issue='invalid_source_bounds',**row) for row in bounds]
    units={u['id']:u for u in catalog};events={e.get('id'):e for e in raw.get('events',[])};bad=[]
    if not events or len(events)!=len(raw.get('events',[])) or not all(isinstance(eid,str) and eid.strip() for eid in events):return [dict(issue='invalid_event_catalog')]
    for eid,e in events.items():
        if not e.get('subject','').strip() or not e.get('unit_ids') or set(e['unit_ids'])-set(units) or type(e.get('keep_order')) is not bool or eid in e.get('requires',[]) or set(e.get('requires',[]))-set(events):bad.append(dict(issue='invalid_event',event=eid))
    if bad:return bad
    for ei,edit in enumerate(edits):
        picks=edit['selections'];hook=edit.get('hook_count')
        if type(hook) is not int or not 0<=hook<=min(0 if single else 3,len(picks)):
            bad.append(dict(edit_index=ei,issue='invalid_hook_count'));continue
        runs=[];previous_time={};previous_position={};seen=set()
        for si,s in enumerate(picks):
            eid=s.get('event_id');e=events.get(eid)
            if not e or s['unit_id'] not in e['unit_ids']:
                bad.append(dict(edit_index=ei,selection_index=si,issue='event_source_mismatch'));continue
            u=units[s['unit_id']];words=u['words'];a=words[s['first_word']]['start'] if words else u['start']
            text=''.join(w['text'] for w in words[s['first_word']:s['last_word']+1]) if words else u['text']
            missing=set(e['requires'])-seen
            if missing:bad.append(dict(edit_index=ei,selection_index=si,issue='missing_event_context',events=sorted(missing)))
            if eid not in seen and not e['requires'] and reference_review(text)=='missing_action_context':
                bad.append(dict(edit_index=ei,selection_index=si,issue='unintroduced_reference',event=eid))
            seen.add(eid)
            if si<hook:continue
            if not runs or runs[-1]!=eid:
                if eid in runs:bad.append(dict(edit_index=ei,selection_index=si,issue='interleaved_event',event=eid))
                runs.append(eid)
            position=e['unit_ids'].index(s['unit_id'])
            if e['keep_order'] and eid in previous_time:
                if raw.get('episode_order_policy')==ORDER_POLICY:
                    reversed_order=(position<previous_position[eid] or (position==previous_position[eid] and a<previous_time[eid]-.001))
                else:reversed_order=a<previous_time[eid]-.001
                if reversed_order:bad.append(dict(edit_index=ei,selection_index=si,issue='event_action_order',event=eid))
            previous_time[eid]=a
            previous_position[eid]=position
    return bad

def validate_repair(raw,fixed,catalog,affected,single=False):
    units={u['id']:u for u in catalog}
    for i in affected:
        for s in edit_rows(raw)[i]['selections']:
            if not units[s['unit_id']].get('protected'):continue
            before=set(range(s['first_word'],s['last_word']+1))
            after={w for pick in edit_rows(fixed)[i]['selections'] if pick['unit_id']==s['unit_id'] for w in range(pick['first_word'],pick['last_word']+1)}
            if not before<=after:raise ValueError('การแก้เหตุการณ์ตัดช่วงที่ผู้ใช้ระบุให้เก็บ')
        if not single:
            import editorial,quality_v8
            a=editorial.assembled_scripts(dict(edits=[edit_rows(raw)[i]]),catalog)[0]['assembled_text']
            b=editorial.assembled_scripts(dict(edits=[edit_rows(fixed)[i]]),catalog)[0]['assembled_text']
            if quality_v8.price_findings(b) and not quality_v8.price_findings(a):raise ValueError('การแก้เหตุการณ์เพิ่มคำราคาที่ห้ามใช้')


def ensure(raw,catalog,folder,key,request,progress,single=False):
    """One targeted correction only if structural event checks actually fail."""
    import optimize21
    if not single and optimize21.folder_enabled(folder):return optimize21.ensure(raw,catalog,folder,key,request,progress)
    from engine import save
    from parallel_requests import call
    raw=copy.deepcopy(raw);raw['episode_order_policy']=ORDER_POLICY
    recovered=recover_reference_checkpoint(raw,catalog,folder,request,progress,single)
    if recovered is not None:raw=recovered
    bad=findings(raw,catalog,single)
    save(folder/'episode-before-check.json',dict(policy=POLICY,findings=bad))
    if bad:
        affected={r['edit_index'] for r in bad if 'edit_index' in r}
        # Invalid global metadata needs explicit review; never guess event assignments locally.
        if any('edit_index' not in r for r in bad):raise ValueError('ข้อมูลกลุ่มเหตุการณ์ไม่ครบ ตรวจแผนที่บันทึกไว้ก่อนส่งออก')
        keys=list(raw['edits']) if isinstance(raw['edits'],dict) else list(range(len(raw['edits'])))
        fields={str(i):copy.deepcopy(request['text']['format']['schema']['properties']['edits']['items'] if single else request['text']['format']['schema']['properties']['edits']['properties'][keys[i]]) for i in sorted(affected)}
        correction=copy.deepcopy(request);root_schema=correction['text']['format']['schema']
        output_fields=dict(fields)
        if single:output_fields['unused']=root_schema['properties']['unused']
        correction['text']['format'].update(name='event_join_correction',schema=dict(obj(output_fields),**{'$defs':root_schema['$defs']}))
        correction['input']=json.dumps(dict(events=raw['events'],original_input=json.loads(request['input']),
            affected_edits={str(i):edit_rows(raw)[i] for i in sorted(affected)},issues=bad),ensure_ascii=False,separators=(',',':'))
        correction['instructions']+='\nRepair ONLY affected_edits. Keep the supplied events and IDs immutable. Return keys for the listed edit indices only. Fix context/order/interleaving by retaining complete source context; preserve supported topics and protected selections. Do not invent speech or alter other versions.'
        progress(message='พบรอยต่อเหตุการณ์ไม่ต่อเนื่อง · ให้ AI แก้เฉพาะเวอร์ชันที่มีปัญหา · เพิ่มได้ 1 API')
        if (folder/'draft-batches.json').exists():
            import batched_sales_draft
            answer=batched_sales_draft.correction(correction,folder,key,'episode-join-repair-v1')
        else:answer=call(correction,folder,key,'episode-join-repair-v1',max_calls=4)
        if set(answer)!=set(output_fields):raise ValueError('คำตอบแก้เหตุการณ์ไม่ครบ')
        fixed=copy.deepcopy(raw)
        for label in fields:fixed['edits'][keys[int(label)]]=answer[label]
        if single:fixed['unused']=answer['unused']
        validate_repair(raw,fixed,catalog,affected,single)
        raw=fixed;bad=findings(raw,catalog,single)
        if bad:
            save(folder/'episode-failed-check.json',dict(findings=bad,candidate=raw))
            labels={'event_action_order':'ลำดับเหตุการณ์ยังไม่ตรงกับแผน','interleaved_event':'มีการแทรกเหตุการณ์เดิมกลับมากลางเรื่อง','missing_event_context':'ขาดท่อนอธิบายเหตุการณ์ก่อนหน้า','unintroduced_reference':'มีคำอ้างอิงที่ยังไม่มีบริบท','event_source_mismatch':'ท่อนที่เลือกไม่ตรงกลุ่มเหตุการณ์'}
            first=bad[0];where=(' · ท่อนที่ '+str(first['selection_index']+1)) if 'selection_index' in first else ''
            raise ValueError(labels.get(first['issue'],'บริบทเหตุการณ์ยังไม่ครบ')+where+' · เก็บคำตอบเดิมไว้แล้ว จึงยังไม่ส่งออก')
    save(folder/'episode-continuity.json',dict(policy=POLICY,reference_policy=REFERENCE_POLICY,reference_warnings=reference_warnings(raw,catalog),events=raw['events'],findings=bad,
        scope='text and source-order checks; not visual/listening approval',manual_edits=False))
    return raw

def attach(plan,raw):
    plan['events']=copy.deepcopy(raw['events']);plan['episode_policy']=POLICY
    plan['episode_order_policy']=raw.get('episode_order_policy','source-time-v1')
    return plan

def record(plan,catalog,folder):
    """Report actual exported selections, not only the model's proposed grouping."""
    from engine import save
    units={u['id']:u for u in catalog};events={e['id']:e for e in plan['events']};clips=[]
    lines=['# ความต่อเนื่องของเหตุการณ์','',
        'AI จัดกลุ่มจากข้อความ โปรแกรมตรวจรหัส ลำดับและบริบทตามกลุ่มนั้น ยังไม่ยืนยันภาพหรือการฟังจริง','']
    for edit in plan['edits']:
        rows=[];lines+=['## '+edit['name'],'']
        for i,s in enumerate(edit['selections']):
            u=units[s['unit_id']];words=u['words'][s['first_word']:s['last_word']+1]
            text=''.join(w['text'] for w in words) if words else u['text']
            row=dict(selection=i,event_id=s['event_id'],subject=events[s['event_id']]['subject'],
                source=u['id'],first_word=s['first_word'],last_word=s['last_word'],text=text)
            rows.append(row);lines+=['- '+row['subject']+' · '+u['id']+' · '+text]
        clips.append(dict(name=edit['name'],hook_count=edit['hook_count'],selections=rows))
    warnings=reference_warnings(plan,catalog)
    if warnings:lines+=['','## คำอ้างอิงที่ควรตรวจเพิ่ม (ไม่หยุดส่งออก)']+['- เวอร์ชัน '+str(r['edit_index']+1)+' ท่อน '+str(r['selection_index']+1)+': '+r['text'] for r in warnings]
    save(folder/'episode-continuity.json',dict(policy=POLICY,reference_policy=REFERENCE_POLICY,reference_warnings=warnings,order_policy=plan.get('episode_order_policy','source-time-v1'),events=plan['events'],clips=clips,
        findings=findings(plan,catalog),scope='text and source-order checks; not visual/listening approval',manual_edits=False))
    (folder/'EPISODE-CONTINUITY.md').write_text('\n'.join(lines),'utf8')
