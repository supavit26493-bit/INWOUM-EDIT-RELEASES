"""Executable inventory -> draft -> exact unused-span recovery -> final assembled audit.

Semantic decisions remain model judgments. Local code computes the bookkeeping and verifies
that each declared group/opportunity is actually accounted for. Never invent missing reviews.
"""
import copy
import hashlib
import json
import re
from planner import obj
import fast_editorial
import editorial
from fast_logic import resolve_plan

SPAN=obj(dict(unit_id=dict(type='string'),first_word=dict(type='integer'),last_word=dict(type='integer')))
INVENTORY=obj(dict(groups=dict(type='array',minItems=1,items=obj(dict(
    id=dict(type='string'),title=dict(type='string'),question=dict(type='string'),
    kind=dict(type='string',enum=['overview','focused','alternative']),unit_ids=dict(type='array',items=dict(type='string'))))),
    units=dict(type='array',items=obj(dict(unit_id=dict(type='string'),reason=dict(type='string'),
        classification=dict(type='string',enum=['usable','recoverable','uncertain','production']),
        opportunities=dict(type='array',items=obj(dict(first_word=dict(type='integer'),last_word=dict(type='integer'),
            group_ids=dict(type='array',items=dict(type='string')),purpose=dict(type='string'))))))),
    action_review=dict(type='array',items=obj(dict(unit_ids=dict(type='array',items=dict(type='string')),reason=dict(type='string'))))))


def edit_schema(audit=False):
    schema=copy.deepcopy(fast_editorial.SCHEMA)
    edit=schema['properties']['edits']['items']
    for key in ('content_group','distinct_body_summary'):
        edit['properties'][key]=dict(type='string');edit['required'].append(key)
    if audit:
        schema['properties']['group_audit']=dict(type='array',items=obj(dict(group_id=dict(type='string'),decision=dict(type='string',enum=['included','hold']),reason=dict(type='string'))))
        schema['properties']['span_audit']=dict(type='array',items=obj(dict(span_id=dict(type='string'),decision=dict(type='string',enum=['included','duplicate','unusable','hold']),reason=dict(type='string'))))
        schema['required']+=['group_audit','span_audit']
    return schema


def validate_inventory(raw,catalog,recover_opportunity_reasons=False,allow_unassigned=False):
    if recover_opportunity_reasons:
        raw=copy.deepcopy(raw)
        for row in raw.get('units',[]):
            purposes=[p['purpose'].strip() for p in row.get('opportunities',[]) if p.get('purpose','').strip()]
            if not row.get('reason','').strip() and purposes and row.get('classification') in ('usable','recoverable'):
                row['reason']='; '.join(dict.fromkeys(purposes))
                row['reason_source']='model_opportunity_purpose_recovered'
    ids={u['id']:u for u in catalog}
    groups=raw.get('groups',[]);gids=[g['id'] for g in groups]
    if not gids or len(gids)!=len(set(gids)):raise ValueError('ตารางกลุ่มสาระขาดหรือรหัสซ้ำ')
    for group in groups:
        if not group['question'].strip() or not group['unit_ids'] or set(group['unit_ids'])-set(ids):raise ValueError('กลุ่มสาระไม่มีเนื้อหารองรับ')
    rows=raw.get('units',[])
    if len(rows)!=len(ids) or {r['unit_id'] for r in rows}!=set(ids):raise ValueError('ยังสำรวจต้นฉบับไม่ครบทุกท่อน')
    for row in rows:
        unit=ids[row['unit_id']]
        if not row['reason'].strip():raise ValueError('ขาดเหตุผลสำรวจท่อน')
        for p in row['opportunities']:
            a,b=p['first_word'],p['last_word']
            if type(a)!=int or type(b)!=int or not 0<=a<=b<max(1,len(unit['words'])):raise ValueError('โอกาสใช้ฟุตอ้างขอบวลีผิด')
            if set(p['group_ids'])-set(gids) or not p['group_ids'] and not allow_unassigned:raise ValueError('โอกาสใช้ฟุตอ้างกลุ่มที่ไม่มี')
            if not p['group_ids'] and not p.get('purpose','').strip():raise ValueError('วลีที่พักไว้โดยไม่จัดกลุ่มต้องมีคำอธิบายจริง')
    for row in raw.get('action_review',[]):
        if set(row['unit_ids'])-set(ids):raise ValueError('คิวภาพสาธิตอ้างท่อนที่ไม่มี')
    return raw


def omissions(plan,catalog):
    """Compute omitted phrase runs, including unused parts of selected units."""
    chosen={u['id']:set() for u in catalog}
    for e in plan['edits']:
        for s in e['selections']:chosen[s['unit_id']].update(range(s['first_word'],s['last_word']+1))
    rows=[]
    for unit in catalog:
        words=unit['words'] or [unit];i=0
        while i<len(words):
            if i in chosen[unit['id']]:i+=1;continue
            a=i
            while i<len(words) and i not in chosen[unit['id']]:i+=1
            b=i-1
            rows.append(dict(span_id=f"{unit['id']}:{a}-{b}",unit_id=unit['id'],first_word=a,last_word=b,
                text=''.join(w['text'] for w in words[a:b+1]),start=words[a]['start'],end=words[b]['end']))
    return rows


def covered(plan,span):
    chosen=set()
    for edit in plan['edits']:
        for s in edit['selections']:
            if s['unit_id']==span['unit_id']:chosen.update(range(s['first_word'],s['last_word']+1))
    return any(i in chosen for i in range(span['first_word'],span['last_word']+1))


def validate_audit(raw,plan,inventory,spans):
    gids={g['id'] for g in inventory['groups']}
    actual={e.get('content_group') for e in plan['edits']}
    if actual-gids:raise ValueError('แผนอ้างกลุ่มสาระนอกตาราง')
    rows=raw.get('group_audit',[])
    if len(rows)!=len(gids) or {r['group_id'] for r in rows}!=gids:raise ValueError('ยังตรวจกลุ่มสาระไม่ครบ')
    for row in rows:
        if not row['reason'].strip() or (row['decision']=='included')!=(row['group_id'] in actual):raise ValueError('สถานะกลุ่มไม่ตรงคลิปที่เลือกจริง')
    ids={s['span_id'] for s in spans};rows=raw.get('span_audit',[])
    if len(rows)!=len(ids) or {r['span_id'] for r in rows}!=ids:raise ValueError('ยังตรวจวลีที่ตกหล่นไม่ครบ')
    byid={s['span_id']:s for s in spans}
    for row in rows:
        if not row['reason'].strip():raise ValueError('วลีที่ตกหล่นไม่มีเหตุผลเฉพาะ')
        # An inclusion must point to actual selected speech, not a model's assertion.
        if (row['decision']=='included')!=covered(plan,byid[row['span_id']]):raise ValueError('เหตุผลวลีที่ตกหล่นไม่ตรงการเลือกจริง')


def reconcile_span_audit(raw,spans):
    """Correct objective inclusion only; preserve the model's semantic judgment verbatim."""
    fixed=copy.deepcopy(raw);byid={s['span_id']:s for s in spans};changes=[]
    for row in fixed.get('span_audit',[]):
        span=byid.get(row['span_id'])
        if span is None:continue  # Normal validator rejects missing/unknown/duplicate IDs.
        selected=sorted({i for e in fixed['edits'] for s in e['selections'] if s['unit_id']==span['unit_id']
            for i in range(max(s['first_word'],span['first_word']),min(s['last_word'],span['last_word'])+1)})
        if (row['decision']=='included')==bool(selected):continue
        original=dict(row);row['model_decision']=row['decision'];row['model_reason']=row['reason']
        row['decision']='included' if selected else 'hold'
        row['reason']='โปรแกรมพบเลือกวลีบางส่วนจริง ดัชนี '+','.join(map(str,selected)) if selected else 'โปรแกรมไม่พบการเลือกวลีนี้จริง จัดพักรอตรวจเหตุผล'
        row['review_source']='computed_selection_reconciliation'
        row['selected_word_indices']=selected
        changes.append(dict(span_id=row['span_id'],original=original,computed_decision=row['decision'],selected_word_indices=selected))
    return fixed,changes


def reconcile_used_rows(raw):
    """Actual selections determine used status; retain displaced model rows in an audit."""
    fixed=copy.deepcopy(raw)
    used={s['unit_id'] for e in fixed['edits'] for s in e['selections']}
    displaced=[r for r in fixed.get('unused',[]) if r['unit_id'] in used]
    fixed['unused']=[r for r in fixed.get('unused',[]) if r['unit_id'] not in used]
    return fixed,displaced


def payload(base,data,schema,instruction):
    p=copy.deepcopy(base);p['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    p['instructions']+='\n'+instruction
    p['text']['format'].update(name='self_edit_workflow',schema=schema)
    return p


def normalize(raw,catalog,inventory):
    # Missing unused reasons may use an inventory reason, with its true origin recorded.
    fixed=copy.deepcopy(raw);used={s['unit_id'] for e in raw['edits'] for s in e['selections']}
    supplied={r['unit_id'] for r in raw.get('unused',[])}
    missing={u['id'] for u in catalog}-used-supplied
    basis={r['unit_id']:r for r in inventory['units']}
    for uid in sorted(missing):
        r=basis[uid];fixed['unused'].append(dict(unit_id=uid,decision='uncertain' if r['classification']=='uncertain' else 'alternative',reason=r['reason']))
    plan=fast_editorial.normalize_plan(fixed,catalog)
    for row in plan['source_review']:
        if row['unit_id'] in missing:row['review_source']='inventory_reason_recovered'
    gids={g['id'] for g in inventory['groups']}
    if any(e.get('content_group') not in gids or not e.get('distinct_body_summary','').strip() for e in plan['edits']):raise ValueError('คลิปขาดกลุ่มสาระหรือคำอธิบายเนื้อหา')
    return plan


def run(catalog,folder,config,key,progress,project,silence):
    from engine import save
    from story_editorial import EXTRA,call,checks
    import collection_review
    import full_contract
    polish=config.get('editorial_workflow')=='self-edit-v4'
    balanced=config.get('editorial_workflow') in ('self-edit-v3','self-edit-v4')
    max_calls=8 if balanced else 6
    max_repairs=3 if balanced else 2
    curated=config.get('editorial_workflow') in ('self-edit-v2','self-edit-v3','self-edit-v4')
    base=fast_editorial.request_body(catalog,config['model'],config.get('effort','medium'),config.get('notes',''))
    base['instructions']+=EXTRA;base['max_output_tokens']=32000
    source=json.loads(base['input'])
    repairs=0
    import selection_bounds
    compact=config.get('editorial_request_version')=='bounded-compact-v1'
    def make_payload(base,data,schema,instruction):
        if compact:
            data=selection_bounds.compact_data(data)
            schema=selection_bounds.bounded_schema(schema,catalog)
            instruction+='\nSource phrase indices are ZERO-BASED, inclusive. Each unit supplies last_phrase_index. Never exceed it. Candidate order matches current_plan.edits and assembled_scripts.'
        return payload(base,data,schema,instruction)
    def correct_bounds(raw,stage,request):
        nonlocal repairs
        bad=selection_bounds.issues(raw,catalog)
        if not bad:return raw
        if repairs>=max_repairs:raise ValueError('ครบจำนวนแก้คำตอบแล้ว: '+json.dumps(bad,ensure_ascii=False))
        repairs+=1
        progress(message='แก้เฉพาะขอบคำ '+str(len(bad))+' จุด · ใช้ข้อความเดิม ไม่เขียนแผนทั้งชุดใหม่')
        correction=selection_bounds.correction_request(request,raw,catalog,bad)
        answer=call(correction,folder,key,stage+'-bounds',max_calls=max_calls)
        fixed=selection_bounds.apply_corrections(raw,catalog,bad,answer)
        save(folder/(stage+'-bounds-applied.json'),dict(issues=bad,corrections=answer,response=fixed))
        return fixed
    def repair_request(request,raw,error):
        data=json.loads(request['input']);data.update(candidate_response=raw,validation_error=str(error))
        if isinstance(raw,dict) and isinstance(raw.get('edits'),list):
            try:
                candidate=normalize(raw,catalog,inventory)
                data['computed_selected_groups']=sorted({e['content_group'] for e in candidate['edits']})
                data['computed_span_included']={s['span_id']:covered(candidate,s) for s in data.get('omitted_spans',[])}
            except (ValueError,KeyError,TypeError,UnboundLocalError):pass
        fixed=copy.deepcopy(request);fixed['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
        fixed['instructions']+='\nREPAIR KNOWN SAVED RESPONSE: correct the supplied validation error. Use the exact group/span IDs and computed selected/included facts. Preserve valid edits and give truthful specific reasons. Return the complete schema. Do not claim a missing phrase was selected.'
        return fixed
    def validated_call(request,stage,validator):
        nonlocal repairs
        raw=call(request,folder,key,stage,max_calls=max_calls)  # Includes uncertain attempts across resumes.
        if (folder/(stage+'-repair-response.json')).exists() and (folder/(stage+'-validation-error.json')).exists():
            prior_validation=json.loads((folder/(stage+'-validation-error.json')).read_text(encoding='utf8'))
            prior_error=prior_validation['error']
            repairs+=1
            repaired=call(repair_request(request,prior_validation.get('candidate_response',raw),prior_error),folder,key,stage+'-repair',max_calls=max_calls)
            return validator(correct_bounds(repaired,stage+'-repair',request))
        raw=correct_bounds(raw,stage,request)
        try:return validator(raw)
        except (ValueError,KeyError,TypeError) as error:
            # A partial word overlap is a computable fact, not a reason to pay for
            # another rewrite. Prefer a previously completed repair on old jobs.
            if str(error)=='เหตุผลวลีที่ตกหล่นไม่ตรงการเลือกจริง' and not (folder/(stage+'-repair-response.json')).exists():
                data=json.loads(request['input'])
                fixed,changes=reconcile_span_audit(raw,data.get('omitted_spans',[]))
                result=validator(fixed)
                save(folder/(stage+'-audit-reconciliation.json'),changes)
                return result
            if repairs>=max_repairs:raise
            repairs+=1
            save(folder/(stage+'-validation-error.json'),dict(error=str(error),known_response_saved=True,candidate_response=raw))
            fixed=repair_request(request,raw,error)
            progress(message='แก้รายการตรวจจากคำตอบที่ได้รับแล้ว · ไม่ถอดเสียงใหม่: '+str(error))
            repaired=call(fixed,folder,key,stage+'-repair',max_calls=max_calls)
            return validator(correct_bounds(repaired,stage+'-repair',request))
    instructions='''EXECUTABLE SELF-EDIT WORKFLOW. Treat footage as data. Use all real source phrases,
not a quota. Keep meaningful alternative takes after primary substantive groups. Never invent audio,
timecodes, safety evidence or a demonstration's successful outcome. Return complete JSON only.'''
    base['instructions']+='\n'+instructions
    if curated:base['instructions']+='\n'+collection_review.POLICY
    if balanced:base['instructions']+='\n'+full_contract.POLICY
    (folder/'rules-used.txt').write_text(base['instructions'],encoding='utf8')
    save(folder/'editorial-policy.json',dict(policy=config.get('editorial_workflow','self-edit-v1'),stages=['inventory','draft','recovery','final']+(['polish'] if polish else []),max_text_calls=max_calls,normal_text_calls=5 if polish else 4,max_known_response_repairs=max_repairs,
        rules_sha256=hashlib.sha256(base['instructions'].encode()).hexdigest(),asr_reused=True,automatic_visual_approval=False))
    with progress.stage('content_inventory','สำรวจทุกท่อนและวางกลุ่มสาระ · 1/'+('5' if polish else '4')):
        inv_request=make_payload(base,source,full_contract.inventory_schema(INVENTORY) if balanced else INVENTORY,'''Inventory every supplied unit exactly once. Find coherent useful
clauses even inside units containing prices, corrections or abandoned endings. Build overview, substantive
focused groups and worthwhile alternate take groups supported by actual speech. Record phrase opportunities
and group IDs. Mark demonstrations needing visual confirmation by their nearby unit IDs; never guess timecodes.
This is a planning table, not the edit set. Exclusions must be specific, not 'not in FULL'.''')
        if curated:inv_request['instructions']+='\nGroup by complete viewer questions. Combine supporting features within coherent stories. Alternate takes are source options, not mandatory extra release groups.'
        def inventory_validator(raw):
            result=validate_inventory(raw,catalog,curated,balanced)
            return full_contract.validate_inventory(result,catalog) if balanced else result
        inventory=validated_call(inv_request,'self-inventory',inventory_validator);save(folder/'content-inventory.json',inventory)
    data=dict(source,inventory=inventory)
    with progress.stage('planning','ประกอบคลิปตามกลุ่มสาระและเทคทางเลือก · 2/'+('5' if polish else '4')):
        draft_request=make_payload(base,data,full_contract.edit_schema(edit_schema()) if balanced else edit_schema(),'''Build the full practical edit collection from the inventory.
Each content_group must be an inventory ID. distinct_body_summary describes actual body content. Focused clips
need only their own relevant explanation/context/conditions; do not paste the FULL feature bundle into all.
Return all supported worthwhile stories/takes, not only a small representative sample. unused covers exactly
units with no selection. Refer to supplied phrase indices and existing protected action units only.''')
        if curated:draft_request['instructions']+='\nPrefer complete focused stories over tiny isolated features. Choose the strongest interchangeable take; reserve others as alternatives. Do not force one output per inventory group.'
        def draft_validator(raw):
            if balanced:raw,_=reconcile_used_rows(raw)
            plan=normalize(raw,catalog,inventory);resolved=resolve_plan(plan,catalog,silence,project)
            if balanced:
                report=full_contract.validate(plan,inventory,catalog,resolved)
                save(folder/'draft-full-coverage.json',report)
            return plan
        plan=validated_call(draft_request,'self-draft',draft_validator)
        save(folder/'self-draft-plan.json',plan)
    for stage,title in [('recovery','ย้อนตรวจทุกวลีที่ตกหล่นและเพิ่มคลิป'),('final','ตรวจบทประกอบจริงทุกคลิปและเกลารอยต่อ')]:
        spans=omissions(plan,catalog)
        save(folder/f'{stage}-omitted-spans.json',spans)
        data=dict(source,inventory=inventory,current_plan=plan,assembled_scripts=editorial.assembled_scripts(plan,catalog),
            omitted_spans=spans,local_checks=checks(plan,catalog))
        previous_plan=copy.deepcopy(plan)
        if curated:
            collection=collection_review.context(plan,catalog,resolve_plan(plan,catalog,silence,project))
            if balanced:
                for i,row in enumerate(collection['candidates']):row['edit_role']=plan['edits'][i]['edit_role']
            data['collection_evidence']=collection
        instruction='''Return the COMPLETE revised collection, preserving useful existing stories while fixing bad
joins and adding supported content. Read every assembled script in playback order: repeated premises, dangling
subjects, changing referents (e.g. single-use cartridge vs reusable product), negations, missing conditions,
production talk and unfinished endings. Recover meaningful clauses and new stories from exact omitted_spans.
Keep qualitative/comparative prices and approved banknote counts, preserve 'not' and uncertainty. Never discard
a whole unit because only one clause is bad. No meaningless permutations to increase count.
group_audit must cover every inventory group once: included only if an edit with that exact content_group exists,
otherwise hold with a specific reason. span_audit must cover every INPUT omitted span once: included if any word
from it is now selected, otherwise duplicate/unusable/hold with an exact source-based reason. Used parts may leave
other words omitted; do not claim those residual words were used. Do not pretend images or listening are verified.'''
        with progress.stage('editorial_'+stage,title+(' · 3/' if stage=='recovery' else ' · 4/')+('5' if polish else '4')):
            schema=edit_schema(audit=True)
            if curated:
                instruction+='\n'+collection_review.POLICY
                if stage=='recovery':instruction+='\nRecover new DETAILS into the relevant complete story before adding another release. Do not protect redundant drafts.'
                else:
                    schema=collection_review.extend_schema(schema)
                    instruction+='''\nFINAL COLLECTION SELECTION: prioritize pruning and meaning review over expansion.
Return only the main publishable complete edits. Assess each collection_evidence candidate exactly once in
candidate_review: keep, merge into a target output, alternate (retained in library, not another published edit),
or drop. target_edit_index is zero-based in your OUTPUT edits; use -1 for no corresponding release.
For each OUTPUT edit give release_review with zero-based edit_index, standalone_reason, distinct_reason,
short_clip_reason (required substantively below 10 seconds; empty otherwise is fine), boundary_review,
claims_review. Adjacent candidate IDs or different group names do not establish different meaning.
Compare price, comfort, use/reuse, signals and audience stories semantically. Remove or combine thin
interchangeable feature fragments; preserve genuinely new technical details in the relevant story.
Do not publish a weak claim or an unresolved product reference merely to use footage. No need to keep
every draft. Preserve useful footage by merging, not by publishing all takes. Review source context,
negations, corrections and complete openings/endings. Thai titles/reasons are mandatory.'''
            if balanced:
                schema=full_contract.edit_schema(schema)
                instruction+='\n'+full_contract.POLICY
                instruction+='\nBefore pruning, verify FULL includes every scope=full requirement, all protected demos and product identity. FULL overlap with focused edits is expected: never prune FULL for this overlap. Preserve every scope=collection requirement in at least one release. Rebuild a substantial FULL first, then assess focused stories.'
            request=make_payload(base,data,schema,instruction)
            def audit_validator(raw):
                if curated:
                    raw,used_rows=reconcile_used_rows(raw)
                    raw,span_rows=reconcile_span_audit(raw,spans)
                    save(folder/(stage+'-selection-bookkeeping.json'),dict(
                        basis='computed selections; raw API response preserved',used_rows_removed_from_unused=used_rows,
                        span_reconciliation=span_rows))
                candidate=normalize(raw,catalog,inventory)
                validate_audit(raw,candidate,inventory,spans)
                resolved=resolve_plan(candidate,catalog,silence,project)
                if balanced:
                    full_report=full_contract.validate(candidate,inventory,catalog,resolved,final=stage=='final')
                    save(folder/(stage+'-full-coverage.json'),full_report)
                if curated and stage=='final':
                    raw,recovered=collection_review.recover_short_reasons(raw,resolved)
                    save(folder/'short-reason-recovery.json',recovered)
                    collection_review.validate(raw,candidate,collection,resolved)
                    if balanced:full_contract.validate_merges(raw,previous_plan,candidate)
                return candidate,raw
            new,raw=validated_call(request,'self-'+stage,audit_validator)
            save(folder/(stage+'-audit.json'),dict(group_audit=raw['group_audit'],span_audit=raw['span_audit']))
            save(folder/(stage+'-plan.json'),new);plan=new
            if curated and stage=='final':collection_review.write_report(folder,raw,collection,previous_plan,plan,save)
            if balanced and stage=='final':full_contract.write_report(folder,full_contract.validate(plan,inventory,catalog,resolve_plan(plan,catalog,silence,project),final=True),save)
    if polish:
        previous_plan=copy.deepcopy(plan)
        collection=collection_review.context(plan,catalog,resolve_plan(plan,catalog,silence,project))
        for i,row in enumerate(collection['candidates']):row['edit_role']=plan['edits'][i]['edit_role']
        data=dict(source,inventory=inventory,current_plan=plan,assembled_scripts=editorial.assembled_scripts(plan,catalog),
            omitted_spans=omissions(plan,catalog),collection_evidence=collection,local_checks=checks(plan,catalog))
        instruction=collection_review.POLICY+'\n'+full_contract.POLICY+'''
FINAL POLISH: the FULL contract and collection now exist. Read the actual complete scripts aloud mentally,
checking every selected range's first/last words against its neighboring source words. Do not pretend listening.
Remove abandoned premises when the speaker's completed correction already states the intended fact with its
conditions. Do not preserve an unnecessary 'maybe single use' followed by 'reusable' just to keep more words.
Trim dangling next-clause heads (มัน, อันนี้, ถ้า, เพราะว่ามัน) and stutters at joins. The contract's boundary
fillers/next-clause heads are not required semantic evidence. NEVER trim ไม่, conditions or uncertainty.
Preserve FULL and all its substantive coverage <=180s. Keep its demonstrations. Improve the order of setup,
demonstration and result. Do not rebuild a tiny summary. Related complete focused material may form substantial
long_focused versions: e.g. cause/context plus explanation/conditions that answer one viewer question. Do not
reduce everything to isolated features or create long clips by padding/repetition. Retain only useful distinct
focused stories; a longer coherent topic can replace several thin fragments. Recover new usable details from
omitted_spans clause by clause, including within units mixing useful content with bad endings; no usage quota.
Do not invent specs or revive held unsafe assertions. Use full source options for referents and preserve caveats.
Return COMPLETE edits and unused reasons plus candidate_review and release_review. Review every INPUT candidate
once: keep/merge/alternate/drop with a real zero-based OUTPUT target or -1. A merge must retain distinctive source
speech in its target; shared generic intros do not establish a merge. For EVERY OUTPUT give zero-based edit_index,
standalone_reason, distinct_reason, short_clip_reason, boundary_review, claims_review. No group/span audit tables
in this pass: focus on meaning, complete stories and clean joins. Thai names/reasons. FULL first.'''
        request=make_payload(base,data,full_contract.edit_schema(collection_review.extend_schema(edit_schema())),instruction)
        def polish_validator(raw):
            raw,displaced=reconcile_used_rows(raw)
            candidate=normalize(raw,catalog,inventory)
            resolved=resolve_plan(candidate,catalog,silence,project)
            full_report=full_contract.validate(candidate,inventory,catalog,resolved,final=True)
            raw,recovered=collection_review.recover_short_reasons(raw,resolved)
            collection_review.validate(raw,candidate,collection,resolved)
            full_contract.validate_merges(raw,previous_plan,candidate)
            save(folder/'polish-bookkeeping.json',dict(displaced_unused=displaced,recovered_short_reasons=recovered))
            return candidate,raw,full_report
        with progress.stage('collection_polish','เกลารอยต่อ FULL และประกอบเรื่องยาว · 5/5'):
            plan,raw,full_report=validated_call(request,'self-polish',polish_validator)
        save(folder/'polish-plan.json',plan)
        for name in ('collection-review.json','COLLECTION-REVIEW.md'):
            file=folder/name
            if file.exists():(folder/('PRE-POLISH-'+name)).write_bytes(file.read_bytes())
        collection_review.write_report(folder,raw,collection,previous_plan,plan,save)
        full_contract.write_report(folder,full_report,save)
    unresolved=omissions(plan,catalog)
    actions=inventory['action_review']
    report=checks(plan,catalog);report.update(meaning_review=('five-stage' if polish else 'four-stage')+' AI text workflow completed; listening pending',visual_review='pending' if actions else 'not inspected')
    save(folder/'text-checks.json',report);save(folder/'remaining-omitted-spans.json',unresolved)
    save(folder/'action-review-queue.json',dict(requests=actions,existing_protected=[u['id'] for u in catalog if u['protected']],automatic_approval=False))
    save(folder/'assembled-scripts.json',editorial.assembled_scripts(plan,catalog));editorial.write_script_report(folder,plan,catalog)
    summary=['# กระบวนการเกลาอัตโนมัติ','', 'ครบ4ขั้น: สำรวจทุกท่อน → ประกอบตามกลุ่ม → เก็บตกวลีจริง → ตรวจบทประกอบ','',
        f"กลุ่มสาระ {len(inventory['groups'])}; คลิป {len(plan['edits'])}; วลีเหลือ {len(unresolved)}; คิวภาพสาธิต {len(actions)}",'',
        'มีเหตุผลรายกลุ่มและรายวลีใน recovery-audit.json/final-audit.json ตัวตรวจตรวจความครบและการใช้จริง ไม่รับรองการตัดสินเชิงความหมาย ภาพ/เสียง/Premiereยังแยกตรวจ','']
    for row in actions:summary+=['- รอตรวจภาพ '+','.join(row['unit_ids'])+': '+row['reason']]
    if polish:summary[2]='ครบ5ขั้น: สำรวจ → ประกอบ FULL+เรื่องย่อย → เก็บตก → คัดชุด → เกลารอยต่อและเรื่องยาว'
    (folder/'SELF-EDIT-WORKFLOW.md').write_text('\n'.join(summary),encoding='utf8')
    plan['editorial_policy']=config.get('editorial_workflow','self-edit-v1')
    return plan
