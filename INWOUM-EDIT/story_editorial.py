"""Two bounded text passes: edit, then assembled-script review and recovery."""
import hashlib
import copy
import json
import re
import editorial
import fast_editorial
from planner import fingerprint, plan_once
from fast_logic import resolve_plan

POLICY='story-v4'
EXTRA='''
LATEST USER REQUEST — MAXIMIZE SUPPORTED EDIT OPTIONS
The user explicitly prefers many useful versions and does not want a convenience cap on their
number. After covering content groups, continue recovering valid alternate hooks, clear and
energetic takes, value/price angles and other supported stories from the whole recording.
Do not cap the collection merely because a full edit or several groups already exist.
Keep accepted existing versions when expanding; add or repair in a separate collection.
Comparison of price, qualitative price/value, approved banknote-count expressions and a
spoken budget constraint are usable. A nearby explicit baht/monetary tier does not ban them.
Preserve 'not cheap but cheaper than...' rather than cutting the negative into 'very cheap'.
Read long unused spans clause by clause: distinguish a different usable story/take, a failed
or unresolved demonstration, uncertain wording and actual crew talk. Recover coherent clauses
before abandoned endings where real phrase boundaries permit. Do not use generic 'duplicate'
or 'incomplete' to dismiss useful new information. Do not manufacture permutations or invent
an outcome to inflate the count: each option must be intelligible and supported by footage.

CONTENT GROUPS BEFORE HOOK VARIANTS — USER CLARIFICATION 20 OCTOBER 2026
First inventory the whole footage by substantive information, then build stories by content
group. A useful collection can contain a complete overview plus focused groups such as a
mechanism/demo, use case, maintenance/reuse, help signals, or audience problem, when supported.
These are examples, never a fixed clip count or mandatory template for another product.
A focused story selects only the information needed to answer its own question. It need not
repeat the overview's whole feature bundle, product introduction, maintenance ending or CTA.
Open directly with a feature or demonstration when spoken/visual context identifies its meaning.
Ensure each group has enough explanation, conditions and a finished thought; short fragments
alone are not sufficient, and no minimum duration or universal intro/outro is required.
Only after these substantive groups are covered consider useful hook-only alternate takes.
Shared body material is allowed, not a reason to make every body the same. Do not mistake
permission for shared bodies for an instruction to produce mainly identical-body variants.
For every edit return content_group and distinct_body_summary naming its actual substantive
topic and what its body answers. These are planning metadata, never invented dialogue.

PHRASE EDITING AND COVERAGE
Thai phrases now have real lexical/alignment boundaries. Their boundaries are selectable,
not necessarily sentence endings. Read neighboring phrases and units as continuous speech.
Remove countdowns and abandoned words by selecting the useful suffix, prefix or separated
non-overlapping ranges. Never discard a whole take solely because one phrase is production chatter.
Keep negatives/corrections and their explanation; never turn a caveat into the opposite claim.
Do not confuse a bag zipper lock with a strap buckle lock, or lightweight with small size.
Never leave "look at the zipper" followed by an unrelated feature without its explanation.
Read every late take. Develop distinct complete viewer stories and usable prepared hook
alternatives. Shared body/endings across versions are allowed. A full essentials version,
focused mechanism/use-case versions and genuinely useful alternate hook/take versions are
options, not a mandatory template or numeric quota. Do not stop just because one full edit exists.
duplicate must cite an actually selected better take; a different new detail is not duplicate.
Clean speech can be joined across units. Incomplete unit endings are not a reason to discard
their complete useful clauses. Do not insert isolated Thai syllables. Make each chosen range
an intelligible phrase and each assembled story complete. Review facts as spoken opinions,
never invent fact checks or new speech. Strong sales fear/travel hooks remain allowed.

ACCEPTED USER WORKFLOW (applies to every product, not only the reference footage)
When asked to add hooks, deliver hooks with a supporting body and a finished ending as full
alternate edits; a library of isolated openings must never replace the current full collection.
For a revision retain valid existing stories, repair bad joins, and add supported alternatives.
Unused does not mean unusable: distinguish a usable alternate take from actual crew talk,
unresolved transcription and incomplete clauses. Read useful prefixes/suffixes of every unit.
An explicit price or profanity in one clause does not disqualify the rest of the same unit.
Qualitative value/price praise and permitted banknote-count expressions are not explicit baht
amounts. Technical sizes/weights/capacities and reuse/replacement/manual-operation details are
not prices. Keep the speaker's uncertainty and conditions when selecting such details.
Never call a unit duplicate because only one clause overlaps a selected take; recover its new
details separately. Whole-unit incomplete is justified only if no useful clause can be selected
at supplied boundaries. A dangling final 'and the father with...', 'because...', 'which...' or
an object reference without its noun is a bad cut even if its indices are valid.
Do not create a collection of tiny feature fragments without enough context to understand
and answer the opening. Shared bodies are allowed when useful hooks address a different angle.
No fixed edit count or source-usage target. Evaluate real coverage together with script quality,
never increase usage by keeping all retakes or decrease it by silently replacing full edits.
Give a specific source-based reason for each unused unit; state when it remains a usable
alternative or needs listening/image context. Never invent a prohibition to justify omission.
'''


def checks(plan,catalog):
    report=fast_editorial.text_checks(plan,catalog)
    for script in editorial.assembled_scripts(plan,catalog):
        text=script['assembled_text']
        if re.search(r'สาม\s*สอง\s*หนึ่ง|จะพูดว่าอะไร|น้ำลายกระเด็น|ขึ้นฟู้ดเทส',text):
            report['warnings'].append(dict(version=script['version'],type='production_chatter'))
        for i,row in enumerate(script['selections']):
            if re.search(r'(ถึงจะ|เอาไว้|คือเวลาเราจะ|นะใส่)\s*$',row['selected_text']):
                report['warnings'].append(dict(version=script['version'],selection=i,type='possible_incomplete_join',text=row['selected_text']))
    used={s['unit_id'] for e in plan['edits'] for s in e['selections']}
    for r in plan['source_review']:
        refs=set(re.findall(r'U\d{4}',r['reason']))
        if r['decision']=='duplicate' and not refs.intersection(used):
            report['warnings'].append(dict(unit=r['unit_id'],type='duplicate_without_selected_reference'))
    return report


def authorize_unknown_retry(folder,max_calls=6):
    """Record explicit user approval without deleting the uncertain paid attempt."""
    from engine import save
    import time
    ledger_file=folder/'api-usage.json'
    ledger=json.loads(ledger_file.read_text(encoding='utf8')) if ledger_file.exists() else []
    pending=[r for r in ledger if r['status']=='sent' and not r.get('retry_authorized_at')]
    if len(pending)!=1:raise ValueError('ต้องมีคำขอไม่ทราบผลหนึ่งรายการเพื่ออนุญาตส่งใหม่')
    if len(ledger)>=max_calls:raise ValueError(f'ครบขีดจำกัด {max_calls} คำขอแล้ว')
    pending[0]['retry_authorized_at']=time.time()
    save(ledger_file,ledger)


def call(payload,folder,key,stage,max_calls=None):
    from engine import save
    import time
    digest=fingerprint(payload);file=folder/(stage+'-response.json')
    if file.exists():
        prior=json.loads(file.read_text(encoding='utf8'))
        if prior['request_hash']!=digest:raise ValueError('คำขอเรียบเรียงเปลี่ยน กรุณาเริ่มงานใหม่')
        return prior['response']
    ledger_file=folder/'api-usage.json'
    ledger=json.loads(ledger_file.read_text(encoding='utf8')) if ledger_file.exists() else []
    prior=[r for r in ledger if r['request_hash']==digest]
    if prior and not (prior[-1]['status']=='sent' and prior[-1].get('retry_authorized_at')):
        raise ValueError('คำขอนี้ไม่ทราบผล ไม่ส่งซ้ำอัตโนมัติ กรุณาตรวจงานเดิม')
    if max_calls is not None and len(ledger)>=max_calls:
        raise ValueError(f'ครบขีดจำกัด {max_calls} คำขอแล้ว เก็บแผนล่าสุดไว้ตรวจต่อ')
    if not key.strip():raise ValueError('ใส่ OpenAI API key ก่อนเรียบเรียง')
    started=time.time()
    ledger.append(dict(stage=stage,request_hash=digest,status='sent',usage=None,started_at=started,
        input_characters=len(payload.get('input',''))));save(ledger_file,ledger)
    try:
        raw,record=plan_once(payload,key)
    finally:
        ledger[-1]['elapsed_seconds']=round(time.time()-started,2)
        save(ledger_file,ledger)
    ledger[-1].update(record,status='completed' if raw is not None else 'failed');save(ledger_file,ledger)
    if raw is None:raise RuntimeError(record['error'])
    save(file,dict(request_hash=digest,response=raw))
    return raw


def normalize_review(raw,catalog,draft):
    """Recover omitted bookkeeping from a valid draft; never alter edit choices."""
    try:
        return fast_editorial.normalize_plan(raw,catalog),[]
    except ValueError as original:
        if str(original)!='รายการเหตุผลยังไม่ครบท่อนที่ไม่ใช้':
            raise
        # A complete earlier plan is evidence only for its still-unused units.
        # Invalid/absent draft reasons must continue to fail closed.
        earlier=fast_editorial.normalize_plan(draft,catalog)
        used={s['unit_id'] for e in raw['edits'] for s in e['selections']}
        supplied={r['unit_id'] for r in raw['unused']}
        missing={u['id'] for u in catalog}-used-supplied
        prior={r['unit_id']:r for r in earlier['source_review'] if r['decision']!='used'}
        unresolved=missing-set(prior)
        if unresolved:
            raise ValueError('รายการเหตุผลยังไม่ครบท่อนที่ไม่ใช้: '+', '.join(sorted(unresolved))) from original
        repaired=copy.deepcopy(raw);recovered=[]
        for uid in sorted(missing):
            row=prior[uid]
            repaired['unused'].append({k:row[k] for k in ('unit_id','decision','reason')})
            recovered.append(dict(unit_id=uid,reason=row['reason'],source='story-draft-response.json',final_review_reason_missing=True))
        plan=fast_editorial.normalize_plan(repaired,catalog)
        for row in plan['source_review']:
            if row['unit_id'] in missing:
                row.update(review_source='ai_draft_unused_reason_recovered',final_review_reason_missing=True)
        return plan,recovered


def run(catalog,folder,config,key,progress,project,silence):
    if config.get('editorial_workflow')=='self-edit-v6':
        import parallel_editorial
        return parallel_editorial.run(catalog,folder,config,key,progress,project,silence)
    if config.get('editorial_workflow')=='self-edit-v5':
        import optimized_editorial
        return optimized_editorial.run(catalog,folder,config,key,progress,project,silence)
    if config.get('editorial_workflow') in ('self-edit-v1','self-edit-v2','self-edit-v3','self-edit-v4'):
        from self_edit_workflow import run as run_workflow
        return run_workflow(catalog,folder,config,key,progress,project,silence)
    from engine import save
    payload=fast_editorial.request_body(catalog,config['model'],config.get('effort','medium'),config.get('notes',''))
    payload['instructions']+=EXTRA
    payload['text']=copy.deepcopy(payload['text'])
    edit_schema=payload['text']['format']['schema']['properties']['edits']['items']
    for name in ('content_group','distinct_body_summary'):
        edit_schema['properties'][name]=dict(type='string')
        edit_schema['required'].append(name)
    payload['max_output_tokens']=24000
    (folder/'rules-used.txt').write_text(payload['instructions'],encoding='utf8')
    save(folder/'editorial-policy.json',dict(policy=POLICY,max_text_calls=2,image_inspection=False,
        rules_sha256=hashlib.sha256(payload['instructions'].encode()).hexdigest(),rules_version='1.3',meaning_review='pending'))
    with progress.stage('planning','เรียบเรียงจากวลีไทยและทั้งฟุตเทจ · ขั้น 1/2'):
        draft=call(payload,folder,key,'story-draft')
    review=dict(payload)
    data=json.loads(payload['input'])
    data['draft']=draft
    try:
        draft_plan=fast_editorial.normalize_plan(draft,catalog)
        data['assembled_draft']=editorial.assembled_scripts(draft_plan,catalog)
        data['local_checks']=checks(draft_plan,catalog)
        resolve_plan(draft_plan,catalog,silence,project)
    except (ValueError,KeyError,TypeError) as error:
        data['draft_validation_error']=str(error)
    review['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    review['instructions']+='''
FINAL ASSEMBLED REVIEW AND RECOVERY
Return the complete revised edit set, not a critique. Read each assembled draft in playback order:
fix repeated premises, crew talk, abandoned starts, dangling referents, missing feature explanations
and inaccurate joins by changing only supplied phrase selections. Inspect every unused or partly
used take for recoverable new information, prepared hooks and useful alternate stories. Include
supported complete alternatives that were overlooked; do not create filler variants or a quota.
Use the same output schema, covering every unused UNIT exactly once after your final selections.
'''
    with progress.stage('editorial_review','อ่านบทที่ต่อจริงและเก็บตก Hook/เรื่องที่เหลือ · ขั้น 2/2'):
        raw=call(review,folder,key,'story-review')
        plan,recovered=normalize_review(raw,catalog,draft)
        save(folder/'coverage-recovery.json',dict(recovered=recovered,edits_changed=False,extra_api_calls=0))
        if recovered:
            progress(message='กู้เหตุผลท่อนที่รอบรีวิวตกหล่นจากร่างเดิม: '+', '.join(r['unit_id'] for r in recovered)+' · ไม่เรียก API เพิ่ม')
        plan['editorial_policy']=POLICY
        resolve_plan(plan,catalog,silence,project)
    report=checks(plan,catalog)
    report['meaning_review']='AI assembled review completed; human listening pending'
    save(folder/'text-checks.json',report)
    save(folder/'assembled-scripts.json',editorial.assembled_scripts(plan,catalog))
    editorial.write_script_report(folder,plan,catalog)
    return plan
