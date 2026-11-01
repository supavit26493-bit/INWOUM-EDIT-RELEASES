"""2.1-only scoped, checkpointed assembly with bounded response batches."""
import copy,json,re
from parallel_requests import call

POLICY='sales-2.1-scoped-batches-v3'
PREFIX='approved-map-clip-'

def scope_correction(req,label):
    data=json.loads(req['input']);chosen=set();event_ids=set()
    if isinstance(data,list):data=[r for r in data if r.get('id')==label]
    elif 'invalid_selections' in data:
        data['invalid_selections']=[r for r in data['invalid_selections'] if r['id']==label]
    elif 'affected_edits' in data:
        affected=data['affected_edits']
        if isinstance(affected,dict):data['affected_edits']={label:affected[label]};rows=list(data['affected_edits'].values())
        else:data['affected_edits']=[r for r in affected if r['id']==label];rows=data['affected_edits']
        for row in rows:
            chosen.update(s['unit_id'] for s in row.get('selections',[]))
            event_ids.update(s['event_id'] for s in row.get('selections',[]) if s.get('event_id'))
            chosen.update(u['id'] for u in row.get('units',[]))
        events=data.get('events',[]);lookup={e['id']:e for e in events}
        while True:
            old=set(event_ids)
            for eid in list(event_ids):event_ids.update(lookup.get(eid,{}).get('requires',[]))
            if old==event_ids:break
        data['events']=[e for e in events if e['id'] in event_ids]
        for e in data['events']:chosen.update(e['unit_ids'])
        if 'original_input' in data:
            original=data['original_input'];original['units']=[u for u in original['units'] if u['id'] in chosen]
            original.pop('approved_story_map',None);original.pop('source_bindings',None)
        if 'issues' in data and label.isdigit():data['issues']=[r for r in data['issues'] if r.get('edit_index')==int(label)]
    req['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    choices=req['text']['format']['schema'].get('$defs',{}).get('source_selection',{}).get('anyOf')
    if chosen and choices:
        choices[:]=[c for c in choices if c['properties']['unit_id']['enum'][0] in chosen]
    return req

def correction(base,folder,key,stage):
    """Keep targeted repair outputs bounded too; preserve every requested repair key."""
    import transport21
    if transport21.folder_enabled(folder):return transport21.correction(base,folder,key,stage)
    schema=base['text']['format']['schema'];result={}
    fields=list(schema['properties'])
    ledger=folder/'api-usage.json'
    allowance=(len(json.loads(ledger.read_text('utf8'))) if ledger.exists() else 0)+len(fields)+1
    for index,label in enumerate(fields):
        req=copy.deepcopy(base);form=req['text']['format'];sc=form['schema']
        sc['properties']={label:sc['properties'][label]};sc['required']=[label]
        req=scope_correction(req,label)
        data=json.loads(req['input']);affected=data.get('affected_edits',{}) if isinstance(data,dict) else {}
        rows=list(affected.values()) if isinstance(affected,dict) else affected
        selection_count=sum(len(r.get('selections',[])) for r in rows)
        # Size the repair allowance from the supplied edit, not the money left.
        req['max_output_tokens']=min(6000,max(2000,1000+60*selection_count))
        req['instructions']+='\nOUTPUT SCOPE: Return ONLY the one required repair key in this schema. Other repairs are separate requests; keep all supplied immutable events, source constraints and edit indices unchanged.'
        raw=call(req,folder,key,PREFIX+'fix-'+stage+'-'+str(index+1).zfill(4),max_calls=allowance)
        if set(raw)!={label}:raise ValueError('คำตอบแก้เฉพาะจุดไม่ตรงรายการที่ขอ')
        result.update(raw)
    return result

def enabled(config):
    return config.get('content_mode','sales')=='sales' and config.get('sales_style')in ('sales_2_1','sales_2_2')

def clip_request(base,mid):
    req=copy.deepcopy(base)
    schema=req['text']['format']['schema'];edits=schema['properties']['edits']
    edits['properties']={mid:edits['properties'][mid]};edits['required']=[mid]
    # Whole-source omissions are inherited once, from the approved map, after merging.
    schema['properties']['unused']['maxItems']=0
    data=json.loads(req['input']);story=data['approved_story_map']
    story['versions']=[v for v in story['versions'] if v['id']==mid]
    story['reserve_takes']=[v for v in story.get('reserve_takes',[]) if v['id']==mid]
    data['assembly_scope']=dict(map_id=mid,policy='sales-2.1-per-clip-v1')
    req['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    req['max_output_tokens']=32768
    req['instructions']+='''\nASSEMBLY SCOPE: Return ONLY the single required edit key in this response.
The approved story map remains authoritative. Make this one complete clip, <=180 seconds;
do not invent or remove planned versions. Other clips are assembled in separate requests.
Return unused=[]; the program inherits whole-source unused reasons from the approved map
after all clips are assembled. Do not repeat a whole-source inventory here.
Declare only events needed by this clip, including prerequisites. Event IDs are local to this
response and will be namespaced without changing selections or event dependencies.
Keep name/purpose/review_note concise. Preserve exact phrase indices and coherent passages.'''
    return req

def legacy_run(base,folder,key,progress):
    from engine import save
    ids=list(base['text']['format']['schema']['properties']['edits']['properties'])
    result=dict(edits={},unused=[],review_note='')
    notes=[];checkpoint=dict(policy='sales-2.1-per-clip-v1',planned_ids=ids,completed_ids=[],complete=False)
    save(folder/'draft-batches.json',checkpoint)
    for index,mid in enumerate(ids):
        req=clip_request(base,mid)
        progress(message=f'2.1 ประกอบคลิป {index+1}/{len(ids)} · เก็บผลแต่ละคลิปไว้ทำต่อ')
        # Stage indices are stable; call verifies the request hash before cache reuse.
        raw=call(req,folder,key,PREFIX+str(index+1).zfill(4),max_calls=len(ids)+4)
        if set(raw.get('edits',{}))!={mid} or raw.get('unused')!=[]:
            raise ValueError('คำตอบประกอบคลิปไม่ตรงชุดที่กำหนด เก็บชุดก่อนหน้าไว้แล้ว')
        edit=copy.deepcopy(raw['edits'][mid])
        if 'events' in base['text']['format']['schema']['properties']:
            events=copy.deepcopy(raw.get('events',[]));names={e['id']:f'b{index+1}:{e["id"]}' for e in events}
            if not events or len(names)!=len(events):raise ValueError('กลุ่มเหตุการณ์ในคลิปไม่ครบ')
            for e in events:
                e['id']=names[e['id']];e['requires']=[names[r] for r in e['requires']]
            for s in edit['selections']:s['event_id']=names[s['event_id']]
            result.setdefault('events',[]).extend(events)
        result['edits'][mid]=edit;notes.append(raw.get('review_note',''))
        checkpoint['completed_ids'].append(mid);save(folder/'draft-batches.json',checkpoint)
    result['review_note']='\n'.join(dict.fromkeys(n for n in notes if n))
    checkpoint['complete']=True;save(folder/'draft-batches.json',checkpoint)
    save(folder/'draft-batches-merged.json',result)
    return result

SHORT={'unit_id':'u','first_word':'f','last_word':'l','event_id':'e'}

def batch_request(base,ids):
    """Scope source via the approved map, preserving dependencies and local context."""
    req=copy.deepcopy(base);schema=req['text']['format']['schema']
    edits=schema['properties']['edits'];edits['properties']={i:edits['properties'][i] for i in ids};edits['required']=ids
    schema['properties']['unused']['maxItems']=0
    data=json.loads(req['input']);story=data['approved_story_map'];bindings=data['source_bindings']
    segments=set()
    if 'FULL' in ids:
        for row in story['full_outline']:segments.update(row['segment_ids'])
        hook_ids={story['full_hook_id']}
    else:hook_ids=set()
    versions=[v for v in story['versions'] if v['id'] in ids]
    reserves=[v for v in story.get('reserve_takes',[]) if v['id'] in ids]
    groups={g for v in versions for g in v.get('group_ids',[])}
    hook_ids.update(v['hook_id'] for v in versions)
    for row in reserves:segments.update(row['segment_ids'])
    for h in story['hooks']:
        if h['id'] in hook_ids:segments.add(h['segment_id'])
    # Reserves may reuse relevant main support from the same topic, not arbitrary footage.
    groups.update(g['id'] for g in story['groups'] if set(g['segment_ids'])&segments)
    for g in story['groups']:
        if g['id'] in groups:segments.update(g['segment_ids'])
    changed=True
    while changed:
        previous=set(segments)
        for link in story['continuity_links']:
            if set(link['segment_ids'])&segments:segments.update(link['segment_ids'])
        changed=previous!=segments
    source=[r['id'] for r in bindings]
    neighbors={source[j] for i,s in enumerate(source) if s in segments for j in range(max(0,i-1),min(len(source),i+2))}
    segments.update(neighbors)
    chosen={u for row in bindings if row['id'] in segments for u in row['unit_ids']}
    # Old/synthetic maps without bindings retain sources, rather than guessing them away.
    if not chosen:chosen={u['id'] for u in data['units']}
    data['units']=[u for u in data['units'] if u['id'] in chosen]
    for u in data['units']:
        if isinstance(u.get('phrases'),dict):u['phrases']=[u['phrases'][str(i)] for i in range(len(u['phrases']))]
        if 'seconds' in u:u['centiseconds']=[round(s*100) for s in u.pop('seconds')]
    data['source_bindings']=[r for r in bindings if r['id'] in segments]
    story['versions']=versions;story['reserve_takes']=reserves
    story['groups']=[g for g in story['groups'] if g['id'] in groups]
    story['hooks']=[h for h in story['hooks'] if h['id'] in hook_ids or h['segment_id'] in segments]
    if 'FULL' not in ids:story.pop('full_outline',None);story.pop('full_hook_id',None)
    story['continuity_links']=[l for l in story['continuity_links'] if set(l['segment_ids'])&segments]
    data['assembly_scope']=dict(map_ids=ids,policy=POLICY)
    # The source enum/schema is also scoped; keep actual phrase bounds unchanged.
    defs=schema.get('$defs',{})
    for choice in defs.get('source_selection',{}).get('anyOf',[]):
        props=choice['properties'];choice['properties']={SHORT.get(k,k):v for k,v in props.items()}
        choice['required']=[SHORT.get(k,k) for k in choice['required']]
    if 'source_selection' in defs:
        defs['source_selection']['anyOf']=[c for c in defs['source_selection']['anyOf'] if c['properties']['u']['enum'][0] in chosen]
        # Units with identical phrase bounds can share one enum branch. This is
        # logically identical to their separate anyOf branches, not looser bounds.
        grouped={}
        for choice in defs['source_selection']['anyOf']:
            shape=copy.deepcopy(choice);shape['properties']['u']['enum']=[]
            signature=json.dumps(shape,sort_keys=True)
            if signature not in grouped:grouped[signature]=shape
            grouped[signature]['properties']['u']['enum']+=choice['properties']['u']['enum']
        defs['source_selection']['anyOf']=list(grouped.values())
        patterns=[]
        for choice in defs['source_selection']['anyOf']:
            props=choice['properties'];uids='(?:'+'|'.join(re.escape(u) for u in props['u']['enum'])+')'
            bound='(?:'+'|'.join(str(i) for i in range(props['f']['maximum']+1))+')'
            patterns.append(uids+','+bound+','+bound+(r',[^,]+' if 'e' in props else ''))
        defs['source_selection']={'type':'string','pattern':'^(?:'+'|'.join(patterns)+')$'}
    if 'events' in schema['properties']:
        schema['properties']['events']['items']['properties']['unit_ids']['items']['enum']=[u['id'] for u in data['units']]
    req['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    phrases=sum(len(u['phrases']) for u in data['units'])
    # Few clips may still be long and require substantial reasoning. Reserve a
    # real response floor; money is checked separately before sending the call.
    req['max_output_tokens']=min(22000,max(12000,4000+600*len(ids)))
    req['instructions']+='''\nSCOPED BATCH: Assemble ONLY the edit keys required by this schema, all of them.
The supplied source pool includes approved topics, dependencies and neighboring context. Use only
these units; do not invent missing speech. A reserve may reuse relevant main support in this pool.
Return unused=[]; unused source decisions are inherited locally after all batches.
phrases is a zero-based array. centiseconds[i]/100 is the exact supplied rounded duration of phrase i.
Each selection is ONE compact string "unit_id,first_word,last_word,event_id" (omit event_id only
when the schema has no events). All source phrase indices and inclusive bounds remain exact.
Keep name/purpose/review_note brief; no repeated inventory. Events are local to this batch.
Do not pad clips or remove planned clips to reduce output. All existing editorial rules still apply.'''
    return req

def run(base,folder,key,progress):
    from engine import save
    save(folder/'draft-batch-base-request.json',base)
    checkpoint=folder/'draft-batches.json'
    if checkpoint.exists() and json.loads(checkpoint.read_text('utf8')).get('policy')=='sales-2.1-per-clip-v1':
        return legacy_run(base,folder,key,progress)
    if checkpoint.exists() and json.loads(checkpoint.read_text('utf8')).get('policy')=='sales-2.1-scoped-batches-v2':
        old=json.loads(checkpoint.read_text('utf8'));ledger=folder/'api-usage.json'
        rows=json.loads(ledger.read_text('utf8')) if ledger.exists() else []
        if old.get('completed_ids') or any(r.get('stage','').startswith(PREFIX+'batch2-') and r.get('status')=='sent' for r in rows):
            raise ValueError('งานชุดรุ่นก่อนมีผลที่ต้องตรวจยืนยัน จึงไม่ส่งคำขอใหม่ทับอัตโนมัติ')
    ids=list(base['text']['format']['schema']['properties']['edits']['properties'])
    batches=[];pending=[]
    for mid in ids:
        candidate=pending+[mid]
        # Bound expected response size, not source character count: overlapping
        # topics share a source pool, so batching them avoids paying for it again.
        if pending and 1500+460*len(candidate)>12000:
            batches.append(pending);pending=[mid]
        else:pending=candidate
    if pending:batches.append(pending)
    result=dict(edits={},unused=[],review_note='');notes=[]
    state=dict(policy=POLICY,planned_ids=ids,batches=batches,completed_ids=[],complete=False)
    save(checkpoint,state)
    for index,mids in enumerate(batches):
        req=batch_request(base,mids)
        progress(message=f'2.1 ประกอบชุด {index+1}/{len(batches)} · จำกัดงบรวม $0.50')
        raw=call(req,folder,key,PREFIX+'batch3-'+str(index+1).zfill(4),max_calls=len(batches)+4)
        if set(raw.get('edits',{}))!=set(mids) or raw.get('unused')!=[]:raise ValueError('คำตอบประกอบคลิปไม่ครบชุด เก็บผลก่อนหน้าไว้แล้ว')
        raw=copy.deepcopy(raw);events=raw.get('events',[])
        names={e['id']:f'g{index+1}:{e["id"]}' for e in events}
        for e in events:e['id']=names[e['id']];e['requires']=[names[r] for r in e['requires']]
        if events:result.setdefault('events',[]).extend(events)
        for mid in mids:
            edit=raw['edits'][mid]
            decoded=[]
            for s in edit['selections']:
                if isinstance(s,str):
                    pattern=req['text']['format']['schema']['$defs']['source_selection']['pattern']
                    if not re.fullmatch(pattern,s):raise ValueError('รูปแบบช่วงคำย่อไม่ตรงขอบคำต้นฉบับ')
                    parts=s.split(',');row=dict(unit_id=parts[0],first_word=int(parts[1]),last_word=int(parts[2]))
                    if len(parts)==4:row['event_id']=parts[3]
                else:row={next((k for k,v in SHORT.items() if v==field),field):value for field,value in s.items()}
                decoded.append(row)
            edit['selections']=decoded
            for s in edit['selections']:
                if names:s['event_id']=names[s['event_id']]
            result['edits'][mid]=edit
        notes.append(raw.get('review_note',''));state['completed_ids']+=mids;save(checkpoint,state)
    result['review_note']='\n'.join(dict.fromkeys(n for n in notes if n));state['complete']=True
    save(checkpoint,state);save(folder/'draft-batches-merged.json',result)
    return result
