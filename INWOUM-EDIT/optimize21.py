"""Shared local findings for one targeted 2.1 repair; never weaken validators."""
import copy,json
from pathlib import Path

def enabled(config):return config.get('content_mode','sales')=='sales' and config.get('sales_style')=='sales_2_1'
def folder_enabled(folder):
    p=folder/'config.json'
    return p.exists() and enabled(json.loads(p.read_text('utf8')))
def rules():return (Path(__file__).parent/'rules/SALES-2.1-CONSISTENCY.txt').read_text('utf8')

def prepare_raw(raw,catalog):
    import production_cleanup,episode_continuity
    result=copy.deepcopy(raw);keys=list(result['edits'])
    cleaned,changes=production_cleanup.clean(dict(edits=episode_continuity.edit_rows(result)),catalog)
    result['edits']={key:edit for key,edit in zip(keys,cleaned['edits'])}
    return result,changes

def findings(raw,catalog,request):
    import episode_continuity as ep,sales_continuity as sc
    bad=ep.findings(raw,catalog)
    if any('edit_index' not in row or row['issue']=='invalid_source_bounds' for row in bad):return bad
    events={e['id']:e for e in raw['events']};visiting=set();done=set()
    def visit(eid):
        if eid in visiting:return False
        if eid in done:return True
        visiting.add(eid)
        for required in events[eid]['requires']:
            if not visit(required):return False
        visiting.remove(eid);done.add(eid);return True
    if not all(visit(eid) for eid in events):return [dict(issue='cyclic_event_prerequisites')]
    rows=ep.edit_rows(raw);bad+=sc.findings(dict(edits=rows),catalog)
    data=json.loads(request['input']);reserves={r['id']:r for r in data.get('approved_story_map',{}).get('reserve_takes',[])}
    units={u['id']:u for u in catalog};norm=lambda s:''.join(s.split())
    for index,(mid,edit) in enumerate(raw['edits'].items()):
        evidence=reserves.get(mid)
        if not evidence or not all(k in evidence for k in ('opening_quote','payoff_quote','recovery_quote')):continue
        text=norm(''.join(''.join(w['text'] for w in (units[s['unit_id']]['words'] or [units[s['unit_id']]])[s['first_word']:s['last_word']+1]) for s in edit['selections']))
        for field in ('opening_quote','payoff_quote','recovery_quote'):
            quote=norm(evidence[field]);valid=text.startswith(quote) if field=='opening_quote' else quote in text
            if not valid:bad.append(dict(edit_index=index,issue='reserve_evidence_missing',map_id=mid,field=field,quote=evidence[field]))
    return bad

def repair_input(correction,raw,keys,affected):
    data=json.loads(correction['input']);story=data.get('original_input',{}).get('approved_story_map',{})
    reserves={r['id']:r for r in story.get('reserve_takes',[])}
    data['required_reserve_evidence']={str(i):reserves[keys[i]] for i in affected if keys[i] in reserves}
    correction['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    correction['instructions']+='\nCOMBINED DEFECT SCOPE: Fix the listed event-context/order, isolated-word and reserve-evidence defects together. Required reserve evidence in input is binding. Keep every other clip unchanged. Return complete requested edits only; no new map, general critique or extra review.'
    return correction

def pending_only_reserves(raw,bad,request):
    if not bad or any('edit_index' not in b for b in bad):return False
    data=json.loads(request['input']);reserve_ids={r['id'] for r in data.get('approved_story_map',{}).get('reserve_takes',[])}
    keys=list(raw['edits']);affected={keys[b['edit_index']] for b in bad}
    if not affected<=reserve_ids:return False
    raw['pending_reserve_keys']=sorted(affected)
    raw['pending_reserve_findings']=copy.deepcopy(bad)
    return True

def hold_pending(plan,raw,folder):
    from engine import save
    keys=set(raw.get('pending_reserve_keys',[]))
    if not keys:return plan
    result=copy.deepcopy(plan);pending=[e for e in result['edits'] if e.get('map_id') in keys]
    result['edits']=[e for e in result['edits'] if e.get('map_id') not in keys]
    result['pending_reserve_edits']=pending
    result['pending_reserve_warning']=f'คลิปหลักครบ · เทคสำรอง {len(pending)} คลิปยังไม่ผ่านตรวจ จึงยังไม่รวมใน XML'
    used={s['unit_id'] for e in result['edits'] for s in e['selections']}
    for row in result.get('source_review',[]):
        if row['unit_id'] not in used and row.get('decision')=='used':
            row.update(decision='alternative',reason='อยู่ในเทคสำรองที่ยังไม่ผ่านตรวจ เก็บแผนไว้แล้ว')
    save(folder/'pending-reserves.json',dict(edits=pending,findings=raw.get('pending_reserve_findings',[]),main_unchanged=True,automatic_retry=False))
    return result

def ensure(raw,catalog,folder,key,request,progress):
    import episode_continuity as ep,batched_sales_draft
    from planner import obj
    from engine import save
    raw=copy.deepcopy(raw);raw['episode_order_policy']=ep.ORDER_POLICY
    bad=findings(raw,catalog,request)
    save(folder/'episode-before-check.json',dict(policy='combined-2.1-v1',findings=bad))
    if bad:
        if any('edit_index' not in b for b in bad):raise ValueError('ข้อมูลเหตุการณ์หรือวงจรบริบทไม่ถูกต้อง จึงไม่จ่าย API ซ่อมคลิปซ้ำ')
        affected={b['edit_index'] for b in bad};keys=list(raw['edits'])
        schema=request['text']['format']['schema']
        fields={str(i):copy.deepcopy(schema['properties']['edits']['properties'][keys[i]]) for i in sorted(affected)}
        correction=copy.deepcopy(request)
        correction['text']['format'].update(name='combined_sales21_correction',schema=dict(obj(fields),**{'$defs':schema['$defs']}))
        correction['input']=json.dumps(dict(events=raw['events'],original_input=json.loads(request['input']),affected_edits={str(i):ep.edit_rows(raw)[i] for i in sorted(affected)},issues=bad),ensure_ascii=False,separators=(',',':'))
        repair_input(correction,raw,keys,affected)
        progress(message=f'2.1 พบข้อผิดจริงใน {len(affected)} คลิป · ซ่อมบริบท/คำโดด/หลักฐานสำรองร่วมกัน')
        try:
            answer=batched_sales_draft.correction(correction,folder,key,'combined-sales21-v1')
        except (RuntimeError,ValueError) as error:
            if not pending_only_reserves(raw,bad,request):raise
            save(folder/'reserve-repair-pending.json',dict(message=str(error).replace(key,'[redacted]') if key else str(error),automatic_retry=False))
        else:
            if set(answer)!=set(fields):raise ValueError('คำตอบซ่อม 2.1 ไม่ครบรายการ')
            fixed=copy.deepcopy(raw)
            for label in fields:fixed['edits'][keys[int(label)]]=answer[label]
            ep.validate_repair(raw,fixed,catalog,affected)
            raw=fixed;bad=findings(raw,catalog,request)
            if bad:
                save(folder/'episode-failed-check.json',dict(findings=bad,candidate=raw))
                if not pending_only_reserves(raw,bad,request):raise ValueError('คลิปหลักยังมีบริบท/คำโดด/ขอบคำไม่ครบ เก็บผลไว้แล้ว ไม่ส่งซ้ำอัตโนมัติ')
    save(folder/'episode-continuity.json',dict(policy=ep.POLICY,combined_checks=True,events=raw['events'],findings=bad,pending_reserve_keys=raw.get('pending_reserve_keys',[]),manual_edits=False))
    return raw
