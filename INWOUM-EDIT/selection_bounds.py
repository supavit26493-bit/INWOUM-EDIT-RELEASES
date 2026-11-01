"""Exact source bounds and small model-authored corrections; never clamp selections."""
import copy
import json
from planner import obj


def issues(raw, catalog):
    units={u['id']:u for u in catalog};bad=[]
    for i,edit in enumerate(raw.get('edits',[])):
        for j,s in enumerate(edit.get('selections',[])):
            u=units.get(s.get('unit_id'));n=max(1,len(u['words'])) if u else 0
            a,b=s.get('first_word'),s.get('last_word')
            if type(a)!=int or type(b)!=int or not 0<=a<=b<n:
                bad.append(dict(edit_index=i,selection_index=j,selection=s,valid_last=n-1))
    return bad


def bounded_schema(schema,catalog):
    schema=copy.deepcopy(schema)
    choices=[]
    for u in catalog:
        last=max(1,len(u['words']))-1
        choices.append(obj(dict(unit_id=dict(type='string',enum=[u['id']]),
            first_word=dict(type='integer',minimum=0,maximum=last),
            last_word=dict(type='integer',minimum=0,maximum=last))))
    def visit(node):
        if isinstance(node,dict):
            for key,value in list(node.items()):
                if key=='selections' and isinstance(value,dict) and value.get('type')=='array':
                    value['items']={'$ref':'#/$defs/source_selection'}
                else:visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(schema)
    schema.setdefault('$defs',{})['source_selection']=dict(anyOf=choices)
    return schema


def correction_request(base,raw,catalog,bad):
    units={u['id']:u for u in catalog};fields={};rows=[]
    for issue in bad:
        i,j=issue['edit_index'],issue['selection_index'];s=issue['selection'];uid=s.get('unit_id')
        # Unknown IDs need a full source-aware repair, not a guessed local substitution.
        if uid not in units:raise ValueError('ไม่พบท่อน '+str(uid)+' ต้องตรวจแผนเต็ม')
        words=units[uid]['words'] or [units[uid]];last=len(words)-1
        label=f'e{i}_s{j}'
        fields[label]=obj(dict(first_word=dict(type='integer',minimum=0,maximum=last),
            last_word=dict(type='integer',minimum=0,maximum=last),reason=dict(type='string')))
        rows.append(dict(id=label,**issue,edit_name=raw['edits'][i]['name'],
            purpose=raw['edits'][i]['purpose'],indexed_phrases={str(k):w['text'] for k,w in enumerate(words)},
            neighboring_selections=raw['edits'][i]['selections'][max(0,j-1):j+2]))
    request=copy.deepcopy(base)
    request.update(max_output_tokens=4000,input=json.dumps(dict(invalid_selections=rows),ensure_ascii=False,separators=(',',':')),
        instructions='Correct ONLY these invalid source phrase indices. Indices are zero-based and inclusive. '
        'Choose the intended complete spoken clause using the supplied indexed source words and edit purpose. '
        'Preserve negation, conditions and uncertainty. Do not simply clamp or invent words. '
        'Keep first_word <= last_word. Explain each correction in Thai. No other edits are requested.')
    request['text']['format'].update(name='source_bounds_correction',schema=obj(fields))
    request['reasoning']=dict(effort='low')
    return request


def apply_corrections(raw,catalog,bad,answer):
    fixed=copy.deepcopy(raw)
    expected={f"e{x['edit_index']}_s{x['selection_index']}" for x in bad}
    if set(answer)!=expected:raise ValueError('คำตอบแก้ขอบคำไม่ครบ')
    for issue in bad:
        i,j=issue['edit_index'],issue['selection_index'];row=answer[f'e{i}_s{j}']
        if not isinstance(row.get('reason'),str) or not row['reason'].strip():raise ValueError('ขาดเหตุผลแก้ขอบคำ')
        fixed['edits'][i]['selections'][j].update(first_word=row['first_word'],last_word=row['last_word'])
    if issues(fixed,catalog):raise ValueError('คำตอบแก้ขอบคำยังอ้างต้นฉบับไม่ถูกต้อง')
    return fixed


def compact_data(data):
    """Remove repeated derived bookkeeping, preserving every source phrase and decision."""
    data=copy.deepcopy(data)
    for u in data.get('units',[]):
        u['last_phrase_index']=len(u['phrases'])-1
    plan=data.get('current_plan')
    if plan:
        for key in ('source_review','notes'):plan.pop(key,None)
        for edit in plan['edits']:
            for selection in edit['selections']:
                for key in ('reason','reason_source'):selection.pop(key,None)
    for script in data.get('assembled_scripts',[]):
        # Selection indices already occur in current_plan; full source is in units.
        script.pop('selections',None);script.pop('outline',None)
    for row in data.get('collection_evidence',{}).get('candidates',[]):
        # The same assembled text is present once in assembled_scripts.
        row.pop('assembled_text',None)
    return data
