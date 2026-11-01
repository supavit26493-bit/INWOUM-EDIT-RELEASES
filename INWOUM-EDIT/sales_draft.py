"""Assemble only the user's selected story map; stop before XML export."""
import copy,hashlib,json
from pathlib import Path
import editorial,fast_editorial,selection_bounds
from fast_logic import resolve_plan
from planner import obj
from parallel_requests import call
import runtime_contract as runtime
import quality_v8

def refresh_export_review(folder,plan,catalog,resolved,duration):
    """Reports must follow the actual rebuilt plan, including local UI edits."""
    from engine import save
    result=inspect(plan,catalog,resolved,duration)
    save(folder/'draft-quality.json',result)
    save(folder/'assembled-scripts.json',editorial.assembled_scripts(plan,catalog))
    editorial.write_script_report(folder,plan,catalog)
    lines=['# ตรวจแผนที่ส่งออกล่าสุด','',f"ส่งออก {len(plan['edits'])} เวอร์ชัน · ไม่ใช้โควตาเปอร์เซ็นต์",'',
        'ตรวจจากช่วงคำที่เลือกจริง ไม่ใช่การฟังเสียงหรือการเปิดตรวจในPremiere','',
        '## จุดตรวจอัตโนมัติ','']
    lines+=['- '+json.dumps(r,ensure_ascii=False) for r in result['findings']] or ['ไม่พบข้อสังเกตจากการตรวจข้อความ/ช่วงคัทในเครื่อง']
    held=folder/'draft-deferred.json'
    if held.exists():lines+=['','## เวอร์ชันที่พักไว้','']+['- '+r['map_id']+': '+r['reason'] for r in json.loads(held.read_text('utf8'))]
    (folder/'DRAFT-REVIEW.md').write_text('\n'.join(lines),'utf8')
    return result

def draft_schema(story,deferred):
    ids=['FULL']+[v['id'] for v in story['versions']]+[r['id'] for r in story.get('reserve_takes',[])]
    if set(deferred)-set(ids) or 'FULL' in deferred:raise ValueError('พักเวอร์ชันผิดรหัสหรือพักFULL')
    fields={mid:obj(dict(name=dict(type='string'),purpose=dict(type='string'),selections=dict(type='array',minItems=1,items=obj(dict(unit_id=dict(type='string'),first_word=dict(type='integer'),last_word=dict(type='integer')))))) for mid in ids if mid not in deferred}
    return obj(dict(edits=obj(fields),unused=copy.deepcopy(fast_editorial.SCHEMA['properties']['unused']),review_note=dict(type='string')))

def normalize_draft(raw,catalog,story,segments,deferred):
    required=['FULL']+[v['id'] for v in story['versions'] if v['id'] not in deferred]+[r['id'] for r in story.get('reserve_takes',[]) if r['id'] not in deferred]
    kinds={v['id']:v['kind'] for v in story['versions']}
    kinds.update({r['id']:'reserve' for r in story.get('reserve_takes',[])})
    if not isinstance(raw.get('edits'),dict) or set(raw['edits'])!=set(required):raise ValueError('ต้องประกอบทุกเวอร์ชันที่เลือก ห้ามพักเพราะอยู่ในFULLแล้ว')
    edits=[dict(raw['edits'][mid],map_id=mid,edit_role='FULL' if mid=='FULL' else ('reserve' if kinds[mid]=='reserve' else ('focused' if kinds[mid] in ('focused','combined') else 'alternative'))) for mid in required]
    reserve_titles={r['id']:r['title'] for r in story.get('reserve_takes',[])}
    for e in edits:
        if e['map_id'] in reserve_titles:e['reserve_title']=reserve_titles[e['map_id']]
    reserve_rows={r['id']:r for r in story.get('reserve_takes',[])}
    for e in edits:
        row=reserve_rows.get(e['map_id'])
        if row and all(k in row for k in ('opening_quote','payoff_quote','recovery_quote','sales_angle')):
            e['reserve_sales_evidence']={k:row[k] for k in ('opening_quote','payoff_quote','recovery_quote','sales_angle')}
    used={s['unit_id'] for e in edits for s in e['selections']};known={u['id'] for u in catalog}
    supplied=copy.deepcopy(raw['unused']);seen={r['unit_id'] for r in supplied};inherited={}
    if len(seen)!=len(supplied) or seen-known:raise ValueError('เหตุผลท่อนพักซ้ำหรือไม่ตรงการเลือก')
    rows=[];partial=[];units={u['id']:u for u in catalog}
    for row in supplied:
        uid=row['unit_id']
        if row.get('decision') not in fast_editorial.UNUSED_DECISIONS or not isinstance(row.get('reason'),str) or not row['reason'].strip():
            raise ValueError('รายการท่อนที่ไม่ใช้ไม่ตรงแผนหรือเหตุผลไม่ครบ')
        if uid not in used:rows.append(row);continue
        # The AI sometimes explains omitted phrases in an otherwise used unit.
        # Preserve every selection; keep this explanation separate from whole-unit unused.
        count=max(1,len(units[uid]['words']));selected=set()
        for edit in edits:
            for selection in edit['selections']:
                if selection['unit_id']!=uid:continue
                a=selection['first_word'];b=selection['last_word']
                if type(a)!=int or type(b)!=int or not 0<=a<=b<count:
                    raise ValueError('รหัสท่อนหรือขอบ phrase ไม่ถูกต้อง')
                selected.update(range(a,b+1))
        omitted=sorted(set(range(count))-selected)
        if not omitted:raise ValueError('ระบุท่อนที่ใช้ครบแล้วว่าไม่ใช้')
        partial.append(dict(**row,scope='omitted_phrases_in_partially_used_unit',
                            omitted_phrase_indices=omitted,reason_source='ai_response'))
    reviews={r['segment_id']:r for r in story['segment_review']}
    decision={'unusable':'production','pending':'uncertain','mixed':'alternative','usable':'alternative','alternative':'alternative'}
    for u in catalog:
        if u['id'] in used or u['id'] in seen:continue
        linked=[s for s in segments if s['start']<u['end'] and s['end']>u['start']]
        prior=[reviews[s['id']] for s in linked if s['id'] in reviews]
        reason='; '.join(dict.fromkeys(r['reason'] for r in prior)) or 'ยังไม่มีการเลือก รอตรวจเหตุผลเฉพาะช่วง'
        rows.append(dict(unit_id=u['id'],decision=decision[prior[0]['disposition']] if prior else 'alternative',reason=reason))
        inherited[u['id']]=[s['id'] for s in linked]
    plan=fast_editorial.normalize_plan(dict(edits=edits,unused=rows),catalog)
    plan['partial_unit_reviews']=partial
    partial_by_id={r['unit_id']:r for r in partial}
    for r in plan['source_review']:
        if r['unit_id'] in partial_by_id:
            r['partial_omission_review']=copy.deepcopy(partial_by_id[r['unit_id']])
        if r['unit_id'] in inherited:r.update(review_source='inherited_story_map',source_segments=inherited[r['unit_id']])
    return plan,inherited

def inspect(plan,catalog,resolved,duration):
    scripts=editorial.assembled_scripts(plan,catalog);issues=[];units={u['id']:u for u in catalog}
    for i,(edit,script,cut) in enumerate(zip(plan['edits'],scripts,resolved)):
        for finding in quality_v8.price_findings(script['assembled_text']):issues.append(dict(edit_index=i,**finding))
        first=edit['selections'][0];u=units[first['unit_id']]
        token=(u['words'][first['first_word']]['text'] if u['words'] else u['text']).strip()
        if token in {'แต่','แต่ว่า','ก็','แบบ','เอ่อ','อ่า','เออ'}:issues.append(dict(edit_index=i,issue='forbidden_opener',text=token))
        issues += [dict(edit_index=i,**r) for r in quality_v8.cut_findings(cut['ranges'])]
        fillers=sum(w['text'].strip() in {'แบบ','เอ่อ','อ่า','เออ'} for s in edit['selections'] for w in units[s['unit_id']]['words'][s['first_word']:s['last_word']+1])
        if fillers>3:issues.append(dict(edit_index=i,issue='fillers_need_context_review',count=fillers))
    usage=quality_v8.coverage(duration,[(r['start'],r['end']) for e in resolved for r in e['ranges']])
    return dict(findings=issues,coverage=usage,audio_review='pending',visual_review='pending',meaning_review='AI draft; human checkpoint',usage_quota=None)

def repair_draft_bounds(raw,catalog,folder,key,request,progress):
    """Repair only invalid phrase indices, using one cached, source-aware AI request."""
    from engine import save
    if not isinstance(raw,dict) or not isinstance(raw.get('edits'),dict):return raw
    ids=list(raw['edits'])
    candidate=dict(edits=[raw['edits'][mid] for mid in ids])
    bad=selection_bounds.issues(candidate,catalog)
    if not bad:return raw
    correction=selection_bounds.correction_request(request,candidate,catalog,bad)
    progress(message=f'ตรวจพบขอบคำไม่ถูกต้อง {len(bad)} จุด · ให้ AI แก้เฉพาะจุดจากคำต้นฉบับ · เพิ่มได้ 1 API · ไม่ถอดเสียงใหม่')
    answer=call(correction,folder,key,'approved-map-draft-bounds-v1',max_calls=3)
    fixed=selection_bounds.apply_corrections(candidate,catalog,bad,answer)
    result=copy.deepcopy(raw)
    for mid,edit in zip(ids,fixed['edits']):result['edits'][mid]=edit
    save(folder/'draft-bounds-repair.json',dict(policy='source-aware-bounds-v1',invalid=bad,corrections=answer,
         changes_only='first_word/last_word of invalid selections',original_response_preserved=True))
    return result


def run(catalog,folder,config,key,progress,project,silence):
    from engine import save
    root=folder.parent.resolve();parent=(root/config['approved_story_map_job']).resolve()
    if not parent.is_relative_to(root):raise ValueError('แผนต้องอยู่ใน jobs')
    source=json.loads((parent/'grouping-source.json').read_text('utf8'));story=json.loads((parent/'content-groups.json').read_text('utf8'))
    if hashlib.sha256((folder/'transcript.json').read_bytes()).hexdigest()!=source['transcript_sha256']:raise ValueError('ข้อความไม่ตรงแผนที่เลือก ห้ามใช้แผนข้ามต้นฉบับ')
    if (parent/'rules-used.txt').read_text('utf8') not in (runtime.rules(),runtime.rules_for(config)):raise ValueError('กฎเปลี่ยนจากแผนที่เลือก ให้ตรวจแผนใหม่ก่อน')
    deferred=config.get('defer_map_ids',{})
    if not isinstance(deferred,dict) or any(not isinstance(r,str) or not r.strip() for r in deferred.values()):raise ValueError('รายการพักต้องมีเหตุผลจากผู้ตรวจ')
    schema=draft_schema(story,deferred)
    data=dict(job_instructions=config.get('notes',''),units=[dict(id=u['id'],
        phrases=[w['text'] for w in (u['words'] or [u])],
        seconds=[round(w['end']-w['start'],2) for w in (u['words'] or [u])],
        protected=u['protected']) for u in catalog])
    data.update(approved_story_map={k:story[k] for k in ('groups','hooks','full_hook_id','full_outline','versions','continuity_links')},
        source_bindings=[dict(id=s['id'],unit_ids=[u['id'] for u in catalog if u['start']<s['end'] and u['end']>s['start']]) for s in source['segments']])
    for unit in data['units']:unit['phrases']={str(i):text for i,text in enumerate(unit['phrases'])}
    import sales_reserve
    if sales_reserve.enabled(config):
        data['approved_story_map']['reserve_takes']=sales_reserve.ordered_rows(story,source['segments'])
        if config.get('sales_style')!='sales_2_1':
            data['reserve_assembly']='Assemble main edits first. Each reserve is a complete sales clip <=180s. Keep exact opening_quote, recovery_quote and payoff_quote, context, benefit and exclusions.'
    data['reviewer_deferred']=deferred
    previous=folder/'approved-map-draft-response.json'
    if previous.exists():
        prior=json.loads(previous.read_text('utf8'))['response']
        data['repair_findings']=dict(previous_deferred=prior.get('deferred_versions',[]),
            instruction='Earlier response wrongly deferred selected stories because their content appears in FULL, skipped useful parts of mixed-price units, and cut incomplete clauses. Assemble all required keys; keep banknote counts and negation. Use explicit phrase indices, not guessed offsets.')
    schema=selection_bounds.bounded_schema(schema,catalog)
    import sales_continuity
    req=dict(model=config['model'],store=False,max_output_tokens=24000,reasoning=dict(effort=config.get('draft_effort',config.get('effort','medium'))),
        instructions=runtime.rules_for(config)+'''\nSTEP: assemble the approved map using indexed source phrases, not a new inventory.
edits must contain EVERY required map key. Only reviewer_deferred is held back by the program.
source_bindings maps T segments to candidate units; choose exact phrase indices by their actual speech.
opening_quote is source evidence; trim_note is implemented through selections. Return short reasons only
for new unused decisions; unchanged source reviews are inherited locally.
This is a text draft checkpoint; no XML or listening approval.''',
        input=json.dumps(data,ensure_ascii=False,separators=(',',':')),
        text=dict(format=dict(type='json_schema',name='approved_map_draft',strict=True,schema=schema)))
    if sales_continuity.enabled(config):req['instructions']+=sales_continuity.instructions_for(config)
    import episode_continuity
    event_mode=episode_continuity.enabled(config)
    if event_mode:
        req['instructions']+='\n'+episode_continuity.INSTRUCTIONS
        req['text']['format']['schema']=episode_continuity.schema(req['text']['format']['schema'],catalog)
    batched=config.get('content_mode','sales')=='sales' and config.get('sales_style')in ('sales_2_1','sales_2_2')
    with progress.stage('draft','2.1 ประกอบเป็นชุดตามขนาด · จำกัดงบรวม $0.50 · เก็บผลทำต่อ' if batched else ('Default: เรียบเรียงเป็นวลีต่อเนื่อง · 1 API' if sales_continuity.enabled(config) else 'Original: ประกอบบทจากแผนเดิม · 1 API')):
        req=runtime.cached_editorial_request(req,folder,'approved-map-draft-v2')
        import batched_sales_draft
        # Preserve completed legacy responses; new 2.1 assemblies use per-clip checkpoints.
        if batched_sales_draft.enabled(config) and not (folder/'approved-map-draft-v2-response.json').exists():
            raw=batched_sales_draft.run(req,folder,key,progress)
        else:raw=call(req,folder,key,'approved-map-draft-v2',max_calls=2)
        raw=repair_draft_bounds(raw,catalog,folder,key,req,progress)
        import optimize21
        optimized=optimize21.enabled(config) and event_mode
        production_changes=[]
        if optimized:raw,production_changes=optimize21.prepare_raw(raw,catalog)
        if event_mode:raw=episode_continuity.ensure(raw,catalog,folder,key,req,progress)
        plan,inherited=normalize_draft(raw,catalog,story,source['segments'],deferred)
        if config.get('sales_style') in ('natural_v2','sales_2_1','sales_2_2'):plan['cut_style']=config['sales_style']
        if event_mode:episode_continuity.attach(plan,raw)
        if sales_continuity.enabled(config):
            import production_cleanup
            if not optimized:plan,production_changes=production_cleanup.clean(plan,catalog)
            save(folder/'production-cleanup.json',dict(changes=production_changes,extra_api_calls=0))
        save(folder/'partial-unused-reasons.json',plan['partial_unit_reviews'])
        import speech_cleanup
        save(folder/'api-plan-before-cleanup.json',plan)
        if sales_continuity.enabled(config):
            if optimized:continuity_issues=[]
            else:plan,continuity_issues=sales_continuity.repair(plan,catalog,folder,key,req,progress)
            plan,cleanup=sales_continuity.clean(plan,catalog)
            cleanup_policy=sales_continuity.POLICY
        else:
            plan,cleanup=speech_cleanup.clean(plan,catalog);cleanup_policy=speech_cleanup.POLICY;continuity_issues=[]
        if optimized:
            plan=optimize21.hold_pending(plan,raw,folder)
            if sales_continuity.findings(plan,catalog):raise ValueError('ยังมีคำโดดหลังเกลารอยต่อ จึงยังไม่ส่งออก')
        if event_mode and episode_continuity.findings(plan,catalog):raise ValueError('การเกลาคำทำให้บริบทเหตุการณ์ไม่ครบ จึงยังไม่ส่งออก')
        if event_mode:episode_continuity.record(plan,catalog,folder)
        save(folder/'speech-cleanup.json',dict(policy=cleanup_policy,changes=cleanup,manual_edits=False,extra_api_calls=1 if continuity_issues else 0))
        if sales_reserve.enabled(config):
            sales_reserve.label_plan(plan,catalog)
            reserve_audit=sales_reserve.audit_plan(plan,catalog)
            save(folder/'reserve-validation.json',reserve_audit)
            if reserve_audit['invalid']:
                labels=', '.join(str(r['map_id']) for r in reserve_audit['invalid'])
                raise ValueError('เทคสำรองประกอบไม่ครบ: '+labels+' · ดู reserve-validation.json · เก็บคำตอบเดิมไว้แล้ว')
            sales_reserve.validate_plan(plan,catalog)
            save(folder/'reserve-takes.json',dict(policy=sales_reserve.POLICY,edits=[e for e in plan['edits'] if e.get('edit_role')=='reserve'],additional_planning_calls=0))
        import hook_library
        if hook_library.enabled(config):
            save(folder/'plan.json',plan)  # Preserve completed main assembly before an optional API failure.
            plan=hook_library.run(plan,catalog,folder,config,key,progress)
        resolved=resolve_plan(plan,catalog,silence,project)
        audit=inspect(plan,catalog,resolved,project['duration']/50)
        save(folder/'plan.json',plan);save(folder/'draft-quality.json',audit)
        save(folder/'approved-map.json',dict(source_job=parent.name,source_hash=source['transcript_sha256'],map=story))
        held=[dict(map_id=mid,reason=reason) for mid,reason in deferred.items()]
        save(folder/'draft-deferred.json',held);save(folder/'inherited-unused-reasons.json',inherited)
        save(folder/'assembled-scripts.json',editorial.assembled_scripts(plan,catalog));editorial.write_script_report(folder,plan,catalog)
        (folder/'rules-used.txt').write_text(runtime.rules_for(config),'utf8')
        lines=['# ร่างตามแผนที่เลือก · หยุดก่อนXML','',raw['review_note'],'',
            f"ใช้ต้นฉบับไม่ซ้ำ {audit['coverage']['unique_used_seconds']}s · ไม่มีโควตาเปอร์เซ็นต์",'','## จุดที่ต้องตรวจ','']
        lines += ['- '+json.dumps(r,ensure_ascii=False) for r in audit['findings']]
        lines += ['','## เวอร์ชันที่ผู้ตรวจพักไว้','']+['- '+r['map_id']+': '+r['reason'] for r in held]
        (folder/'DRAFT-REVIEW.md').write_text('\n'.join(lines),'utf8')
    next_step='กำลังส่งออก XML อัตโนมัติ' if config.get('auto_export') else 'ยังไม่สร้างXML'
    progress(stage='assembled' if config.get('auto_export') else 'done',message=f"ร่าง {len(plan['edits'])} เวอร์ชันตามแผน · {len(audit['findings'])} ข้อสังเกต · {next_step}",result=dict(draft_review=True,planned_versions=len(plan['edits'])))
    return plan
