"""Lossless request compaction for sales 2.1; editorial validators stay authoritative."""
import copy,json

POLICY='sales-2.1-transport-v1'

def enabled(config):
    return config.get('content_mode','sales')=='sales' and config.get('sales_style')=='sales_2_1'

def folder_enabled(folder):
    path=folder/'config.json'
    return path.exists() and enabled(json.loads(path.read_text('utf8')))

def compact_bounds(schema):
    """Union identical bounds into enums, preserving the exact accepted selections."""
    result=copy.deepcopy(schema)
    source=result.get('$defs',{}).get('source_selection',{})
    if 'anyOf' not in source:return result
    grouped={}
    for branch in source['anyOf']:
        shape=copy.deepcopy(branch);prop=shape.get('properties',{}).get('unit_id')
        if not prop or not prop.get('enum'):return result
        ids=prop['enum'];prop['enum']=[]
        signature=json.dumps(shape,sort_keys=True,separators=(',',':'))
        grouped.setdefault(signature,shape)['properties']['unit_id']['enum'].extend(ids)
    source['anyOf']=list(grouped.values())
    return result

def repair_request(base,labels):
    """Union the already dependency-scoped inputs; never drop event prerequisites."""
    from batched_sales_draft import scope_correction
    req=copy.deepcopy(base);schema=req['text']['format']['schema']
    schema['properties']={k:schema['properties'][k] for k in labels};schema['required']=list(labels)
    parts=[json.loads(scope_correction(copy.deepcopy(req),label)['input']) for label in labels]
    data=copy.deepcopy(parts[0])
    evidence=json.loads(base['input']).get('required_reserve_evidence',{}) if isinstance(json.loads(base['input']),dict) else {}
    if isinstance(data,dict) and evidence:
        data['required_reserve_evidence']={k:v for k,v in evidence.items() if k in labels}
    for part in parts[1:]:
        if isinstance(data,list):
            data.extend(part);continue
        for field in ('events','issues'):
            if field in part:
                for row in part[field]:
                    if row not in data[field]:data[field].append(row)
        if 'affected_edits' in part:
            if isinstance(data['affected_edits'],dict):data['affected_edits'].update(part['affected_edits'])
            else:data['affected_edits'].extend(part['affected_edits'])
        if 'original_input' in part:
            units=data['original_input']['units'];known={u['id'] for u in units}
            units.extend(u for u in part['original_input']['units'] if u['id'] not in known)
    # Drop only prior planning metadata, not source phrases, constraints or instructions.
    if isinstance(data,dict) and 'original_input' in data:
        original=data['original_input']
        for field in ('repair_findings','reviewer_deferred','assembly_scope'):original.pop(field,None)
        # Phrase dictionaries carry the same contiguous indices as arrays.
        for unit in original.get('units',[]):
            phrases=unit.get('phrases')
            if isinstance(phrases,dict) and set(phrases)=={str(i) for i in range(len(phrases))}:
                unit['phrases']=[phrases[str(i)] for i in range(len(phrases))]
    chosen=set();events=set()
    if isinstance(data,list):rows=data
    else:
        affected=data.get('affected_edits',{})
        rows=list(affected.values()) if isinstance(affected,dict) else affected
        events={e['id'] for e in data.get('events',[])}
        for event in data.get('events',[]):chosen.update(event['unit_ids'])
        chosen.update(u['id'] for u in data.get('original_input',{}).get('units',[]))
    for row in rows:
        chosen.update(s['unit_id'] for s in row.get('selections',[]))
        chosen.update(u['id'] for u in row.get('units',[]))
    choices=schema.get('$defs',{}).get('source_selection',{}).get('anyOf',[])
    if chosen:
        kept=[]
        for choice in choices:
            ids=[u for u in choice['properties']['unit_id']['enum'] if u in chosen]
            if ids:
                choice['properties']['unit_id']['enum']=ids
                if events and 'enum' in choice['properties'].get('event_id',{}):
                    choice['properties']['event_id']['enum']=[e for e in choice['properties']['event_id']['enum'] if e in events]
                kept.append(choice)
        if choices:schema['$defs']['source_selection']['anyOf']=kept
    req['text']['format']['schema']=compact_bounds(schema)
    req['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    count=sum(len(row.get('selections',[])) for row in rows)
    req['max_output_tokens']=min(10000,max(4000,1400+60*count))
    req['instructions']+='\nREPAIR OUTPUT: Return exactly the required repair keys together. Retain all editorial rules and immutable events. phrases arrays use zero-based inclusive indices. Repair only listed defects; keep unaffected passages and meaningful emphasis. Keep reasons brief; do not repeat narration.'
    return req

def correction(base,folder,key,stage):
    from parallel_requests import call
    from engine import save
    fields=list(base['text']['format']['schema']['properties']);batches=[];pending=[]
    for label in fields:
        trial=repair_request(base,pending+[label]);data=json.loads(trial['input'])
        affected=data.get('affected_edits',{}) if isinstance(data,dict) else data
        rows=list(affected.values()) if isinstance(affected,dict) else affected
        count=sum(len(r.get('selections',[])) for r in rows)
        if pending and (len(pending)>=3 or count>90 or len(trial['input'])>45000):
            batches.append(pending);pending=[label]
        else:pending.append(label)
    if pending:batches.append(pending)
    result={};metrics=[]
    ledger=folder/'api-usage.json'
    allowance=(len(json.loads(ledger.read_text('utf8'))) if ledger.exists() else 0)+len(batches)+1
    for index,labels in enumerate(batches):
        req=repair_request(base,labels)
        stage_id='approved-map-clip-fix-economy1-'+stage+'-'+str(index+1).zfill(4)
        raw=call(req,folder,key,stage_id,max_calls=allowance)
        if set(raw)!=set(labels):raise ValueError('คำตอบแก้เฉพาะจุดไม่ตรงรายการที่ขอ')
        result.update(raw);metrics.append(dict(keys=labels,input_chars=len(req['input']),schema_chars=len(json.dumps(req['text']['format']['schema'],ensure_ascii=False))))
    save(folder/('economy-'+stage+'.json'),dict(policy=POLICY,batches=metrics,manual_edits=False))
    return result

def map_request(request,count):
    result=copy.deepcopy(request)
    def walk(node):
        if isinstance(node,dict):
            if node.get('type')=='integer' and node.get('enum')==list(range(1,count+1)):
                node.pop('enum');node.update(minimum=1,maximum=count)
            for value in node.values():walk(value)
        elif isinstance(node,list):
            for value in node:walk(value)
    walk(result['text']['format']['schema'])
    return result

