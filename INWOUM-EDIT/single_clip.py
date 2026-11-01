"""One non-sales story, one cached editorial call; source speech only."""
import copy
import json
from pathlib import Path

import editorial
import fast_editorial
from fast_logic import full_selection, merge, subtract, resolve_plan
from parallel_requests import call
from planner import obj

POLICY = 'single-story-v1'


def prepare_catalog(catalog):
    """Keep protected speech whole; add demonstration gaps without overlapping units."""
    speech = [copy.deepcopy(u) for u in catalog if not u['id'].startswith('P')]
    protected = [u for u in catalog if u['id'].startswith('P')]
    occupied = merge([(u['start'], u['end']) for u in speech])
    for u in speech:
        if any(u['start'] < p['end'] and u['end'] > p['start'] for p in protected):
            u['protected'] = True
    gaps = merge([(p['start'], p['end']) for p in protected])
    for i, (a, b) in enumerate(subtract(gaps, occupied)):
        speech.append(dict(id=f'Psingle{i:04}',start=a,end=b,words=[],protected=True,
                           text='ช่วงสาธิตที่ผู้ใช้ระบุให้เก็บ'))
    return sorted(speech, key=lambda u: (u['start'], u['end'], u['id']))


def request_body(catalog, config):
    method = config.get('single_method', 'chronological')
    limited=config.get('single_max_seconds',0)==180
    rules = (Path(__file__).parent/'rules'/'SINGLE-STORY-v1.txt').read_text('utf8')
    if method == 'chronological':
        schema = obj(dict(decisions=dict(type='array',items=obj(dict(
            unit_id=dict(type='string'),keep=dict(type='boolean'),reason=dict(type='string'))))))
        instruction = ('MODE: chronological. Decide keep/omit for EVERY unit exactly once. '
            'Keep full units, preserving source order. Omit only clearly off-topic speech or production chatter. '
            'Do not remove repeated speech, alternate takes or fillers in this mode. '
            'Return short Thai reasons for omitted units; use an empty reason for kept units.')
        units = [dict(id=u['id'],text=u['text'],protected=u['protected']) for u in catalog]
    else:
        schema = copy.deepcopy(fast_editorial.SCHEMA)
        schema['properties']['edits'].update(minItems=1,maxItems=1)
        instruction = ('MODE: rearrange. Return exactly ONE complete story. Reorder only when useful. '
            'Remove wasted fillers and immediate repeated words/phrases and select the best complete take. '
            'Preserve meaningful Thai reduplication and all distinct necessary ideas. '
            'Select inclusive zero-based phrase indices. Do not invent or rewrite spoken words. '
            'List every wholly unused unit with a short Thai reason. Use decision production for off-topic speech; '
            'money is not a reason to omit: factual prices and numbers are allowed in this non-sales mode.')
        units = [dict(id=u['id'],phrases={str(i):w['text'] for i,w in enumerate(u['words'] or [u])},
                      protected=u['protected']) for u in catalog]
    if limited:
        rules=rules.replace('with no duration ceiling','with a hard maximum duration of 180 seconds')
        rules=rules.replace('Do not omit an idea just to make the clip shorter.','Prioritize the main story, its necessary context and a complete ending; omit secondary ideas to fit the time budget.')
        rules=rules.replace('Ignore sales-only price/hook/180-second policies.','No sales requirement; the user-selected 180-second ceiling applies.')
        rules=rules.replace('Retain doubtful passages rather than guessing.','Do not distort uncertainty when selecting a concise story.')
        instruction=instruction.replace('Omit only clearly off-topic speech or production chatter.','Omit off-topic speech and secondary or redundant units to fit 180 seconds.')
        instruction=instruction.replace('Do not remove repeated speech, alternate takes or fillers in this mode.','Keep chosen units whole and in source order; select a concise set of units.')
        instruction=instruction.replace('Preserve meaningful Thai reduplication and all distinct necessary ideas.','Preserve meaningful Thai reduplication and the key ideas necessary for this concise story.')
        instruction+='\nDURATION: One coherent clip <=180 seconds. Aim <=165 seconds of selected source speech to allow boundary padding. Finish complete sentences; never chop a story at 180 seconds. Use supplied source durations, not word-count estimates.'
        lookup={u['id']:u for u in catalog}
        for unit in units:
            source=lookup[unit['id']];unit['seconds']=round(source['end']-source['start'],3)
            if method!='chronological':
                unit['phrase_seconds']={str(i):round(w['end']-w['start'],3) for i,w in enumerate(source['words'] or [source])}
    if config.get('single_style','original') in ('natural','natural_v2') and method=='rearrange':
        from sales_continuity import instructions_for
        instruction+='\n'+instructions_for(config,single=True)
    request = dict(model=config['model'],store=False,max_output_tokens=24000,
        instructions=rules+'\n'+instruction,
        input=json.dumps(dict(topic=config.get('content_topic',''),notes=config.get('notes',''),units=units),
                         ensure_ascii=False,separators=(',',':')),
        text=dict(format=dict(type='json_schema',name='single_story',strict=True,schema=schema)))
    if config.get('effort','medium')!='default':
        request['reasoning']=dict(effort=config.get('effort','medium'))
    import episode_continuity
    if episode_continuity.enabled(config):
        request['instructions']+='\n'+episode_continuity.INSTRUCTIONS
        request['text']['format']['schema']=episode_continuity.schema(request['text']['format']['schema'],catalog,single=True)
    return request


def normalize(raw,catalog,method,style='original'):
    if method=='chronological':
        rows=raw.get('decisions',[]);ids={u['id'] for u in catalog}
        if len(rows)!=len(ids) or {r.get('unit_id') for r in rows}!=ids:
            raise ValueError('AI ต้องตัดสินครบทุกท่อน ห้ามตกหล่นหรือซ้ำ')
        decisions={r['unit_id']:r for r in rows}
        if any(type(r.get('keep')) is not bool for r in rows):
            raise ValueError('คำตัดสินเก็บท่อนไม่ถูกต้อง')
        selected=[];unused=[]
        for u in catalog:
            row=decisions[u['id']]
            if row['keep'] or u['protected']:selected.append(full_selection(u))
            else:unused.append(dict(unit_id=u['id'],decision='production',reason=row.get('reason','')))
        raw=dict(edits=[dict(name='คอนเทนต์คลิปเดียว',purpose='เล่าเรื่องตามลำดับต้นฉบับ',selections=selected)],unused=unused)
    if not isinstance(raw.get('edits'),list) or len(raw['edits'])!=1:
        raise ValueError('โหมดคลิปเดียวต้องมีหนึ่งเวอร์ชันเท่านั้น')
    plan=fast_editorial.normalize_plan(raw,catalog)
    for u in catalog:
        if not u['protected']:continue
        picks=[s for s in plan['edits'][0]['selections'] if s['unit_id']==u['id']]
        covered={i for s in picks for i in range(s['first_word'],s['last_word']+1)}
        if covered!=set(range(max(1,len(u['words'])))):
            raise ValueError('แผนตัดช่วงที่ผู้ใช้ระบุให้เก็บ กรุณาตรวจแผน')
    if method=='rearrange':
        if style in ('natural','natural_v2'):
            from sales_continuity import clean
        else:
            from speech_cleanup import clean
        plan,changes=clean(plan,catalog)
        plan['speech_cleanup']=changes
    plan['editorial_policy']=POLICY
    return plan


def run(catalog,folder,config,key,progress,project,silence):
    from engine import save
    request=request_body(catalog,config)
    (folder/'rules-used.txt').write_text(request['instructions'],'utf8')
    limit=config.get('single_max_seconds',0)
    project['single_max_seconds']=limit
    duration_label='ไม่เกิน 3 นาที' if limit==180 else 'ไม่จำกัดความยาว'
    with progress.stage('draft','AI เลือกเนื้อหาคลิปเดียว · 1 API · '+duration_label):
        import episode_continuity
        event_mode=episode_continuity.enabled(config)
        raw=episode_continuity.recover_single_checkpoint(folder,request,catalog,progress) if event_mode else None
        if raw is None:raw=call(request,folder,key,'single-story',max_calls=6)
        if event_mode:raw=episode_continuity.ensure(raw,catalog,folder,key,request,progress,single=True)
        method=config.get('single_method','chronological');style=config.get('single_style','original')
        plan=normalize(raw,catalog,method,style)
        if style=='natural_v2':plan['cut_style']='natural_v2'
        if event_mode:episode_continuity.attach(plan,raw)
        if style in ('natural','natural_v2') and method=='rearrange':
            import production_cleanup
            plan,production_changes=production_cleanup.clean(plan,catalog)
            save(folder/'production-cleanup.json',dict(changes=production_changes,extra_api_calls=0))
        if style in ('natural','natural_v2') and method=='rearrange':
            import sales_continuity
            plan,issues=sales_continuity.repair(plan,catalog,folder,key,request,progress,content_mode='single')
            plan,changes=sales_continuity.clean(plan,catalog)
            plan.setdefault('speech_cleanup',[]).extend(changes)
            save(folder/'single-continuity.json',dict(style=style,repair_findings=issues,cleanup=plan['speech_cleanup'],manual_edits=False))
        if event_mode and episode_continuity.findings(plan,catalog,single=True):raise ValueError('การเกลาคำทำให้บริบทเหตุการณ์ไม่ครบ จึงยังไม่ส่งออก')
        if event_mode:episode_continuity.record(plan,catalog,folder)
        save(folder/'single-draft-before-duration-check.json',plan)
        resolve_plan(plan,catalog,silence,project)
        save(folder/'plan.json',plan)
        save(folder/'assembled-scripts.json',editorial.assembled_scripts(plan,catalog))
        editorial.write_script_report(folder,plan,catalog)
    return plan
