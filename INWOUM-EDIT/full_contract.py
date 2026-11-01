"""Source-backed FULL completeness contract; no invented selections or duration padding."""
import copy
import re
from planner import obj
from collection_review import word_set

POLICY='''FULL + FOCUSED COLLECTION — latest mandatory user instruction.
Always deliver one FULL first, clearly named FULL, no longer than 180 seconds AFTER actual cuts.
FULL is the comprehensive principal story, not a teaser, a feature summary, or the union's shortest
representative. It must identify the product and explain the real problem, operation/demo/result,
use conditions/reuse/technical details, benefits/value and relevant audience/rationale supported by
this recording. Keep coherent source explanations; do not collapse them to single feature labels.
Choose best takes without repeating premises inside FULL. Preserve negatives and uncertainty.
FULL naturally shares source material with focused stories: that is REQUIRED and is NOT a reason
to shorten/remove FULL. Compare redundancy among focused edits separately. Retain useful long focused
stories where related content makes a complete substantial narrative. No fixed minimum length or count.
Inventory distinct usable information across the ENTIRE recording, including clauses within unused
units. Define full_requirements with tight real source options: each option is a COMPLETE bundle of
selection anchors sufficient to express that fact with its conditions. scope=full means mandatory in
FULL; collection means mandatory somewhere in the released collection; hold means a specific real
source problem (uncertain claim, abandoned action, crew talk), never merely 'already enough clips'.
Include supported product identity, key technical details, replacement/reuse qualifications, useful
comparative price and actual audience suggestions. Do not demote usable facts just to make FULL short.
Held unverified technical/safety assertions must not be turned into claims. Do not invent facts, prices,
audio or successful actions. Existing protected demonstration intervals must appear in FULL with original
sound. Use the listed tight anchors as required; final selected ranges must actually contain one complete
option for each required fact. The program checks selected words and actual duration, not your assertion.
For a merge, keep an actual distinctive source selection from the candidate in its target; if only an
interchangeable take is discarded, honestly use alternate/drop rather than claim its content was merged.
Names/reasons must be Thai. FULL is first; edit_role is FULL, focused or long_focused. Never pad to 3 minutes.'''


def inventory_schema(schema):
    schema=copy.deepcopy(schema)
    span=obj(dict(unit_id=dict(type='string'),first_word=dict(type='integer'),last_word=dict(type='integer')))
    schema['properties']['full_requirements']=dict(type='array',minItems=1,items=obj(dict(
        id=dict(type='string'),topic=dict(type='string'),
        function=dict(type='string',enum=['identity','problem','mechanism','demonstration','condition','value','audience','rationale','other']),
        scope=dict(type='string',enum=['full','collection','hold']),reason=dict(type='string'),
        options=dict(type='array',items=obj(dict(selections=dict(type='array',minItems=1,items=span)))))))
    schema['required'].append('full_requirements')
    return schema


def edit_schema(schema):
    schema=copy.deepcopy(schema);edit=schema['properties']['edits']['items']
    edit['properties']['edit_role']=dict(type='string',enum=['FULL','focused','long_focused'])
    edit['required'].append('edit_role')
    return schema


def validate_inventory(inventory,catalog):
    rows=inventory['full_requirements'];ids=[r['id'] for r in rows];units={u['id']:u for u in catalog}
    if not rows or len(ids)!=len(set(ids)):raise ValueError('รายการสาระ FULL ขาดหรือรหัสซ้ำ')
    if not any(r['function']=='identity' and r['scope']=='full' for r in rows):
        raise ValueError('ต้องมีสาระระบุสินค้าจริงใน FULL')
    for row in rows:
        if not row['topic'].strip() or not row['reason'].strip():raise ValueError('รายการสาระ FULL ต้องมีเหตุผลเฉพาะ')
        if row['scope']!='hold' and not row['options']:raise ValueError('สาระที่ต้องใช้ไม่มีแหล่งคำพูดรองรับ: '+row['id'])
        for option in row['options']:
            if not option['selections']:raise ValueError('ตัวเลือกสาระ FULL ไม่มีช่วงคำพูด')
            for s in option['selections']:
                u=units.get(s['unit_id']);a=s['first_word'];b=s['last_word']
                if u is None or type(a)!=int or type(b)!=int or not 0<=a<=b<max(1,len(u['words'])):
                    raise ValueError('ขอบคำสาระ FULL ไม่ตรงต้นฉบับ: '+row['id'])
    return inventory


def option_words(option,units):
    # Anchors may accidentally include a hesitation or the head of the next clause.
    # These are evidence checks only: never change the actual selected audio ranges.
    leading={'เอ่อ','เออ','อืม','อือ','อะ','อ่ะ','โห','เฮ้ย'}
    trailing={'มัน','อันนี้','ถ้า','เพราะว่า','เพราะ','แล้วก็','ก็','กับ','แต่','แต่ว่า','คือ','แล้ว','แบบ','ซึ่ง','นะ','ครับ','นะครับ','นะคะ','ครับผม','ไง','ล่ะ','อะ','อ่ะ'}
    result=set()
    for s in option['selections']:
        words=units[s['unit_id']].get('words',[])
        a,b=s['first_word'],s['last_word'];left,right=a,b
        if words:
            while left<=right and words[left]['text'].strip() in leading:left+=1
            # A dangling modal can belong to a following location/placement clause,
            # rather than qualify the completed fact being anchored. Require the
            # actual following source words (e.g. ก็อาจจะ | แบบอยู่นี่), never strip
            # อาจจะ generally: it remains mandatory for uncertain product claims.
            for start in range(max(left,right-2),right+1):
                head=''.join(w['text'].strip() for w in words[start:right+1])
                following=''.join(w['text'].strip() for w in words[right+1:right+6])
                if head=='ก็อาจจะ' and re.match(r'แบบ(?:อยู่|ตรง)(?:นี่|นี้|นั้น|นู่น)',following):
                    right=start-1
                    break
            while left<=right:
                text=words[right]['text'].strip()
                if text in trailing or re.fullmatch(r'(?:เพราะว่า|เพราะ|แล้วก็|แต่ว่า|ซึ่ง)(?:มัน|เรา|เขา|แบบ)?',text):right-=1
                else:break
        if left>right:left,right=a,b  # Never turn a vacuous anchor into automatic coverage.
        result.update((s['unit_id'],i) for i in range(left,right+1))
    return result


def validate(plan,inventory,catalog,resolved,final=False):
    full=[i for i,e in enumerate(plan['edits']) if e.get('edit_role')=='FULL']
    if full!=[0]:raise ValueError('ต้องมี FULL หนึ่งคลิปเป็นคลิปแรก และคลิปย่อยมีบทบาท focused/long_focused')
    if any(e.get('edit_role') not in ('FULL','focused','long_focused') for e in plan['edits']):
        raise ValueError('ทุกคลิปต้องระบุบทบาท FULL หรือเรื่องย่อย')
    duration=round(sum(r['end']-r['start'] for r in resolved[0]['ranges']),2)
    if duration>180:raise ValueError(f'FULL ยาว {duration}s เกิน180s ต้องเกลาภายในเรื่องโดยไม่ทำสาระสำคัญหาย')
    chosen=[word_set(e) for e in plan['edits']];units={u['id']:u for u in catalog}
    rows=[];missing=[]
    for requirement in inventory['full_requirements']:
        in_full=any(option_words(o,units)<=chosen[0] for o in requirement['options'])
        in_collection=any(option_words(o,units)<=words for o in requirement['options'] for words in chosen)
        adjustments=[]
        for option in requirement['options']:
            original={(s['unit_id'],i) for s in option['selections'] for i in range(s['first_word'],s['last_word']+1)}
            removed=sorted(original-option_words(option,units))
            if removed:adjustments.append(dict(ignored_boundary_words=[dict(unit_id=uid,word_index=i,text=units[uid]['words'][i]['text']) for uid,i in removed]))
        rows.append(dict(id=requirement['id'],topic=requirement['topic'],scope=requirement['scope'],
            in_full=in_full,in_collection=in_collection,reason=requirement['reason'],anchor_adjustments=adjustments))
        if requirement['scope']=='full' and not in_full or final and requirement['scope']=='collection' and not in_collection:
            missing.append(dict(id=requirement['id'],topic=requirement['topic'],required_source_options=requirement['options']))
    protected=[u['id'] for u in catalog if u['protected'] and (u['id'],0) not in chosen[0]]
    if missing or protected:
        import json
        raise ValueError('FULL/ชุดส่งออกยังขาดสาระจริง '+json.dumps(dict(missing=missing,missing_protected_demo=protected),ensure_ascii=False,separators=(',',':')))
    return dict(full_index=0,seconds=duration,max_seconds=180,requirements=rows,
        check='source anchor coverage ignoring edge hesitations/next-clause heads, and exact duration; semantic scope remains model judgment')


def validate_merges(raw,previous_plan,plan):
    other=[word_set(e) for e in previous_plan['edits']]
    for row in raw['candidate_review']:
        if row['decision']!='merge':continue
        index=int(row['candidate_id'][1:])-1;source=previous_plan['edits'][index]
        peer_sets=[s for j,s in enumerate(other) if j!=index and previous_plan['edits'][j].get('edit_role')!='FULL']
        if not peer_sets:peer_sets=[s for j,s in enumerate(other) if j!=index]
        peers=set().union(*peer_sets)
        distinct=other[index]-peers
        if not distinct:continue
        target=word_set(plan['edits'][row['target_edit_index']])
        retained=any(word_set(dict(selections=[s]))<=target and word_set(dict(selections=[s]))&distinct for s in source['selections'])
        if not retained:raise ValueError('อ้าง merge แต่ไม่มีวลีเฉพาะจาก '+row['candidate_id']+' ในคลิปปลายทาง ต้องเก็บสาระจริงหรือระบุ alternate/drop ตามจริง')


def write_report(folder,report,save):
    save(folder/'full-coverage.json',report)
    lines=['# ตรวจ FULL และสาระที่ต้องเก็บ','',f"FULL {report['seconds']:.2f} วินาที / ไม่เกิน180วินาที",'',
        'ตรวจการเลือกช่วงคำพูดจริง ไม่ใช่การรับรองความหมาย ภาพหรือการฟัง','']
    for row in report['requirements']:
        lines.append(f"- {row['id']} · {row['topic']} · {row['scope']} · FULL={row['in_full']} / ชุด={row['in_collection']}: {row['reason']}")
    (folder/'FULL-COVERAGE.md').write_text('\n'.join(lines),encoding='utf8')
