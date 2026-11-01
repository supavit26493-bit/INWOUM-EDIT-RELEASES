"""Collection-level evidence and release decisions; never edit source selections locally."""
import itertools
import copy
from planner import obj
import editorial

POLICY='''COLLECTION CURATION — latest user correction overrides earlier expansion/preservation advice.
Make as many DISTINCT COMPLETE useful stories as the footage supports. Do not publish every usable take,
feature, audience noun or prepared hook as another tiny clip. There is no minimum duration or target count.
A short clip is valid only when it stands alone and has real value separate from the other releases.
Plan viewer stories before feature lists: related complementary details can form one coherent answer.
Choose the strongest take for a repeated premise. Store weaker interchangeable takes as alternatives.
An alternative library supplements the main collection and must not replace complete principal stories.
Shared bodies are allowed for a substantial new audience/problem/payoff, not a superficial wording change.
Compare the WHOLE collection and explicitly merge/drop redundant drafts. Earlier drafts are candidates,
not approved deliverables that must survive. Do not discard useful new clauses while merging; incorporate
them into the best relevant story. No fixed 10-second deletion, padding, usage quota or clip-count quota.
Use Thai titles, reasons and reports. Preserve negatives, conditions and product referents. Do not promote
speculative technical/safety claims into verified explanations. Keep useful comparative price language.
Read every assembled boundary against its full source unit; remove stutters, abandoned clauses and dangling
pronouns. Review visible demonstrations only to the extent already verified; do not claim listening.'''


def word_set(edit):
    return {(s['unit_id'],i) for s in edit['selections'] for i in range(s['first_word'],s['last_word']+1)}


def context(plan,catalog,resolved):
    scripts=editorial.assembled_scripts(plan,catalog)
    candidates=[]
    for i,(edit,script,cut) in enumerate(zip(plan['edits'],scripts,resolved)):
        candidates.append(dict(candidate_id=f'C{i+1:03d}',name=edit['name'],content_group=edit['content_group'],
            seconds=round(sum(r['end']-r['start'] for r in cut['ranges']),2),assembled_text=script['assembled_text']))
    pairs=[]
    for a,b in itertools.combinations(range(len(plan['edits'])),2):
        x,y=word_set(plan['edits'][a]),word_set(plan['edits'][b]);shared=len(x&y)
        if shared:
            pairs.append(dict(a=candidates[a]['candidate_id'],b=candidates[b]['candidate_id'],
                a_source_overlap=round(shared/len(x),3),b_source_overlap=round(shared/len(y),3)))
    return dict(candidates=candidates,source_overlap=pairs,
        note='Source overlap is evidence only. Different takes may express the same meaning with no word-ID overlap.')


def extend_schema(schema):
    schema['properties']['candidate_review']=dict(type='array',items=obj(dict(
        candidate_id=dict(type='string'),decision=dict(type='string',enum=['keep','merge','alternate','drop']),
        target_edit_index=dict(type='integer'),reason=dict(type='string'))))
    schema['properties']['release_review']=dict(type='array',items=obj(dict(
        edit_index=dict(type='integer'),standalone_reason=dict(type='string'),distinct_reason=dict(type='string'),
        short_clip_reason=dict(type='string'),boundary_review=dict(type='string'),claims_review=dict(type='string'))))
    schema['required']+=['candidate_review','release_review']
    return schema


def validate(raw,plan,collection,resolved):
    expected={c['candidate_id'] for c in collection['candidates']};rows=raw['candidate_review']
    if len(rows)!=len(expected) or {r['candidate_id'] for r in rows}!=expected:
        raise ValueError('ตรวจการคัดคลิปเดิมไม่ครบทุก candidate')
    count=len(plan['edits'])
    for row in rows:
        target=row['target_edit_index']
        if not row['reason'].strip() or type(target)!=int or not -1<=target<count:
            raise ValueError('เหตุผลหรือคลิปปลายทางของการคัดชุดไม่ถูกต้อง')
        if row['decision'] in ('keep','merge') and target<0:
            raise ValueError('คลิปที่เก็บ/รวมต้องอ้างคลิปส่งออกจริง')
    rows=raw['release_review']
    if len(rows)!=count or {r['edit_index'] for r in rows}!=set(range(count)):
        raise ValueError('ยังตรวจความสมบูรณ์และความต่างไม่ครบทุกคลิปส่งออก')
    for row in rows:
        if any(not row[k].strip() for k in ('standalone_reason','distinct_reason','boundary_review','claims_review')):
            raise ValueError('ขาดเหตุผลทบทวนคุณภาพคลิป')
        seconds=sum(r['end']-r['start'] for r in resolved[row['edit_index']]['ranges'])
        if seconds<10 and not row['short_clip_reason'].strip():
            raise ValueError('คลิปต่ำกว่า 10 วินาทีต้องระบุเหตุผลว่าคุ้มแยกเผยแพร่ ไม่ใช่บังคับลบ')


def recover_short_reasons(raw,resolved):
    """Reuse the model's substantive rationale, never invent approval of a short edit."""
    fixed=copy.deepcopy(raw);changes=[]
    for row in fixed.get('release_review',[]):
        i=row['edit_index']
        if type(i)!=int or not 0<=i<len(resolved):continue
        seconds=sum(r['end']-r['start'] for r in resolved[i]['ranges'])
        if seconds<10 and not row['short_clip_reason'].strip() and row['standalone_reason'].strip() and row['distinct_reason'].strip():
            row['short_clip_reason']=row['standalone_reason']+'; '+row['distinct_reason']
            row['short_reason_source']='model_standalone_and_distinct_recovered'
            changes.append(dict(edit_index=i,seconds=round(seconds,2),reason=row['short_clip_reason'],source=row['short_reason_source']))
    return fixed,changes


def write_report(folder,raw,collection,previous_plan,plan,save):
    byid={f'C{i+1:03d}':edit for i,edit in enumerate(previous_plan['edits'])}
    alternatives=[dict(review=r,edit=byid[r['candidate_id']]) for r in raw['candidate_review'] if r['decision']=='alternate']
    save(folder/'alternate-takes.json',alternatives)
    save(folder/'collection-review.json',dict(candidate_review=raw['candidate_review'],release_review=raw['release_review']))
    lines=['# คัดความซ้ำทั้งชุด','',f"ก่อนคัด {len(collection['candidates'])} คลิป → ส่งออก {len(plan['edits'])} คลิป; เทคทางเลือกพักไว้ {len(alternatives)}",'',
        'รายการนี้เป็นการทบทวนข้อความด้วย AI ไม่ใช่การรับรองภาพ/การฟังหรือความถูกต้องของข้ออ้าง','']
    for row in raw['candidate_review']:
        target='ไม่ส่งออก' if row['target_edit_index']<0 else f"คลิป {row['target_edit_index']+1}"
        lines.append(f"- {row['candidate_id']} · {byid[row['candidate_id']]['name']} → {row['decision']} / {target}: {row['reason']}")
    lines+=['','## เหตุผลของคลิปส่งออก','']
    for row in raw['release_review']:
        lines += [f"### {row['edit_index']+1}. {plan['edits'][row['edit_index']]['name']}",row['standalone_reason'],row['distinct_reason'],row['short_clip_reason'],row['boundary_review'],row['claims_review'],'']
    (folder/'COLLECTION-REVIEW.md').write_text('\n'.join(lines),encoding='utf8')
