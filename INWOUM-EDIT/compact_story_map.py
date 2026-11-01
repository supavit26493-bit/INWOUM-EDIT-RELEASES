"""Lossless transport for long story maps; no editorial filtering or extra calls."""
import copy,json
POLICY='compact-story-map-v2'
ALIASES=dict(groups='g',hooks='h',full_hook_id='fh',full_outline='f',versions='v',segment_review='r',
 continuity_links='c',review_note='n',reserve_takes='rt',id='i',title='t',segment_ids='ss',segment_id='si',
 opening_quote='oq',trim_note='tn',sales_angle='a',role='ro',budget_seconds='b',purpose='p',kind='k',hook_id='hi',
 group_ids='gs',payoff='po',review_condition='rc',roles='rs',disposition='d',reason='why',excluded_quotes='x',quote='q',payoff_quote='pq',recovery_quote='rq')
REVERSE={v:k for k,v in ALIASES.items()}
assert len(REVERSE)==len(ALIASES)

def enabled(segments,config):
 return (len(segments)>=80 and config.get('content_mode','sales')=='sales'
  and config.get('sales_style','original')in ('sales_2_1','sales_2_2')
  and config.get('compact_story_map',True) is not False)

def budget(segments,config):
 if 'map_output_tokens' in config:return config['map_output_tokens']
 return 32768 if len(segments)>=160 else 28000

def wire_schema(base,count):
 def walk(node,field=None):
  if field=='segment_id':return dict(type='integer',enum=list(range(1,count+1)))
  if field=='segment_ids':
   result=copy.deepcopy(node);result['items']=dict(type='integer',enum=list(range(1,count+1)));return result
  result=copy.deepcopy(node)
  if 'properties' in result:
   result['properties']={ALIASES[k]:walk(v,k) for k,v in node['properties'].items()}
   result['required']=[ALIASES[k] for k in node.get('required',[])]
  if 'items' in result:result['items']=walk(node['items'])
  return result
 return walk(base)

def prepare(request,segments,config):
 if not enabled(segments,config):return request
 result=copy.deepcopy(request);result['max_output_tokens']=budget(segments,config)
 data=json.loads(result['input'])
 data['segments']=[dict(row,id=i) for i,row in enumerate(data['segments'],1)]
 result['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
 form=result['text']['format'];form['name']='sales_story_map_compact_v1'
 form['schema']=wire_schema(form['schema'],len(segments))
 result['instructions']+='''\nTRANSPORT ONLY / compact-story-map-v1:
The source IDs in this request are integers. Return those exact integers in si/ss; never renumber
them by topic. G/H/V/R identifiers remain strings. The schema uses short property aliases for
the same full story-map fields; use this dictionary (original:wire):\n'''+json.dumps(ALIASES,separators=(',',':'))+'''
Preserve all required fields, one review per source segment, every selected story, and reserve takes.
Do not limit the number of useful alternatives to save space. Keep explanatory metadata concise:
one specific brief clause for why/p/po/rc/tn, normally no more than 60 characters; use empty optional
text only where the existing contract permits. Do not repeat source narration in reasons/titles.
Quotes must remain verbatim; never shorten, translate or alter quotes to fit this guidance.
This changes serialization only, not the editorial, exclusion, context, price or reserve policies.'''
 if config.get('compact_review_groups',True):
  form['name']='sales_story_map_compact_v2'
  reviews=form['schema']['properties']['r'];reviews['minItems']=1
  fields=reviews['items']['properties'];reference=fields.pop('si')
  fields['ss']=dict(type='array',minItems=1,items=reference)
  reviews['items']['required']=['ss' if k=='si' else k for k in reviews['items']['required']]
  result['instructions']+='''\nREVIEW TRANSPORT v2: In r only, ss is a nonempty list of explicit source IDs that
share EXACTLY the same assessment (rs, d, why, x). Read every source segment, and list every source
ID exactly once across r. Group related segments only when one specific reason truthfully applies
to every listed ID. Split differing content, decisions, caveats or meanings. Never use an implicit
default or omit unselected IDs. If x contains an excluded quote, that row MUST list a single source
ID so the quotation stays attached to its actual speech. The program expands each explicitly
assessed ss into separate per-source reviews without changing reasons or decisions. All source
quote, price, safety, completeness and reserve requirements remain unchanged.'''
 if config.get('sales_style')=='sales_2_1':
  import transport21
  result=transport21.map_request(result,len(segments))
 return result

def is_compact(request):
 return request.get('text',{}).get('format',{}).get('name') in ('sales_story_map_compact_v1','sales_story_map_compact_v2')

def decode(answer,segments):
 ids=[s['id'] for s in segments]
 answer=copy.deepcopy(answer)
 if any('ss' in row for row in answer.get('r',[])):
  expanded=[];seen=set()
  for row in answer['r']:
   refs=row.get('ss')
   if 'si' in row or not isinstance(refs,list) or not refs:raise ValueError('รายการเหตุผลย่อไม่ระบุท่อนครบ')
   if row.get('x') and len(refs)!=1:raise ValueError('คำที่ห้ามใช้ต้องอ้างท่อนเดียวให้ตรงต้นฉบับ')
   for value in refs:
    if type(value)!=int or not 1<=value<=len(ids) or value in seen:raise ValueError('รายการเหตุผลย่ออ้างท่อนผิดหรือซ้ำ')
    seen.add(value);expanded.append(dict({k:v for k,v in row.items() if k!='ss'},si=value))
  if seen!=set(range(1,len(ids)+1)):raise ValueError('รายการเหตุผลย่ออ่านต้นฉบับไม่ครบทุกท่อน')
  # Restore source order; grouping changes no source assessment or quote.
  answer['r']=sorted(expanded,key=lambda row:row['si'])
 def source_id(value):
  if type(value)!=int or not 1<=value<=len(ids):raise ValueError('แผนย่ออ้างท่อนต้นฉบับไม่ถูกต้อง')
  return ids[value-1]
 def walk(value,field=None):
  if field=='segment_id':return source_id(value)
  if field=='segment_ids':
   if not isinstance(value,list):raise ValueError('แผนย่อไม่มีรายการท่อนต้นฉบับ')
   return [source_id(v) for v in value]
  if isinstance(value,list):return [walk(v) for v in value]
  if isinstance(value,dict):
   if any(k not in REVERSE for k in value):raise ValueError('คำตอบแผนย่อมีช่องที่ไม่รู้จัก')
   return {REVERSE[k]:walk(v,REVERSE[k]) for k,v in value.items()}
  return value
 return walk(answer)

def resume_request(request,legacy_request,folder,legacy_compact_request=None):
 """Only reuse a verified completed legacy request that is semantically identical."""
 from planner import fingerprint
 from parallel_requests import semantic_fingerprint
 from model_catalog import prepare_request
 response_file=folder/'sales-story-map-response.json';request_file=folder/'sales-story-map-request.json'
 if not is_compact(request) or not response_file.exists() or not request_file.exists():return request
 old=json.loads(request_file.read_text('utf8'));saved=json.loads(response_file.read_text('utf8'))
 candidate=legacy_compact_request if is_compact(old) else legacy_request
 if candidate is None:return request
 candidate=prepare_request(candidate)
 for key in ('service_tier','prompt_cache_options'):
  if key in old and key not in candidate:candidate[key]=old[key]
 if saved.get('request_hash')==fingerprint(old) and semantic_fingerprint(old)==semantic_fingerprint(candidate):return old
 return request
