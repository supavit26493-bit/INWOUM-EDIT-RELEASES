"""One source-complete planning call. No audio, cuts, or implicit paid retries."""
import hashlib,json,re,copy
from pathlib import Path
from planner import obj
from parallel_requests import call
import runtime_contract as runtime
MAP_REVISION='source-quotes-patch-v3'

def canonicalize_reference_catalogs(answer,segments,config):
    """Default candidate catalogs are sets of available source IDs, not edit playback.

    A source segment can supply both a hook and another clause later. Keep its
    first catalog reference, leaving quotes, outline beats and ordered links intact.
    Unknown/empty references remain invalid; never guess or remove source speech.
    """
    if config.get('content_mode','sales')!='sales' or config.get('sales_style','original') not in ('natural','natural_v2','sales_2_1','sales_2_2'):return answer,[]
    fixed=copy.deepcopy(answer);known={s['id'] for s in segments};changes=[]
    for section in ('groups','versions','full_outline','reserve_takes'):
        for index,row in enumerate(fixed.get(section,[])):
            refs=row.get('segment_ids')
            if not isinstance(refs,list) or not refs or any(not isinstance(v,str) or v not in known for v in refs):continue
            unique=list(dict.fromkeys(refs))
            if unique!=refs:
                changes.append(dict(path=f'{section}[{index}].segment_ids',id=row.get('id',''),
                    before=refs,after=unique,reason='Repeated candidate source reference; not a repeated cut'))
                row['segment_ids']=unique
    return fixed,changes

def locally_recoverable_references(folder,config):
    """Avoid a paid map patch when completed candidate metadata can be fixed locally."""
    if config.get('sales_style','original') not in ('natural','natural_v2','sales_2_1','sales_2_2'):return False
    try:
        candidate=json.loads((folder/'map-candidate.json').read_text('utf8'))
        segments=json.loads((folder/'grouping-source.json').read_text('utf8'))['segments']
        fixed,changes=canonicalize_reference_catalogs(candidate,segments,config)
        if not changes:return False
        validate(fixed,segments)
    except (OSError,ValueError,KeyError,TypeError):return False
    return True

def completed_local_recovery(folder,segments,config,transcript_bytes):
    """Recover duplicate candidate IDs only, with authenticated completed API provenance."""
    from planner import fingerprint
    try:
        candidate=json.loads((folder/'map-candidate.json').read_text('utf8'))
        old_source=json.loads((folder/'grouping-source.json').read_text('utf8'))
        req=json.loads((folder/'sales-story-map-request.json').read_text('utf8'))
        reply=json.loads((folder/'sales-story-map-response.json').read_text('utf8'))
        ledger=json.loads((folder/'api-usage.json').read_text('utf8'))
        digest=fingerprint(req)
        if reply.get('request_hash')!=digest or not any(r.get('stage')=='sales-story-map' and r.get('status')=='completed' and r.get('request_hash')==digest for r in ledger):return None
        if old_source['transcript_sha256']!=hashlib.sha256(transcript_bytes).hexdigest():return None
        if (folder/'rules-used.txt').read_text('utf8')!=runtime.rules_for(config):return None
        if req.get('model')!=config.get('model') or req.get('reasoning',{}).get('effort')!=config.get('effort','medium'):return None
        if json.loads(req['input']).get('job_instructions','')!=config.get('notes',''):return None
        fixed,changes=canonicalize_reference_catalogs(candidate,segments,config)
        if not changes:return None
        validate(fixed,segments)
        return fixed,changes
    except (OSError,ValueError,KeyError,TypeError):return None

def is_map_job(config,folder):
    return bool(config.get('group_preview') or (config.get('editorial_workflow')=='sales-v8'
        and not config.get('approved_story_map_job') and (folder/'grouping-source.json').is_file()))

def output_limit_retry(folder,config):
    """Prepare one explicit new-job retry for a KNOWN output-limit result, never send."""
    ledger_file=folder/'api-usage.json'
    if config.get('known_output_retry') or not ledger_file.exists():return None
    ledger=json.loads(ledger_file.read_text('utf8'))
    if not ledger or ledger[-1].get('status')!='failed' or ledger[-1].get('incomplete_reason')!='max_output_tokens':return None
    from model_catalog import prepare_request
    used=(ledger[-1].get('usage') or {}).get('output_tokens',0)
    requested=min(32768,max(24000,config.get('map_output_tokens',11000)*2,used*2))
    wire=prepare_request(dict(model=config['model'],max_output_tokens=requested))
    if wire['max_output_tokens']<=used:raise ValueError('โมเดลนี้ถึงเพดานคำตอบแล้ว เลือกโมเดลที่รองรับคำตอบยาวกว่าแล้วจัดกลุ่มจากข้อความเดิม')
    return dict(config,map_output_tokens=requested,known_output_retry=True,group_preview=True)

def validation_retry(folder,config):
    """One explicit retry for a completed response rejected locally, never an unknown request."""
    path=folder/'map-validation.json';ledger=folder/'api-usage.json'
    if not path.exists() or not ledger.exists() or config.get('validation_retry_count',0)>=1:return None
    state=json.loads(path.read_text('utf8'));calls=json.loads(ledger.read_text('utf8'))
    if state.get('valid') is not False or not calls or calls[-1].get('status')!='completed' or not (folder/'map-candidate.json').exists():return None
    if locally_recoverable_references(folder,config):return None
    return dict(config,refine_map_job=folder.name,effort='low',map_output_tokens=16000,
        validation_retry_count=config.get('validation_retry_count',0)+1,blocking_only=True,validation_error=state['error'])

def patch_schema(ids,include_reserves=False):
    fields=schema(ids)['properties'];text=dict(type='string');strings=dict(type='array',items=text)
    def rows(name):
        result=copy.deepcopy(fields[name]);result.pop('minItems',None);result.pop('maxItems',None);return result
    result=obj(dict(hook_updates=rows('hooks'),hook_additions=rows('hooks'),remove_hooks=strings,
        version_updates=rows('versions'),version_additions=rows('versions'),remove_versions=strings,
        group_updates=rows('groups'),group_additions=rows('groups'),remove_groups=strings,
        full_hook_id=text,full_outline=rows('full_outline'),segment_review_updates=rows('segment_review'),
        continuity_additions=rows('continuity_links'),review_note=text))
    if include_reserves:
        import sales_reserve
        reserve=copy.deepcopy(sales_reserve.map_schema(schema(ids))['properties']['reserve_takes'])
        reserve.pop('maxItems',None)
        result['properties'].update(reserve_updates=copy.deepcopy(reserve),reserve_additions=copy.deepcopy(reserve),remove_reserves=strings)
        result['required']+=['reserve_updates','reserve_additions','remove_reserves']
    return result

def apply_map_patch(previous,patch):
    """Unmentioned stories and reviews retain their values; no implicit deletions."""
    result=copy.deepcopy(previous)
    operations=[
        ('hooks','id','hook_updates','hook_additions','remove_hooks'),
        ('versions','id','version_updates','version_additions','remove_versions'),
        ('groups','id','group_updates','group_additions','remove_groups'),
        ('segment_review','segment_id','segment_review_updates',None,None)]
    if any(k in patch for k in ('reserve_updates','reserve_additions','remove_reserves')):
        result.setdefault('reserve_takes',[])
        operations.append(('reserve_takes','id','reserve_updates','reserve_additions','remove_reserves'))
    for plural,field,update,addition,removal in operations:
        originals=result[plural];existing={r[field] for r in originals};deletes=patch[removal] if removal else []
        updates=patch[update];adds=patch[addition] if addition else []
        update_ids=[r[field] for r in updates];add_ids=[r[field] for r in adds]
        if len(deletes)!=len(set(deletes)) or set(deletes)-existing or len(update_ids)!=len(set(update_ids)) or set(update_ids)-existing or set(update_ids)&set(deletes):raise ValueError('patchอ้างรายการเดิมผิดหรือซ้ำ')
        if len(add_ids)!=len(set(add_ids)) or set(add_ids)&existing:raise ValueError('patchเพิ่มรหัสที่มีอยู่แล้ว')
        by_id={r[field]:r for r in updates}
        result[plural]=[copy.deepcopy(by_id.get(r[field],r)) for r in originals if r[field] not in deletes]+copy.deepcopy(adds)
    if patch['full_hook_id']:result['full_hook_id']=patch['full_hook_id']
    if patch['full_outline']:result['full_outline']=copy.deepcopy(patch['full_outline'])
    result['review_note']=patch['review_note']
    for row in patch['continuity_additions']:
        if row not in result['continuity_links']:result['continuity_links'].append(copy.deepcopy(row))
    return result

def refinement_request(segments,config,previous):
    findings=audit(previous,segments);focus=set(config.get('review_segment_ids',[]))
    import sales_reserve
    reserve_findings=sales_reserve.map_findings(previous,segments)
    findings+=reserve_findings
    if config.get('blocking_only'):findings=[r for r in findings if r['issue'] in ('extra_full_in_versions','quote_not_verbatim') or r['issue'].startswith('reserve_')]
    for row in findings:
        if row.get('segment_id'):focus.add(row['segment_id'])
        focus.update(row.get('segment_ids',[]))
    ids=[s['id'] for s in segments]
    # Report missing IDs as defects, but never send them as if they were real source.
    unknown=focus-set(ids)
    if unknown and not reserve_findings:raise ValueError('ท่อนที่ขอตรวจอยู่นอกต้นฉบับ')
    focus.intersection_update(ids)
    if reserve_findings:
        norm=lambda t:''.join(t.split())
        for reserve in previous.get('reserve_takes',[]):
            for field in ('opening_quote','recovery_quote','payoff_quote'):
                quote=norm(reserve.get(field,''))
                if quote:focus.update(s['id'] for s in segments if quote in norm(s['text']))
        if not focus:raise ValueError('ไม่มีบริบทต้นฉบับพอสำหรับซ่อมเทคสำรอง จึงไม่ส่ง API โดยไม่มีข้อมูล')
    # Neighboring clauses protect setup/continuation. Do not re-scan all pending footage.
    requested=set(focus)
    for i,sid in enumerate(ids):
        if sid in requested:focus.update(ids[max(0,i-1):i+2])
    for link in previous['continuity_links']:
        if len(link['segment_ids'])<=4 and requested.intersection(link['segment_ids']):focus.update(link['segment_ids'])
    focused=[s for s in segments if s['id'] in focus]
    compact_findings=[{k:v for k,v in row.items() if k not in ('source','quote')} for row in findings]
    data=dict(previous_map={k:copy.deepcopy(previous[k]) for k in ('groups','hooks','full_hook_id','full_outline','versions','continuity_links')},
        source_segments=focused,source_reviews=[r for r in previous['segment_review'] if r['segment_id'] in focus],
        local_review=compact_findings,validation_error=config.get('validation_error',''),job_instructions=config.get('notes',''))
    include_reserves='reserve_takes' in previous
    if include_reserves:data['previous_map']['reserve_takes']=copy.deepcopy(previous['reserve_takes'])
    budget=config.get('map_output_tokens',16000)
    if type(budget)!=int or not 11000<=budget<=32768:raise ValueError('ขอบเขตคำตอบแผนไม่ถูกต้อง')
    req=dict(model=config['model'],store=False,max_output_tokens=budget,reasoning=dict(effort=config.get('effort','low')),
        instructions=runtime.rules_for(config)+'''\nSTEP: targeted map PATCH, not another inventory or complete map.
Return only changed/new/deleted IDs. Empty arrays retain prior values; empty full_hook_id/full_outline retain FULL.
Keep prior groups and reviews unless the supplied issues require a change. Review only supplied source clauses,
their context and user notes. opening_quote is verbatim source; trim_note is a later edit instruction.
Merge repeated FULL explanations into one beat when useful; role names are labels, not required checklist slots.
Use one brief reason per changed item. Do not echo unchanged reviews or whole source text.''',
        input=json.dumps(data,ensure_ascii=False,separators=(',',':')),
        text=dict(format=dict(type='json_schema',name='sales_map_patch',strict=True,schema=patch_schema(ids,include_reserves))))
    if config.get('blocking_only'):
        req['instructions']+='\nFix ONLY the blocking findings listed in local_review. An extra FULL in versions must be explicitly removed or changed to a genuinely focused story; merely renaming the same full collection is not a solution. Do not change other accepted items. Resolve every listed blocker.'
    return req,dict(reviewed_segment_ids=[s['id'] for s in focused],preserved_review_count=len(segments)-len(focused),mode='targeted_patch',blocking_only=bool(config.get('blocking_only')))

def audit(answer,segments):
    """Source evidence and review prompts, never automatic editorial deletions."""
    source={s['id']:s for s in segments};findings=[]
    selected={i for b in answer.get('full_outline',[]) for i in b['segment_ids']}|{i for v in answer.get('versions',[]) for i in v['segment_ids']}
    for h in answer.get('hooks',[]):
        text=source.get(h['segment_id'],{}).get('text','')
        if ''.join(h['opening_quote'].split()) not in ''.join(text.split()):
            findings.append(dict(issue='quote_not_verbatim',hook_id=h['id'],segment_id=h['segment_id'],quote=h['opening_quote'],source=text))
        if re.match(r'^(?:แต่|แต่ว่า|ก็|แบบ|เอ่อ|อ่า|เออ|หรือ)',h['opening_quote']) and not h.get('trim_note','').strip():
            findings.append(dict(issue='hook_opening_needs_trim',hook_id=h['id']))
    for v in answer.get('versions',[]):
        if re.search(r'\bFULL\b|เวอร์ชันเต็ม|ฉบับเต็ม',v['title'],re.I):findings.append(dict(issue='extra_full_in_versions',version_id=v['id']))
    for r in answer.get('segment_review',[]):
        if r['disposition'] in ('usable','alternative','mixed') and r['segment_id'] not in selected:
            findings.append(dict(issue='opportunity_without_version',segment_id=r['segment_id'],source=source[r['segment_id']]['text'],model_reason=r['reason']))
    selected.update(sid for r in answer.get('reserve_takes',[]) for sid in r['segment_ids'])
    findings=[r for r in findings if not (r['issue']=='opportunity_without_version' and r['segment_id'] in selected)]
    beats=answer.get('full_outline',[])
    for i,b in enumerate(beats):
        if b['role'] in ('hook','close'):continue
        for earlier in beats[:i]:
            if earlier['role'] not in ('hook','close') and set(b['segment_ids'])==set(earlier['segment_ids']):
                findings.append(dict(issue='repeated_full_source_needs_distinct_clauses',roles=[earlier['role'],b['role']],segment_ids=b['segment_ids']))
    return findings

def schema(ids):
    text=dict(type='string');refs=dict(type='array',minItems=1,items=dict(type='string',enum=ids));texts=dict(type='array',items=text)
    return obj(dict(groups=dict(type='array',minItems=1,items=obj(dict(id=text,title=text,segment_ids=refs))),
        hooks=dict(type='array',minItems=1,items=obj(dict(id=text,segment_id=dict(type='string',enum=ids),opening_quote=text,trim_note=text,sales_angle=text))),
        full_hook_id=text,full_outline=dict(type='array',minItems=3,items=obj(dict(role=dict(type='string',enum=['hook','problem','demo','mechanism','features','conditions','value','close']),segment_ids=refs,budget_seconds=dict(type='integer',minimum=1,maximum=180),purpose=text))),
        versions=dict(type='array',items=obj(dict(id=text,title=text,kind=dict(type='string',enum=['focused','combined','alternate_take','alternate_hook']),hook_id=text,group_ids=texts,segment_ids=refs,payoff=text,review_condition=text))),
        segment_review=dict(type='array',minItems=len(ids),maxItems=len(ids),items=obj(dict(segment_id=dict(type='string',enum=ids),
            roles=dict(type='array',minItems=1,items=dict(type='string',enum=['hook','problem','demo','reaction','feature','condition','value','buyer','transition','close','chatter'])),
            disposition=dict(type='string',enum=['usable','alternative','mixed','pending','unusable']),reason=text,
            excluded_quotes=dict(type='array',items=obj(dict(quote=text,reason=text)))))),
        continuity_links=dict(type='array',items=obj(dict(segment_ids=refs,reason=text))),review_note=text))

def validate(answer,segments):
    import sales_reserve
    sales_reserve.validate_map(answer,segments)
    source={s['id']:s for s in segments};ids=set(source);norm=lambda t:''.join(t.split())
    def refs(values,where):
        if not values:raise ValueError('แผน '+where+' ไม่มีท่อนอ้างอิง')
        missing=set(values)-ids
        if missing:raise ValueError('แผน '+where+' อ้างท่อนที่ไม่พบ: '+', '.join(sorted(missing)))
        repeated=list(dict.fromkeys(v for v in values if values.count(v)>1))
        if repeated:raise ValueError('แผน '+where+' อ้างรหัสท่อนซ้ำ: '+', '.join(repeated))
    groups={g['id']:g for g in answer['groups']};hooks={h['id']:h for h in answer['hooks']}
    if not groups or len(groups)!=len(answer['groups']) or not hooks or len(hooks)!=len(answer['hooks']):raise ValueError('กลุ่มหรือHookว่าง/รหัสซ้ำ')
    for g in groups.values():refs(g['segment_ids'],'กลุ่ม '+g['id'])
    for h in hooks.values():
        if h['segment_id'] not in ids or not norm(h['opening_quote']) or norm(h['opening_quote']) not in norm(source[h['segment_id']]['text']):raise ValueError('Hook ไม่ตรงวลีต้นฉบับ ห้ามแต่งหรือแก้ASRในคำอ้าง')
    if answer['full_hook_id'] not in hooks:raise ValueError('FULL อ้างHookที่ไม่มี')
    beats=answer['full_outline']
    if not 0<sum(b['budget_seconds'] for b in beats)<=180 or beats[0]['role']!='hook' or beats[-1]['role']!='close':raise ValueError('โครงFULLหรืองบเวลาผิด')
    for i,b in enumerate(beats):refs(b['segment_ids'],'FULL ช่วงที่ '+str(i+1))
    if hooks[answer['full_hook_id']]['segment_id'] not in beats[0]['segment_ids']:raise ValueError('ช่วงเปิดFULLไม่ตรงHook')
    versions=answer['versions'];seen=set();used_hooks={answer['full_hook_id']}
    for v in versions:
        refs(v['segment_ids'],'เวอร์ชัน '+v['id'])
        if not v['id'] or v['id']=='FULL' or v['id'] in seen or not v['group_ids'] or set(v['group_ids'])-set(groups) or v['hook_id'] not in hooks:raise ValueError('เวอร์ชันอ้างรหัส/กลุ่ม/Hookผิด')
        if hooks[v['hook_id']]['segment_id'] not in v['segment_ids']:raise ValueError('เวอร์ชันไม่มีท่อนHookที่อ้าง')
        if v['kind'].startswith('alternate') and not v['review_condition'].strip():raise ValueError('ทางเลือกต้องมีเหตุผล/จุดรอตรวจ')
        if re.search(r'\bFULL\b|เวอร์ชันเต็ม|ฉบับเต็ม',v['title'],re.I):raise ValueError('FULLมีโครงหลักแล้ว ห้ามสร้างFULLซ้ำในรายการย่อย')
        seen.add(v['id']);used_hooks.add(v['hook_id'])
    if used_hooks!=set(hooks):raise ValueError('Hookบางตัวไม่มีเวอร์ชันรองรับ')
    reviews=answer['segment_review']
    if len(reviews)!=len(ids) or {r['segment_id'] for r in reviews}!=ids:raise ValueError('ต้องอ่านครบทุกท่อนหนึ่งครั้ง')
    for r in reviews:
        if not r['reason'].strip():raise ValueError('ท่อนที่พักต้องมีเหตุผล')
        for e in r['excluded_quotes']:
            if not norm(e['quote']) or norm(e['quote']) not in norm(source[r['segment_id']]['text']) or not e['reason'].strip():raise ValueError('ข้อเสนอไม่นับฟุตต้องมีวลีจริงและเหตุผล')
    for i,link in enumerate(answer['continuity_links']):refs(link['segment_ids'],'เหตุการณ์ต่อเนื่องชุดที่ '+str(i+1))
    return answer

def segments_for(source):
    raw=(source/'transcript.json').read_bytes()
    return raw,[dict(id=f'T{i:03d}',start=s['start'],end=s['end'],text=s['text']) for i,s in enumerate(json.loads(raw),1)]

def make_request(segments,config):
    import compact_story_map
    budget=config.get('map_output_tokens',24000)
    if type(budget)!=int or not 11000<=budget<=32768:raise ValueError('ขอบเขตคำตอบแผนไม่ถูกต้อง')
    request=dict(model=config['model'],store=False,max_output_tokens=budget,reasoning=dict(effort=config.get('effort','medium')),
        instructions=runtime.rules_for(config)+'''\nSTEP: initial story map. Read each source segment once and return schema.
opening_quote is a verbatim source span; trim_note describes later trimming. Every hook needs a version or FULL.
full_outline is the sole principal FULL: hook first, close last. Other role labels are optional; combine them
when they describe one explanation. Keep full_outline out of versions. Reasons: one short clause.''',
        input=json.dumps(dict(segments=segments,job_instructions=config.get('notes','')),ensure_ascii=False,separators=(',',':')),
        text=dict(format=dict(type='json_schema',name='sales_story_map',strict=True,schema=schema([s['id'] for s in segments]))))
    import episode_continuity
    if episode_continuity.enabled(config):request['instructions']+='\n'+episode_continuity.MAP_INSTRUCTIONS
    import sales_reserve
    if sales_reserve.enabled(config):request['text']['format']['schema']=sales_reserve.map_schema(request['text']['format']['schema'])
    return compact_story_map.prepare(request,segments,config)

def write_report(folder,answer,segments):
    lookup={s['id']:s for s in segments};stamp=lambda x:f'{int(x)//60:02d}:{x%60:05.2f}'
    def labels(ids):return ', '.join(f"{i} ({stamp(lookup[i]['start'])}–{stamp(lookup[i]['end'])})" for i in ids)
    lines=['# แผนขายจากต้นฉบับครบ · สัญญา11ข้อ','',answer['review_note'],'',
        'แผนเท่านั้น ยังไม่ประกอบคัท ไม่ยืนยันภาพ/เสียง ความยาวหรือเปอร์เซ็นต์ใช้งานจริง','', '## กลุ่มเรื่อง','']
    for g in answer['groups']:lines += [f"- {g['id']} {g['title']} · {labels(g['segment_ids'])}"]
    lines += ['','## Hook ที่มีวลีจริง','']
    for h in answer['hooks']:lines += [f"- {h['id']} · {h['sales_angle']} · {labels([h['segment_id']])}\n  “{h['opening_quote']}”"]
    for h in answer['hooks']:
        if h.get('trim_note'):lines += [f"  เกลาภายหลัง {h['id']}: {h['trim_note']}"]
    lines += ['',f"## FULL · งบประมาณ {sum(b['budget_seconds'] for b in answer['full_outline'])} วินาที",'']
    for b in answer['full_outline']:lines += [f"- {b['role']} {b['budget_seconds']}s · {b['purpose']} · {labels(b['segment_ids'])}"]
    lines += ['','## เวอร์ชันย่อย/ผสม/เทคทางเลือก','']
    for v in answer['versions']:lines += [f"- {v['id']} {v['title']} · {v['kind']} · {v['hook_id']}\n  {v['payoff']} · รอตรวจ: {v['review_condition'] or 'ไม่มีเพิ่มเติมในขั้นวางแผน'}\n  {labels(v['segment_ids'])}"]
    if 'reserve_takes' in answer:
        lines += ['','## เทคสำรองแยกจากคลิปหลัก','']
        for r in answer['reserve_takes']:
            lines += [f"- {r['id']} {r['title']} · {labels(r['segment_ids'])} · {r['reason']}"]
            if 'opening_quote' in r:
                lines += [f"  คลิปขายสำรอง · {r['sales_angle']}\n  เปิด: “{r['opening_quote']}”\n  กู้เนื้อหา: “{r['recovery_quote']}”\n  ประโยชน์/ปิดประเด็น: “{r['payoff_quote']}”"]
    selected={i for b in answer['full_outline'] for i in b['segment_ids']}|{i for v in answer['versions'] for i in v['segment_ids']}
    selected.update(sid for r in answer.get('reserve_takes',[]) for sid in r['segment_ids'])
    unassigned=[r['segment_id'] for r in answer['segment_review'] if r['disposition'] in ('usable','alternative','mixed') and r['segment_id'] not in selected]
    lines += ['','## จุดที่ยังไม่มีเวอร์ชันรองรับ','',labels(unassigned) if unassigned else 'ทุกท่อนที่ระบุใช้ได้/ทางเลือก/ผสมมีแผนรองรับ แต่ยังไม่ใช่ใช้จริง']
    lines += ['','## ต้องอ่านต่อกัน','']
    for r in answer['continuity_links']:lines += ['- '+labels(r['segment_ids'])+' · '+r['reason']]
    lines += ['','## ตรวจครบทุกท่อนและข้อเสนอพักเฉพาะวลี','']
    for r in answer['segment_review']:
        lines += [f"- {labels([r['segment_id']])} · {r['disposition']} · {','.join(r['roles'])} · {r['reason']}"]
        for e in r['excluded_quotes']:lines += [f"  เสนอพัก: “{e['quote']}” — {e['reason']} (ยังเป็นข้อเสนอ ไม่ใช่ข้อยกเว้นที่ผู้ใช้ยืนยัน)"]
    (folder/'CONTENT-GROUPS-REVIEW.md').write_text('\n'.join(lines),'utf8')
    return unassigned

def run(source,folder,config,key,emit,cancelled,progress=None):
    import compact_story_map
    from engine import save,Progress
    progress=progress or Progress(folder,emit,cancelled);raw,segments=segments_for(source);request=make_request(segments,config)
    recovered=completed_local_recovery(folder,segments,config,raw)
    save(folder/'config.json',dict(config,ruleset=runtime.RULESET,grouping_source=source.name,map_revision=MAP_REVISION))
    save(folder/'grouping-source.json',dict(source_job=source.name,transcript_sha256=hashlib.sha256(raw).hexdigest(),segments=segments))
    (folder/'rules-used.txt').write_text(runtime.rules_for(config),'utf8')
    save(folder/'runtime-used.json',runtime.status())
    if config.get('refine_map_job') and recovered is None:
        prior=(folder.parent/config['refine_map_job']).resolve()
        if not prior.is_relative_to(folder.parent.resolve()):raise ValueError('แผนเดิมต้องอยู่ในjobs')
        old_source=json.loads((prior/'grouping-source.json').read_text('utf8'))
        if old_source['transcript_sha256']!=hashlib.sha256(raw).hexdigest():raise ValueError('ต้นฉบับแผนเดิมไม่ตรง')
        candidate=prior/'map-candidate.json'
        previous=json.loads(candidate.read_text('utf8')) if candidate.exists() else json.loads((prior/'sales-story-map-response.json').read_text('utf8'))['response']
        if config.get('sales_style')in ('sales_2_1','sales_2_2') and not candidate.exists() and (prior/'sales-story-map-request.json').is_file() and compact_story_map.is_compact(json.loads((prior/'sales-story-map-request.json').read_text('utf8'))):previous=compact_story_map.decode(previous,segments)
        request,scope=refinement_request(segments,config,previous)
        save(folder/'refinement-scope.json',scope)
        save(folder/'refinement-source.json',dict(job=prior.name,request_hash=json.loads((prior/'sales-story-map-response.json').read_text('utf8'))['request_hash']))
    with progress.stage('grouping','วางเรื่องและHookจากต้นฉบับครบ · สัญญา11ข้อ · 1 API'):
        if recovered is not None:
            answer,recovered_changes=recovered
            if not (folder/'map-candidate-before-local-recovery.json').exists():
                save(folder/'map-candidate-before-local-recovery.json',json.loads((folder/'map-candidate.json').read_text('utf8')))
            save(folder/'map-local-recovery.json',dict(changes=recovered_changes,api_calls_added=0,speech_changed=False,completed_response_preserved=True))
            progress(message='แก้รหัสอ้างอิงซ้ำจากคำตอบเดิม · ไม่เรียก AI และไม่ถอดเสียงใหม่')
        else:
            request=runtime.cached_editorial_request(request,folder,'sales-story-map')
            if compact_story_map.is_compact(request):
                legacy=make_request(segments,dict(config,compact_story_map=False))
                legacy=runtime.cached_editorial_request(legacy,folder,'sales-story-map')
                legacy_compact=make_request(segments,dict(config,compact_review_groups=False))
                legacy_compact=runtime.cached_editorial_request(legacy_compact,folder,'sales-story-map')
                request=compact_story_map.resume_request(request,legacy,folder,legacy_compact)
            if compact_story_map.is_compact(request):
                save(folder/'map-transport.json',dict(policy=compact_story_map.POLICY,segments=len(segments),max_output_tokens=request['max_output_tokens'],lossless=True,extra_api_calls=0))
            answer=call(request,folder,key,'sales-story-map',max_calls=1)
            if compact_story_map.is_compact(request):answer=compact_story_map.decode(answer,segments)
            if config.get('refine_map_job'):
                save(folder/'map-patch.json',answer);answer=apply_map_patch(previous,answer)
        save(folder/'map-candidate.json',answer)
        answer,reference_fixes=canonicalize_reference_catalogs(answer,segments,config)
        if recovered is not None:reference_fixes=recovered[1]+reference_fixes
        if reference_fixes:
            save(folder/'map-reference-normalization.json',dict(policy='unique-candidate-references-v1',changes=reference_fixes,
                speech_changed=False,api_calls_added=0,raw_response_preserved=True))
            progress(message='รวมรหัสท่อนอ้างอิงซ้ำในรายการแผน · คงเนื้อหาและ Hook เดิม · ไม่เรียก API เพิ่ม')
        findings=audit(answer,segments);save(folder/'map-findings.json',findings)
        try:answer=validate(answer,segments)
        except ValueError as error:
            save(folder/'map-validation.json',dict(valid=False,error=str(error)))
            try:
                write_report(folder,answer,segments)
                report=folder/'CONTENT-GROUPS-REVIEW.md';candidate=folder/'LUNA-CANDIDATE-REVIEW.md'
                candidate.write_text('# คำตอบยังไม่ผ่านตรวจ — ห้ามประกอบต่อ\n\n'+str(error)+'\n\n'+report.read_text('utf8'),'utf8');report.unlink()
            except (KeyError,TypeError):pass
            raise
        save(folder/'map-validation.json',dict(valid=True));save(folder/'content-groups.json',answer)
        unassigned=write_report(folder,answer,segments)
    save(folder/'map-audit.json',dict(unassigned_usable=unassigned,source_unchanged=hashlib.sha256((source/'transcript.json').read_bytes()).hexdigest()==hashlib.sha256(raw).hexdigest(),actual_coverage='not yet cut'))
    next_step='เรียบเรียงต่ออัตโนมัติ' if config.get('auto_export') else 'หยุดรอตรวจ'
    progress(stage='grouped' if config.get('auto_export') else 'done',message=f"แผน11ข้อเสร็จ · {len(answer['hooks'])} Hook · {len(answer['versions'])} เวอร์ชันย่อย/ทางเลือก · {next_step}",result=dict(group_preview=True,ruleset=runtime.RULESET,groups=len(answer['groups']),hooks=len(answer['hooks']),planned_versions=len(answer['versions'])+1))
    return answer
