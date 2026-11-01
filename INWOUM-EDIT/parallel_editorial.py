"""Atomic fact coverage, local speech gates, and disjoint parallel editorial work."""
import copy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import editorial
import fast_editorial as fast
import full_contract as full
import optimized_editorial as quality
import self_edit_workflow as legacy
import selection_bounds as bounds
from fast_logic import resolve_plan
from planner import obj,fingerprint
from parallel_requests import call

RULES=Path(__file__).parent/'rules'


def compact_edit(edit):
    return {k:copy.deepcopy(edit[k]) for k in ('name','purpose','content_group','distinct_body_summary','edit_role','selections') if k in edit} | {
        'selections':[{k:s[k] for k in ('unit_id','first_word','last_word')} for s in edit['selections']]}


def make_inventory(raw,catalog):
    ids={u['id'] for u in catalog};rows=raw['unit_audit']
    if len(rows)!=len(ids) or {r['unit_id'] for r in rows}!=ids:raise ValueError('บัญชีสาระต้องสำรวจทุกท่อนหนึ่งครั้ง')
    inv=dict(groups=raw['groups'],units=[],full_requirements=raw['facts'],protected_names=raw['protected_names'],action_review=raw['action_review'])
    inv=quality.bind_names(inv,catalog);full.validate_inventory(inv,catalog)
    facts={r['id']:r for r in raw['facts']};gids={g['id'] for g in raw['groups']}
    for fact in facts.values():
        if fact['group_id'] not in gids:raise ValueError('สาระอ้างกลุ่มผิด')
    for row in rows:
        if len(row['fact_ids'])!=len(set(row['fact_ids'])) or set(row['fact_ids'])-set(facts):raise ValueError('ท่อนอ้างสาระผิดหรือซ้ำ')
        if row['classification'] in ('usable','recoverable') and not row['fact_ids']:raise ValueError('ท่อนใช้ได้ต้องผูกสาระ ห้ามตกหล่นในช่องว่างของบัญชี')
        opportunities=[];links=[]
        for fid in row['fact_ids']:
            fact=facts[fid]
            anchors=[s for o in fact['options'] for s in o['selections'] if s['unit_id']==row['unit_id']]
            links.append(dict(fact_id=fid,literal_option_from_this_unit=bool(anchors),
                basis='source_option' if anchors else 'model_semantic_reference_not_literal_evidence'))
            for s in anchors:opportunities.append(dict(first_word=s['first_word'],last_word=s['last_word'],group_ids=[fact['group_id']],purpose=fact['topic'],fact_id=fid))
        inv['units'].append(dict(unit_id=row['unit_id'],classification=row['classification'],reason=row['reason'],opportunities=opportunities,fact_links=links))
    legacy.validate_inventory(inv,catalog,True,True);quality.validate_names(inv,catalog)
    active=[dict(id=r['id'],reason='ตรวจขอบสาระเดิม',options=r['options']) for r in raw['facts'] if r['scope']!='hold']
    quality.apply_fact_anchors(dict(anchors=active),inv,catalog)
    return inv


def coverage(plan,inventory,catalog):
    units={u['id']:u for u in catalog};chosen=[full.word_set(e) for e in plan['edits']]
    return [dict(id=f['id'],topic=f['topic'],scope=f['scope'],reason=f['reason'],
        edit_indices=[i for i,words in enumerate(chosen) if any(full.option_words(o,units)<=words for o in f['options'])]) for f in inventory['full_requirements']]


def merge_focused(previous,raw):
    count=len(previous);replacements={};drops={}
    for r in raw['replacements']:
        i=r['edit_index']
        if type(i)!=int or not 1<=i<count or i in replacements:raise ValueError('รหัสคลิปย่อยแก้ผิดหรือซ้ำ')
        replacements[i]=r['edit']
    for r in raw['drops']:
        i=r['edit_index']
        if type(i)!=int or not 1<=i<count or i in drops or not r['reason'].strip():raise ValueError('รหัสคลิปย่อยพักผิดหรือขาดเหตุผล')
        drops[i]=r['reason']
    if set(drops)&set(replacements):raise ValueError('ห้ามแก้และพักคลิปเดียวกัน')
    result=[copy.deepcopy(replacements.get(i,previous[i])) for i in range(1,count) if i not in drops]
    result+=copy.deepcopy(raw['additions'])
    if any(e['edit_role'] not in ('focused','long_focused') for e in result):raise ValueError('งานเก็บตกห้ามแก้หรือสร้าง FULL')
    return result


def apply_full_polish(answer,inventory,edit,catalog):
    inv=copy.deepcopy(inventory);result=copy.deepcopy(edit);facts={r['id']:r for r in inv['full_requirements']}
    groups=[]
    for row in answer['equivalent_facts']:
        ids=set(row['fact_ids'])
        if len(ids)<2 or len(ids)!=len(row['fact_ids']) or ids-set(facts) or not row['reason'].strip():raise ValueError('คู่สาระเทียบเท่าไม่ถูกต้อง')
        if any(facts[i]['scope']=='hold' for i in ids) or len({facts[i]['function'] for i in ids})!=1:raise ValueError('ห้ามรวมข้อพักหรือข้อเท็จจริงคนละหน้าที่')
        if any(ids&g for g in groups):raise ValueError('กลุ่มสาระเทียบเท่าห้ามซ้อนกัน')
        groups.append(ids)
        options=[];seen=set()
        for fid in sorted(ids):
            for option in facts[fid]['options']:
                digest=json.dumps(option,sort_keys=True)
                if digest not in seen:options.append(option);seen.add(digest)
        for fid in ids:facts[fid]['options']=copy.deepcopy(options)
    drops=answer['remove_selection_indices'];replacements={}
    if len(drops)!=len(set(drops)) or any(type(i)!=int or not 0<=i<len(edit['selections']) for i in drops):raise ValueError('รหัสวลีที่ลบผิดหรือซ้ำ')
    units={u['id']:u for u in catalog}
    for row in answer['selection_replacements']:
        i=row['selection_index']
        if type(i)!=int or not 0<=i<len(edit['selections']) or i in replacements or i in drops:raise ValueError('รหัสวลีแก้ผิดหรือซ้ำ')
        uid=edit['selections'][i]['unit_id'];n=max(1,len(units[uid]['words']));parts=[];last=-1
        for r in row['ranges']:
            a,b=r['first_word'],r['last_word']
            if type(a)!=int or type(b)!=int or not 0<=a<=b<n or a<=last:raise ValueError('ขอบวลีเกลาผิดหรือซ้อน')
            parts.append(dict(unit_id=uid,first_word=a,last_word=b));last=b
        if not parts:raise ValueError('ช่วงเกลาว่าง')
        replacements[i]=parts
    result['selections']=[s for i,s0 in enumerate(edit['selections']) if i not in drops for s in replacements.get(i,[s0])]
    if not result['selections']:raise ValueError('ห้ามลบ FULL ทั้งหมด')
    full.validate_inventory(inv,catalog)
    return inv,result


def validate_fact_proof(raw,inventory,plan,catalog):
    facts={r['id']:r for r in inventory['full_requirements']};rows=raw['fact_review']
    if len(rows)!=len(facts) or {r['fact_id'] for r in rows}!=set(facts):raise ValueError('ยังตรวจความหมายไม่ครบทุกสาระ')
    scripts=editorial.assembled_scripts(plan,catalog);result=[];errors=[]
    for row in rows:
        f=facts[row['fact_id']];i=row['clip_index'];indices=row['selection_indices']
        if not row['reason'].strip():errors.append(f['id']+' ไม่มีเหตุผล');continue
        if f['scope']=='hold':
            if row['status']!='held' or i!=-1 or indices:errors.append(f['id']+' ต้องพักตามบัญชีเดิม')
            result.append(dict(id=f['id'],topic=f['topic'],scope='hold',edit_indices=[],reason=row['reason'],evidence=[]));continue
        if row['status']!='covered' or type(i)!=int or not 0<=i<len(plan['edits']) or f['scope']=='full' and i!=0:
            errors.append(f['id']+' ยังขาด: '+row['reason']);continue
        if not indices or len(indices)!=len(set(indices)) or any(type(j)!=int or not 0<=j<len(plan['edits'][i]['selections']) for j in indices):
            errors.append(f['id']+' อ้างวลีที่ไม่ได้เลือกจริง');continue
        evidence=[scripts[i]['selections'][j] for j in indices]
        result.append(dict(id=f['id'],topic=f['topic'],scope=f['scope'],edit_indices=[i],reason=row['reason'],evidence=evidence))
    if errors:raise ValueError('ตรวจสาระจากบทจริงไม่ผ่าน '+json.dumps(errors,ensure_ascii=False))
    return result


def run(catalog,folder,config,key,progress,project,silence):
    from engine import save
    instructions=fast.rules_text()+'\n'+(RULES/'EDITORIAL-LESSONS-1.4.md').read_text('utf8')+'\n'+(RULES/'EDITORIAL-LESSONS-1.5.md').read_text('utf8')
    if config.get('hook_policy')=='hook-sales-v1':
        instructions+='\n'+(RULES/'EDITORIAL-LESSONS-1.6.md').read_text('utf8')
    source=json.loads(fast.request_body(catalog,config['model'],config.get('effort','medium'),config.get('notes',''))['input'])
    source=bounds.compact_data(source);seed=None;prior=[];prior_inventory=None
    if config.get('editorial_seed_job'):
        root=folder.parent.resolve();seed=(root/config['editorial_seed_job']).resolve()
        if not seed.is_relative_to(root):raise ValueError('งานอ้างอิงต้องอยู่ใน jobs')
        if (seed/'plan.json').exists() and json.loads((seed/'catalog.json').read_text('utf8'))==catalog and all((seed/n).read_bytes()==(folder/n).read_bytes() for n in ('source-sha256.json','audio-identity.json')):
            prior=[compact_edit(e) for e in json.loads((seed/'plan.json').read_text('utf8'))['edits']]
            f=seed/'content-inventory.json'
            if f.exists():prior_inventory=json.loads(f.read_text('utf8'))
            save(folder/'editorial-seed.json',dict(source_job=seed.name,source_and_audio_and_catalog_verified=True,accepted_edits=len(prior)))
        else:seed=None
    def request(data,schema,task,effort=None,selection_catalog=None):
        # Only schemas selecting source need the source alternatives definition.
        if '"selections"' in json.dumps(schema):schema=bounds.bounded_schema(schema,selection_catalog or catalog)
        return dict(model=config['model'],store=False,max_output_tokens=18000,
            instructions=instructions+'\n'+task,input=json.dumps(data,ensure_ascii=False,separators=(',',':')),
            reasoning=dict(effort=effort or config.get('effort','medium')),
            text=dict(format=dict(type='json_schema',name='parallel_editorial',strict=True,schema=schema)))
    def checked(req,stage,validate):
        raw=call(req,folder,key,stage)
        error_file=folder/(stage+'-validation-error.json')
        if (folder/(stage+'-repair-response.json')).exists() and error_file.exists():
            saved=json.loads(error_file.read_text('utf8'));fix=copy.deepcopy(req)
            data=json.loads(fix['input']);data.update(invalid_response=raw,validation_error=saved['error'])
            fix['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
            fix['instructions']+='\nCorrect this known validation error ONLY; keep all valid facts and decisions.'
            return validate(call(fix,folder,key,stage+'-repair'))
        try:return validate(raw)
        except (ValueError,KeyError,TypeError) as error:
            fix=copy.deepcopy(req);data=json.loads(fix['input']);data.update(invalid_response=raw,validation_error=str(error))
            fix['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
            fix['instructions']+='\nCorrect this known validation error ONLY; keep all valid facts and decisions.'
            save(folder/(stage+'-validation-error.json'),dict(error=str(error)))
            return validate(call(fix,folder,key,stage+'-repair'))
    def normalize(edits,inv):return legacy.normalize(dict(edits=edits,unused=[]),catalog,inv)
    def gate(edits,inv):
        if not edits:return
        plan=normalize(edits,inv);quality.validate_names(inv,catalog,plan);quality.validate_forbidden_fragments(plan,catalog)
        quality.validate_speech(plan,catalog);resolve_plan(plan,catalog,silence,project)
    def structure(plan,inv):
        return full.validate(plan,dict(inv,full_requirements=[]),catalog,resolve_plan(plan,catalog,silence,project),True)
    def clean_speech(edits,inv,stage):
        plan=dict(edits=copy.deepcopy(edits));findings=quality.speech_findings(plan,catalog)
        if findings:
            targets,schema,data=quality.speech_patch_request(plan,catalog,findings)
            req=request(data,schema,'Fix ONLY these speech ranges. Preserve source meaning. Return ordered nonoverlapping subranges inside each original selection. Remove stutters and abandoned connector/verb tails. Never rewrite other selections.',effort='low')
            plan=checked(req,stage+'-'+fingerprint(req)[:10],lambda answer:quality.apply_speech_patch(dict(edits=edits),targets,answer))
        gate(plan['edits'],inv)
        return plan['edits']
    def repair_links(raw,fact_schema):
        facts={r['id']:r for r in raw['facts']};units={u['id']:u for u in catalog};targets=[]
        for row in raw['unit_audit']:
            unbound=any(not any(s['unit_id']==row['unit_id'] for o in facts[fid]['options'] for s in o['selections']) for fid in row['fact_ids'] if fid in facts)
            if unbound or row['classification'] in ('usable','recoverable') and not row['fact_ids'] or not re.search('[\u0e00-\u0e7f]',row['reason']):
                targets.append(row)
        if not targets:return raw
        ids=[r['unit_id'] for r in targets]
        data=dict(rows=[dict(row=r,indexed_source={str(i):w['text'] for i,w in enumerate(units[r['unit_id']]['words'] or [units[r['unit_id']]])}) for r in targets],
            facts=[dict(id=f['id'],topic=f['topic'],scope=f['scope'],group_id=f['group_id']) for f in facts.values()],groups=raw['groups'])
        row_schema=obj(dict(unit_id=dict(type='string',enum=ids),classification=dict(type='string',enum=['usable','recoverable','uncertain','production']),
            fact_ids=dict(type='array',items=dict(type='string')),reason=dict(type='string')))
        schema=obj(dict(unit_updates=dict(type='array',minItems=len(ids),maxItems=len(ids),items=row_schema),new_facts=dict(type='array',items=fact_schema)))
        req=request(data,schema,'''Audit ONLY these questionable source-to-fact links. A missing literal option can be
a redundant TAKE of the SAME exact fact: link to that existing fact, do not add redundant evidence. But different
properties/methods are NEW facts (chest carry is NOT waist carry; light is NOT small; wire is NOT buckle lock).
Add a new atomic full/collection fact with a CLEAN complete source option for every genuinely NEW useful detail.
Read each indexed source, not just its old reason. Mixed/uncompleted units can have complete useful prefixes.
For genuinely unusable fragments choose uncertain/production with a specific reason; do not call them usable or
recoverable with no fact. Return every requested unit exactly once. All reasons/topics Thai only. Preserve all
existing facts; never demote/delete them or invent properties. New fact source must come from supplied units.''',effort='low',selection_catalog=[u for u in catalog if u['id'] in ids])
        def apply(answer):
            updates={r['unit_id']:r for r in answer['unit_updates']}
            if len(updates)!=len(ids) or set(updates)!=set(ids):raise ValueError('ตรวจลิงก์สาระไม่ครบหรือซ้ำ')
            result=copy.deepcopy(raw);known=set(facts)
            for fact in answer['new_facts']:
                if fact['id'] in known:raise ValueError('สาระใหม่ใช้รหัสเดิม')
                known.add(fact['id']);result['facts'].append(fact)
            problems=[]
            for row in updates.values():
                if not re.search('[\u0e00-\u0e7f]',row['reason']):problems.append(row['unit_id']+' เหตุผลต้องเป็นไทย')
                if row['classification'] in ('usable','recoverable') and not row['fact_ids']:problems.append(row['unit_id']+' ไม่มีสาระ')
                if set(row['fact_ids'])-known:problems.append(row['unit_id']+' รหัสสาระผิด')
            if problems:raise ValueError('; '.join(problems))
            result['unit_audit']=[updates.get(r['unit_id'],r) for r in result['unit_audit']]
            return result
        return checked(req,'fact-link-audit',apply)
    def clean_fact_words(raw):
        result=copy.deepcopy(raw);locations=[];edits=[]
        for i,fact in enumerate(raw['facts']):
            if fact['scope']=='hold':continue
            for j,option in enumerate(fact['options']):
                locations.append((i,j));edits.append(dict(name=fact['id']+' '+fact['topic'],edit_role='FULL',selections=option['selections']))
        plan=dict(edits=edits)
        findings=[r for r in quality.speech_findings(plan,catalog) if r['issue']=='consecutive_repeat']
        if findings:
            targets,schema,data=quality.speech_patch_request(plan,catalog,findings)
            req=request(data,schema,'Clean only accidental consecutive repeated words in these FACT EVIDENCE ranges. Return ordered nonoverlapping subranges within each original. Keep the full factual meaning, names, negations, uncertainty and conditions. Other evidence stays unchanged.',effort='low')
            def validate(answer):
                fixed=quality.apply_speech_patch(plan,targets,answer)
                if any(r['issue']=='consecutive_repeat' for r in quality.speech_findings(fixed,catalog)):raise ValueError('ช่วงอ้างอิงยังมีคำซ้ำ')
                return fixed
            fixed=checked(req,'fact-speech-'+fingerprint(req)[:10],validate)
            for k,(i,j) in enumerate(locations):result['facts'][i]['options'][j]['selections']=fixed['edits'][k]['selections']
        return result
    save(folder/'editorial-policy.json',dict(policy='self-edit-v6',normal_text_calls=5,max_text_calls=21,
        independent_workers=['full','focused-recovery'],rules_sha256=hashlib.sha256(instructions.encode()).hexdigest(),automatic_visual_approval=False))
    (folder/'rules-used.txt').write_text(instructions,encoding='utf8')
    inv_schema=full.inventory_schema(legacy.INVENTORY)
    fact_schema=inv_schema['properties']['full_requirements']['items']
    fact_schema['properties']['group_id']=dict(type='string');fact_schema['required'].append('group_id')
    schema=obj(dict(groups=copy.deepcopy(legacy.INVENTORY['properties']['groups']),
        facts=dict(type='array',minItems=1,items=fact_schema),
        protected_names=dict(type='array',items=obj(dict(text=dict(type='string')))),
        action_review=copy.deepcopy(legacy.INVENTORY['properties']['action_review']),
        unit_audit=dict(type='array',minItems=len(catalog),maxItems=len(catalog),items=obj(dict(
            unit_id=dict(type='string',enum=[u['id'] for u in catalog]),
            classification=dict(type='string',enum=['usable','recoverable','uncertain','production']),
            fact_ids=dict(type='array',items=dict(type='string')),reason=dict(type='string'))))))
    data=dict(source)
    if prior_inventory:
        data['existing_groups_to_preserve']=prior_inventory['groups']
    with progress.stage('content_inventory','บัญชีสาระทุกท่อนและขอบคำสะอาด · 1/2'):
        req=request(data,schema,'''Inventory ALL source clauses into atomic distinct facts, not just the prior topics.
When existing_groups_to_preserve is supplied, retain ALL its group IDs; you may add groups. Keep groups broad.
FACTS ARE NOT GROUPS. One fact describes ONE distinct property/method/claim. "lightweight and compact" is TWO
facts; "strap wire and buckle lock" is TWO; "RFID and storage capacity" is TWO. Chest carry, waist carry and
concealing under clothing are distinct methods, NOT alternative takes of one fact. Preserve each as a separate
essential product fact when spoken clearly. Facts may share a broad group_id. Do not copy broad group titles
as fact topics. Every source option of a fact must express the SAME exact property/method; never use size as
an alternative source for weight. Do not bundle several retakes as one mandatory option.
Include each usable construction detail, weight/size, carry method, useful comparative value, audience/problem,
mechanism/demo and limitations. FULL scope for essential distinct product facts; collection for other useful
information and substantive hook angles. Alternative TAKES of the same fact are multiple source OPTIONS,
not mandatory separate clips. Hold only genuinely unusable/unclear material, never 'enough clips already'.
One fact may have several options, each option an exact complete clause with conditions. Use disjoint ranges
to remove stutters; do not anchor next-clause heads or repeat words. Do NOT bundle a complete long take merely
for product identity/audience. No profanity anchors. Names exact Thai text only. Audit every unit once and link
usable/recoverable units to fact IDs. Best literal evidence options need not enumerate every redundant take;
unit links without a literal option are model semantic references, NOT proof of coverage. Mixed bad/price/unfinished units
can still contain complete useful clauses. Unit boundaries are NOT story boundaries. Keep reasons very short.''',effort='low')
        def inventory_check(raw):
            result=make_inventory(raw,catalog)
            if prior_inventory and {g['id'] for g in prior_inventory['groups']}-{g['id'] for g in result['groups']}:
                raise ValueError('ต้องรักษารหัสกลุ่มเดิมเพื่ออ้างอิงคลิปที่ยอมรับแล้ว เพิ่มกลุ่มใหม่ได้')
            return result
        binding=fingerprint(dict(source=source,instructions=instructions,model=config['model'],effort=config.get('effort','medium'),inventory_effort='low',inventory_task_revision='atomic-links-v2'))
        reuse=None
        if seed and (seed/'fact-ledger-binding.json').exists() and json.loads((seed/'fact-ledger-binding.json').read_text('utf8'))['binding']==binding:
            for name in ('fact-ledger-canonical.json','fact-ledger-reused.json','fact-ledger-repair-response.json','fact-ledger-response.json'):
                if (seed/name).exists():
                    candidate=json.loads((seed/name).read_text('utf8'))['response']
                    try:inventory=inventory_check(candidate);reuse=candidate;break
                    except (ValueError,KeyError,TypeError):continue
        if reuse is not None:
            save(folder/'fact-ledger-reused.json',dict(source_job=seed.name,response=reuse,binding=binding))
            progress(message='ใช้บัญชีสาระเดิมที่ตรงเสียง ต้นฉบับ กฎ และโมเดล ไม่เรียก API สำรวจซ้ำ')
        else:
            ledger_raw=call(req,folder,key,'fact-ledger')
            # A prior completed repair is a model answer, not a hand-written plan.
            reviewed_links=False
            for name in ('fact-ledger-repair-response.json','fact-ledger-final-repair-response.json','fact-ledger-final-repair-repair-response.json'):
                old_repair=folder/name
                if old_repair.exists():
                    cached=json.loads(old_repair.read_text('utf8'));ledger=json.loads((folder/'api-usage.json').read_text('utf8'))
                    if not any(r['request_hash']==cached['request_hash'] and r['status']=='completed' for r in ledger):raise ValueError('คำตอบแก้บัญชีไม่มีหลักฐาน API')
                    ledger_raw=cached['response'];reviewed_links=name.startswith('fact-ledger-final')
            linked=clean_fact_words(ledger_raw if reviewed_links else repair_links(ledger_raw,fact_schema))
            canonical_raw=linked
            try:inventory=inventory_check(linked)
            except (ValueError,KeyError,TypeError) as error:
                fix=request(dict(source,inventory_to_correct=linked,validation_error=str(error)),schema,
                    'Correct only the reported inventory validation problem. Keep every distinct fact, its scope and all valid links. Return complete schema. Thai only.',effort='low')
                def final_inventory_check(answer):
                    nonlocal canonical_raw
                    result=inventory_check(answer);canonical_raw=answer
                    return result
                inventory=checked(fix,'fact-ledger-final-repair',final_inventory_check)
            save(folder/'fact-ledger-canonical.json',dict(response=canonical_raw,binding=binding))
        save(folder/'fact-ledger-binding.json',dict(binding=binding))
        save(folder/'content-inventory.json',inventory)
    if prior:
        with progress.stage('speech_precheck','ตรวจหัวคลิป คำซ้ำ และรอยต่อก่อนตรวจใหญ่'):
            prior=clean_speech(prior,inventory,'seed-speech')
    seed_plan=dict(edits=prior)
    es=full.edit_schema(legacy.edit_schema())['properties']['edits']['items']
    es['properties']['content_group']['enum']=[g['id'] for g in inventory['groups']]
    full_edit=copy.deepcopy(es);full_edit['properties']['edit_role']['enum']=['FULL']
    focused_edit=copy.deepcopy(es);focused_edit['properties']['edit_role']['enum']=['focused','long_focused']
    full_schema=obj(dict(edit=full_edit,review_note=dict(type='string')))
    focus_schema=obj(dict(replacements=dict(type='array',items=obj(dict(edit_index=dict(type='integer',minimum=1,maximum=max(1,len(prior)-1)),edit=focused_edit))),
        additions=dict(type='array',items=focused_edit),drops=dict(type='array',items=obj(dict(edit_index=dict(type='integer',minimum=1,maximum=max(1,len(prior)-1)),reason=dict(type='string')))),
        review_note=dict(type='string')))
    common=dict(source,inventory=inventory,existing_edits=[compact_edit(e) for e in prior],fact_coverage=coverage(seed_plan,inventory,catalog))
    full_req=request(common,full_schema,'''Review/rebuild ONLY the principal FULL edit. Keep the accepted story's strengths;
incorporate every full-scope fact from the NEW inventory, including previously missing useful details. FULL
must be complete coherent sales speech <=180s. No duplicate premises, stutters, conjunction opening or dangling
clause heads. Read actual joins. Keep demonstrations, negations, real conditions and uncertainty. Choose source
options expressing each required fact. This worker owns ONLY FULL; it does not curate focused versions.''')
    focus_req=request(dict(common,omitted_spans=legacy.omissions(seed_plan,catalog)),focus_schema,'''Own ONLY focused stories.
Preserve good accepted focused clips. Recover all collection-scope facts AND useful previously omitted details
into coherent stories: complementary TWO topics may form a better story. Never make every fact a tiny clip.
Compare the whole focused collection for substantive duplication and standalone value. Drop/merge a redundant
or weak fragment only when its useful information is retained in another focused story (or FULL requirements).
Review short price-only/intro-only fragments carefully; preserve limits such as not truly slash-proof.
Ensure every collection-scope fact appears with its full source option in at least one focused output.
Return sparse replacements by zero-based existing edit_index, additions, and drops with reasons. No FULL edits.
Unchanged focused candidates persist automatically. If no previous edits, return additions only. Do not create
all permutations. Read source neighboring words and real sentence joins; no invented claims, conditions or speech.''')
    def full_worker():
        def validate(raw):
            edits=clean_speech([raw['edit']],inventory,'full-speech')
            p=normalize(edits,inventory);structure(p,inventory)
            return dict(raw,edit=edits[0])
        return checked(full_req,'parallel-full',validate)
    def focus_worker():
        def validate(raw):
            edits=merge_focused(prior,raw)
            edits=clean_speech(edits,inventory,'focused-speech')
            return dict(raw,edits=edits)
        return checked(focus_req,'parallel-focused',validate)
    results={}
    with progress.stage('parallel_review','ตรวจ FULL และเก็บตกเรื่องย่อยพร้อมกัน · 2/2'):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={pool.submit(full_worker):'full',pool.submit(focus_worker):'focused'}
            for future in as_completed(futures):
                name=futures[future];results[name]=future.result()
                progress(message=('FULL' if name=='full' else 'คลิปย่อยและสาระเก็บตก')+' ตรวจเสร็จ เก็บคำตอบแล้ว')
    edit=results['full']['edit'];units={u['id']:u for u in catalog}
    replacement_choices=[]
    for i,s in enumerate(edit['selections']):
        n=max(1,len(units[s['unit_id']]['words']))
        replacement_choices.append(obj(dict(selection_index=dict(type='integer',enum=[i]),ranges=dict(type='array',minItems=1,maxItems=4,items=obj(dict(
            first_word=dict(type='integer',minimum=0,maximum=n-1),last_word=dict(type='integer',minimum=0,maximum=n-1)))))))
    polish_schema=obj(dict(equivalent_facts=dict(type='array',items=obj(dict(fact_ids=dict(type='array',minItems=2,items=dict(type='string',enum=[f['id'] for f in inventory['full_requirements']])),reason=dict(type='string')))),
        remove_selection_indices=dict(type='array',items=dict(type='integer',minimum=0,maximum=len(edit['selections'])-1)),
        selection_replacements=dict(type='array',items=dict(anyOf=replacement_choices)),review_note=dict(type='string')))
    fact_text=[dict(id=f['id'],topic=f['topic'],function=f['function'],scope=f['scope'],
        options=[[''.join(w['text'] for w in (units[s['unit_id']]['words'][s['first_word']:s['last_word']+1] or [units[s['unit_id']]])) for s in o['selections']] for o in f['options']]) for f in inventory['full_requirements']]
    with progress.stage('final_speech_review','ตรวจซ้ำใจความ FULL และเกลาวลีเฉพาะจุด'):
        req=request(dict(facts=fact_text,full_script=editorial.assembled_scripts(dict(edits=[edit]),catalog)[0],
            indexed_source={uid:{str(i):w['text'] for i,w in enumerate(units[uid]['words'] or [units[uid]])} for uid in sorted({s['unit_id'] for s in edit['selections']})}),polish_schema,
            '''Final SMALL FULL polish, not another rewrite. Some fact IDs may accidentally duplicate the SAME
property in different wording. Only genuinely equivalent facts with identical conditions may share evidence
options. Do not equate size with weight, chest with waist, a wire with a buckle, price with value, identity with
an anti-theft feature or remove uncertainty. Keep all fact IDs/scopes. If the FULL repeats a premise (such as
explaining the same strap wire twice), remove the weaker redundant selection only after equivalent facts share
evidence. Improve awkward fillers/joins with exact source ranges from the SAME unit. Prefer complete fluent
phrases over tiny isolated words, e.g. keep a complete weight description, not just a broken 'คือเบา'. Keep
all unique details, names, conditions, negatives and demonstrations. No new claims. Most selections should
remain unchanged. Return only equivalent fact groups, deleted selection indices and changed ranges, plus a
concise Thai review note. Do not claim audio/visual approval.''',effort='low')
        def validate_polish(answer):
            inv,edited=apply_full_polish(answer,inventory,edit,catalog)
            all_edits=[edited]+results['focused']['edits'];gate(all_edits,inv)
            structure(normalize(all_edits,inv),inv)
            return inv,edited
        proposal_file=folder/'final-full-polish-response.json'
        if proposal_file.exists() and not (folder/'final-full-polish-request.json').exists() and json.loads(proposal_file.read_text('utf8'))['request_hash']!=fingerprint(req):
            proposal=json.loads(proposal_file.read_text('utf8'));ledger=json.loads((folder/'api-usage.json').read_text('utf8'))
            if not any(r['request_hash']==proposal['request_hash'] and r['status']=='completed' for r in ledger):raise ValueError('ข้อเสนอเกลาบทไม่มีคำตอบ API ที่เสร็จแล้วรองรับ')
            inventory,edit=validate_polish(proposal['response'])
            save(folder/'final-full-polish-proposal-reuse.json',dict(original_request_hash=proposal['request_hash'],current_request_hash=fingerprint(req),
                note='Legacy request lacked saved payload and stable key order. Reused completed model proposal as candidate, not an exact cache hit. Source structure validated again; independent meaning proof required before export.'))
        else:inventory,edit=checked(req,'final-full-polish',validate_polish)
        save(folder/'content-inventory-final.json',inventory)
    edits=[edit]+results['focused']['edits']
    plan=normalize(edits,inventory);gate(edits,inventory)
    report=structure(plan,inventory)
    keys=[tuple((s['unit_id'],s['first_word'],s['last_word']) for s in e['selections']) for e in edits[1:]]
    if len(keys)!=len(set(keys)):raise ValueError('คลิปย่อยซ้ำช่วงตัดกันทั้งคลิป')
    scripts=editorial.assembled_scripts(plan,catalog)
    proof_scripts=[dict(clip_index=i,name=s['name'],selections=[dict(selection_index=j,**r) for j,r in enumerate(s['selections'])]) for i,s in enumerate(scripts)]
    proof_schema=obj(dict(fact_review=dict(type='array',minItems=len(inventory['full_requirements']),maxItems=len(inventory['full_requirements']),items=obj(dict(
        fact_id=dict(type='string',enum=[f['id'] for f in inventory['full_requirements']]),status=dict(type='string',enum=['covered','missing','held']),
        clip_index=dict(type='integer',minimum=-1,maximum=len(plan['edits'])-1),selection_indices=dict(type='array',items=dict(type='integer',minimum=0,maximum=max(len(e['selections']) for e in plan['edits'])-1)),reason=dict(type='string'))))))
    with progress.stage('meaning_proof','ตรวจความหมายทุกสาระจากวลีที่เลือกจริง'):
        req=request(dict(source_facts=fact_text,actual_selected_scripts=proof_scripts),proof_schema,'''Independently verify
every fact against ACTUAL SELECTED speech, not the author's review note. Topic is the claim; original source
quotes provide its conditions/meaning but may contain stutters or unrelated next-clause heads. Another selected
take can express the same fact. Preserve negatives, uncertainty, component identity and real conditions.
For each required fact, cite actual clip_index and one or more actual selection_index values that collectively
express that same claim completely. Full-scope facts MUST be in clip0. Collection-scope facts may occur anywhere.
If genuinely missing use missing, clip_index -1 and no selections; do not invent evidence or demote the fact.
Held facts must remain held. Do not mistake a similar property for evidence (chest/waist, size/weight, wire/lock).
Reasons concise Thai. No listening/image approval. Citation IDs are checked by program, semantic entailment is
your separate review judgment. Inspect the spoken text, not just titles or declared purposes.''',effort='low')
        try:proof=checked(req,'meaning-proof',lambda raw:validate_fact_proof(raw,inventory,plan,catalog))
        except ValueError:
            prior_proof_file=folder/'meaning-proof-repair-response.json'
            if not prior_proof_file.exists():raise
            missing=[r for r in json.loads(prior_proof_file.read_text('utf8'))['response']['fact_review'] if r['status']=='missing']
            if not missing:raise
            missing_ids={r['fact_id'] for r in missing}
            insertion_schema=obj(dict(insertions=dict(type='array',minItems=1,items=obj(dict(
                edit_index=dict(type='integer',minimum=0,maximum=len(plan['edits'])-1),
                before_selection_index=dict(type='integer',minimum=0,maximum=max(len(e['selections']) for e in plan['edits'])),
                selections=dict(type='array',minItems=1,items=copy.deepcopy(legacy.SPAN))))),reason=dict(type='string')))
            fix=request(dict(source,current_scripts=proof_scripts,missing_review=missing,
                missing_facts=[r for r in fact_text if r['id'] in missing_ids]),insertion_schema,
                '''Restore ONLY the facts marked missing by the independent reviewer. Existing selected speech is
LOCKED; add complete source clauses at the appropriate boundary, never delete/rewrite the accepted body.
Full-scope facts must be restored in edit0; collection facts may use another existing appropriate story.
Supply explicit coherent speech establishing product identity/function, real travel warnings or storage ability
when those are missing. Keep source conditions, names and negatives. Do not introduce a repeated premise when
a short linking explanation suffices. No stutters, forbidden conjunction opener, profanity or new unsupported
claims. before_selection_index inserts before that zero-based boundary; selection_count appends at end.''',effort='low')
            locked_plan=copy.deepcopy(plan)
            def apply_insertions(answer):
                revised=copy.deepcopy(locked_plan);seen=set();rows=answer['insertions']
                for row in sorted(rows,key=lambda r:(r['edit_index'],r['before_selection_index']),reverse=True):
                    i,j=row['edit_index'],row['before_selection_index']
                    if type(i)!=int or not 0<=i<len(locked_plan['edits']) or type(j)!=int or not 0<=j<=len(locked_plan['edits'][i]['selections']) or (i,j) in seen:raise ValueError('ตำแหน่งเติมสาระผิดหรือซ้ำ')
                    seen.add((i,j));revised['edits'][i]['selections'][j:j]=row['selections']
                gate(revised['edits'],inventory);structure(revised,inventory)
                return normalize(revised['edits'],inventory)
            plan=checked(fix,'meaning-source-insert',apply_insertions)
            scripts=editorial.assembled_scripts(plan,catalog)
            fixed_req=copy.deepcopy(req);data=json.loads(fixed_req['input'])
            data['actual_selected_scripts']=[dict(clip_index=i,name=s['name'],selections=[dict(selection_index=j,**r) for j,r in enumerate(s['selections'])]) for i,s in enumerate(scripts)]
            fixed_req['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
            fixed_req['text']['format']['schema']['properties']['fact_review']['items']['properties']['selection_indices']['items']['maximum']=max(len(e['selections']) for e in plan['edits'])-1
            try:proof=checked(fixed_req,'meaning-proof-after-insert',lambda raw:validate_fact_proof(raw,inventory,plan,catalog))
            except ValueError:
                last_file=folder/'meaning-proof-after-insert-repair-response.json'
                if not last_file.exists():raise
                feedback=[r for r in json.loads(last_file.read_text('utf8'))['response']['fact_review'] if r['status']=='missing']
                if not feedback:raise
                restoration=copy.deepcopy(fix);restore_data=json.loads(restoration['input'])
                restore_data.update(previous_restoration_feedback=feedback,last_attempt_scripts=data['actual_selected_scripts'])
                restoration['input']=json.dumps(restore_data,ensure_ascii=False,separators=(',',':'))
                restoration['instructions']+='''\nThe previous restoration STILL failed these specific claims. Replace the
previous restoration proposal, starting from LOCKED current_scripts (not last_attempt_scripts). Restore ALL
original missing_facts plus address the new feedback. Cite actual explicitly spoken PROPERTY/FUNCTION, not
merely a product name when an anti-theft function is required. Read full source units rather than blindly
copying an imperfect inventory anchor. Use complete clauses; do not end a range on เอาไว้/สำหรับ/ที่ without
its continuation. Do not repeat identity or a property already present. Boundaries refer to LOCKED scripts.'''
                plan=checked(restoration,'meaning-source-restore',apply_insertions)
                scripts=editorial.assembled_scripts(plan,catalog)
                verify=copy.deepcopy(req);verify_data=json.loads(verify['input'])
                verify_data['actual_selected_scripts']=[dict(clip_index=i,name=s['name'],selections=[dict(selection_index=j,**r) for j,r in enumerate(s['selections'])]) for i,s in enumerate(scripts)]
                verify['input']=json.dumps(verify_data,ensure_ascii=False,separators=(',',':'))
                verify['text']['format']['schema']['properties']['fact_review']['items']['properties']['selection_indices']['items']['maximum']=max(len(e['selections']) for e in plan['edits'])-1
                # Reserve one final read-only verification after the edit budget.
                # It cannot trigger another writing/repair request.
                final_proof=call(verify,folder,key,'meaning-proof-after-restore',max_calls=21)
                save(folder/'candidate-plan-before-proof.json',plan)
                try:proof=validate_fact_proof(final_proof,inventory,plan,catalog)
                except ValueError as error:
                    # One bounded read-only adjudication. Never modify the cut to satisfy
                    # a reviewer that cited the wrong clip or overlooked selected speech.
                    appeal=copy.deepcopy(verify);appeal_data=json.loads(appeal['input'])
                    appeal_data.update(previous_review=final_proof,local_validation_error=str(error),
                        original_context={u['id']:u['text'] for u in catalog})
                    appeal['input']=json.dumps(appeal_data,ensure_ascii=False,separators=(',',':'))
                    appeal['instructions']+='''\nResolve this contradictory review ONCE without changing any cuts.
Every full-scope fact must cite clip_index 0, even if a focused clip also expresses it. Check each FULL
selection before claiming it is missing. A source anchor may be inaccurate; judge the original context,
fact topic and actual selected speech together. If the selected speech genuinely lacks the claim, keep
missing. Do not manufacture evidence or approve merely because validation failed. Return all facts.'''
                    adjudicated=call(appeal,folder,key,'meaning-proof-adjudication',max_calls=22)
                    proof=validate_fact_proof(adjudicated,inventory,plan,catalog)
    save(folder/'fact-coverage.json',dict(method='independent AI meaning judgment; actual cited edit/selection IDs verified locally',facts=proof))
    report=structure(plan,inventory)
    report['requirements']=[dict(id=r['id'],topic=r['topic'],scope=r['scope'],in_full=0 in r['edit_indices'],in_collection=bool(r['edit_indices']),reason=r['reason'],anchor_adjustments=[]) for r in proof]
    report['check']='source duration/protected intervals plus independent AI meaning judgment; actual cited edit/selection IDs verified locally'
    full.write_report(folder,report,save)
    lines=['# บัญชีสาระกับคลิปที่ใช้จริง','','AI ตรวจความหมายแยกจากงานเขียน โปรแกรมตรวจ ID คลิป/วลีอ้างอิงว่ามีจริง ไม่ใช่การรับรองภาพหรือการฟัง','']
    for row in proof:
        clips=', '.join(str(i+1).zfill(2) for i in row['edit_indices']) or 'พัก: '+row['reason']
        lines.append('- '+row['id']+' · '+row['topic']+' · '+row['scope']+' · '+clips)
    (folder/'FACT-COVERAGE.md').write_text('\n'.join(lines),encoding='utf8')
    save(folder/'parallel-review.json',dict(full_note=results['full']['review_note'],focused_note=results['focused']['review_note'],drops=results['focused']['drops'],automatic_audio_approval=False))
    review_lines=['# ผลตรวจ FULL และคัดคลิปย่อย','','เป็นความเห็นของโมเดลจากข้อความ ไม่ใช่ผลฟังหรือยืนยันภาพ','',
        'FULL: '+results['full']['review_note'],'','ชุดคลิปย่อย: '+results['focused']['review_note'],'','## คลิปที่พักจากชุดเดิม','']
    for row in results['focused']['drops']:review_lines.append('- '+prior[row['edit_index']]['name']+': '+row['reason'])
    (folder/'COLLECTION-REVIEW.md').write_text('\n'.join(review_lines),encoding='utf8')
    save(folder/'final-plan.json',plan);save(folder/'assembled-scripts.json',editorial.assembled_scripts(plan,catalog))
    editorial.write_script_report(folder,plan,catalog)
    save(folder/'remaining-omitted-spans.json',legacy.omissions(plan,catalog))
    save(folder/'speech-quality.json',dict(findings=quality.speech_findings(plan,catalog)))
    save(folder/'text-checks.json',fast.text_checks(plan,catalog))
    (folder/'SELF-EDIT-WORKFLOW.md').write_text('# กระบวนการ 0.7.0\n\nบัญชีสาระทุกท่อน → ตรวจคำซ้ำก่อนส่ง → FULL และเก็บตก/คัดคลิปย่อยทำพร้อมกัน → ตรวจสาระ/ขอบคำ/เสียงต้นฉบับในเครื่อง\n\nทุกสาระที่ไม่พักต้องพบช่วงคำจริงในผล ยังรอฟังและตรวจ Premiere\n',encoding='utf8')
    plan['editorial_policy']='self-edit-v6'
    return plan
