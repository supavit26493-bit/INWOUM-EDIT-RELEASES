"""One compact text planning call; coverage bookkeeping and checks stay local."""
import copy
import hashlib
import json
import re

import bootstrap
import coverage_report
import editorial
from fast_logic import resolve_plan
from planner import obj, fingerprint, plan_once

POLICY = 'story-v3'
RULE_FILE = 'EDITORIAL-FAST-v3.txt'
UNUSED_DECISIONS = ['duplicate','production','money','incomplete','uncertain','alternative']
SCHEMA = obj(dict(
    edits=dict(type='array',minItems=1,maxItems=100,items=obj(dict(
        name=dict(type='string'),purpose=dict(type='string'),selections=dict(type='array',minItems=1,items=obj(dict(
            unit_id=dict(type='string'),first_word=dict(type='integer'),last_word=dict(type='integer'))))))),
    unused=dict(type='array',items=obj(dict(unit_id=dict(type='string'),
        decision=dict(type='string',enum=UNUSED_DECISIONS),reason=dict(type='string'))))))


def rules_text():
    return (bootstrap.RULES/RULE_FILE).read_text(encoding='utf8')


def request_body(catalog, model, effort, notes):
    units=[]
    for unit in catalog:
        phrases=unit['words'] or [unit]
        units.append(dict(id=unit['id'],phrases=[w['text'] for w in phrases],
                          seconds=[round(w['end']-w['start'],2) for w in phrases],protected=unit['protected']))
    payload=dict(model=model,instructions=rules_text(),store=False,max_output_tokens=12000,
        input=json.dumps(dict(job_instructions=notes,units=units),ensure_ascii=False,separators=(',',':')),
        text=dict(format=dict(type='json_schema',name='edit_choices',strict=True,schema=SCHEMA)))
    if effort!='default':payload['reasoning']=dict(effort=effort)
    return payload


def normalize_plan(raw, catalog):
    """Validate exact choices; used coverage is computed, never claimed as AI review."""
    if not isinstance(raw,dict) or not isinstance(raw.get('edits'),list) or not raw['edits']:
        raise ValueError('API ไม่ส่งรายการเวอร์ชันที่สมบูรณ์')
    ids={u['id']:u for u in catalog};used=set()
    from selection_bounds import issues
    invalid=issues(raw,catalog)
    if invalid:
        raise ValueError('รหัสท่อนหรือขอบ phrase ไม่ถูกต้อง: '+json.dumps(invalid,ensure_ascii=False))
    edits=copy.deepcopy(raw['edits'])
    for edit in edits:
        if not isinstance(edit.get('purpose'),str) or not edit['purpose'].strip():
            raise ValueError('เวอร์ชันต้องมีประเด็น')
        if not isinstance(edit.get('selections'),list) or not edit['selections']:
            raise ValueError('เวอร์ชันไม่มีท่อน')
        for selection in edit['selections']:
            uid=selection.get('unit_id');a=selection.get('first_word');b=selection.get('last_word')
            if uid not in ids or type(a)!=int or type(b)!=int or not 0<=a<=b<max(1,len(ids[uid]['words'])):
                raise ValueError('รหัสท่อนหรือขอบ phrase ไม่ถูกต้อง')
            used.add(uid)
            selection.update(reason='เลือกตามแผน: '+edit['purpose'],reason_source='selection_map')
    unused=raw.get('unused')
    if not isinstance(unused,list):raise ValueError('ขาดรายการท่อนที่ไม่ใช้')
    reviews=[];seen=set()
    for row in unused:
        uid=row.get('unit_id')
        if uid not in ids or uid in used or uid in seen or row.get('decision') not in UNUSED_DECISIONS or not isinstance(row.get('reason'),str) or not row['reason'].strip():
            raise ValueError('รายการท่อนที่ไม่ใช้ไม่ตรงแผนหรือเหตุผลไม่ครบ')
        seen.add(uid);reviews.append(dict(**row,review_source='ai_unused_reason'))
    if seen!=set(ids)-used:raise ValueError('รายการเหตุผลยังไม่ครบท่อนที่ไม่ใช้')
    reviews.extend(dict(unit_id=u['id'],decision='used',reason='พบการเลือกในแผน',review_source='selection_map')
                   for u in catalog if u['id'] in used)
    plan=dict(edits=edits,source_review=reviews,notes=[],editorial_policy=POLICY)
    coverage_report.validate_source_review(plan,catalog)
    return plan


def text_checks(plan,catalog):
    warnings=[]
    norm=lambda text:re.sub(r'[\s.,!?…]+','',text)
    scripts=editorial.assembled_scripts(plan,catalog)
    for script in scripts:
        seen={}
        for i,row in enumerate(script['selections']):
            text=norm(row['selected_text'])
            if len(text)>=12 and text in seen:
                warnings.append(dict(version=script['version'],type='exact_phrase_repeat',selection=i,previous_selection=seen[text],text=row['selected_text']))
            seen[text]=i
        if re.search(r'(ที่แบบ|ซึ่ง|เพราะว่า|ก็เลยคิดว่า|ถ้า|แต่ว่า)\s*$',script['assembled_text']):
            warnings.append(dict(version=script['version'],type='possible_incomplete_ending'))
        if re.search(r'แม่ง|เหี้ย|ไอ้สัส',script['assembled_text']):
            warnings.append(dict(version=script['version'],type='language_needs_review'))
    return dict(method='local text heuristics; not semantic approval or listening',warnings=warnings,
                meaning_review='pending',audio_review='pending',premiere_import='pending')


def run(catalog,folder,config,key,progress,project,silence):
    from engine import save
    payload=request_body(catalog,config['model'],config.get('effort','medium'),config.get('notes',''))
    request_hash=fingerprint(payload)
    save(folder/'editorial-policy.json',dict(policy=POLICY,max_text_calls=1,image_inspection=False,
        rules_file=RULE_FILE,rules_sha256=hashlib.sha256(payload['instructions'].encode()).hexdigest(),
        used_review_basis='computed selections; unused reasons from AI',meaning_review='pending'))
    (folder/'rules-used.txt').write_text(payload['instructions'],encoding='utf8')
    cache_file=folder/'fast-editorial-response.json'
    with progress.stage('planning','เรียบเรียงข้อความ 1 คำขอ: เลือกเทค เรียงเรื่อง ตัดใจความซ้ำ — ไม่ตรวจภาพ'):
        cached=json.loads(cache_file.read_text(encoding='utf8')) if cache_file.exists() else None
        if cached and cached.get('request_hash')==request_hash:
            raw=cached['response']
        else:
            path=folder/'api-usage.json'
            ledger=json.loads(path.read_text(encoding='utf8')) if path.exists() else []
            if any(row.get('request_hash')==request_hash and row.get('status')!='completed' for row in ledger):
                raise ValueError('คำขอนี้เคยไม่สำเร็จหรือไม่ทราบผล ไม่ส่งซ้ำอัตโนมัติ เริ่มงานใหม่เมื่อต้องการลองอีกครั้ง')
            ledger.append(dict(stage='fast_editorial',request_hash=request_hash,status='sent',usage=None));save(path,ledger)
            raw,record=plan_once(payload,key)
            ledger[-1].update(record,status='completed' if raw is not None else 'failed');save(path,ledger)
            if raw is None:raise RuntimeError(record['error'])
            save(cache_file,dict(request_hash=request_hash,response=raw))
        plan=normalize_plan(raw,catalog)
        resolve_plan(plan,catalog,silence,project)
    with progress.stage('text_checks','ตรวจบทและรหัสท่อนในเครื่อง — ไม่เรียก API เพิ่ม'):
        checks=text_checks(plan,catalog)
        save(folder/'text-checks.json',checks)
        save(folder/'assembled-scripts.json',editorial.assembled_scripts(plan,catalog))
        editorial.write_script_report(folder,plan,catalog)
        if checks['warnings']:progress(message=f'พบจุดข้อความที่ควรตรวจ {len(checks["warnings"])} จุด — เก็บรายงานไว้ ไม่แก้เสียงเอง')
    return plan
