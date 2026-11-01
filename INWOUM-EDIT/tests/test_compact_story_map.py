import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sales_map,compact_story_map as compact
from planner import fingerprint

def fixture(count=231):
 segments=[dict(id=f'T{i:03}',start=i*4,end=i*4+3,text=f'สินค้านี้ใช้งานดีมาก เรื่องที่ {i}') for i in range(1,count+1)]
 ids=[s['id'] for s in segments]
 story=dict(groups=[dict(id='G1',title='สินค้า',segment_ids=ids)],
  hooks=[dict(id='H1',segment_id=ids[0],opening_quote=segments[0]['text'],trim_note='',sales_angle='ขาย')],full_hook_id='H1',
  full_outline=[dict(role=r,segment_ids=refs,budget_seconds=b,purpose='สาระ') for r,refs,b in [('hook',ids[:1],5),('features',ids[1:-1],160),('close',ids[-1:],5)]],
  versions=[dict(id='V1',title='วิธีใช้',kind='focused',hook_id='H1',group_ids=['G1'],segment_ids=ids[:2],payoff='เห็นประโยชน์',review_condition='')],
  segment_review=[dict(segment_id=s,roles=['feature'],disposition='usable',reason='ประโยชน์ของสินค้า',excluded_quotes=[]) for s in ids],
  continuity_links=[dict(segment_ids=ids[1:3],reason='ต่อเนื่อง')],review_note='ครบ',
  reserve_takes=[dict(id='R1',title='เทคต่างอารมณ์',segment_ids=ids[-2:],reason='ทางเลือก',
   opening_quote=segments[-2]['text'],payoff_quote=segments[-1]['text'],recovery_quote=segments[-2]['text'],sales_angle='เน้นอีกเทค')])
 return segments,story

def encode(story,segments):
 ids={s['id']:i for i,s in enumerate(segments,1)}
 def walk(v,field=None):
  if field=='segment_id':return ids[v]
  if field=='segment_ids':return [ids[x] for x in v]
  if isinstance(v,list):return [walk(x) for x in v]
  if isinstance(v,dict):return {compact.ALIASES[k]:walk(x,k) for k,x in v.items()}
  return v
 return walk(story)

def grouped_encode(story,segments):
 wire=encode(story,segments);groups={}
 for row in wire['r']:
  key=json.dumps({k:v for k,v in row.items() if k!='si'},sort_keys=True)
  if row['x']:key+='-'+str(row['si'])
  if key not in groups:groups[key]=dict({k:v for k,v in row.items() if k!='si'},ss=[])
  groups[key]['ss'].append(row['si'])
 wire['r']=list(groups.values());return wire

class CompactStoryMapTests(unittest.TestCase):
 def setUp(self):
  p=patch('api_budget.count_input',return_value=1000);p.start();self.addCleanup(p.stop)
 def test_lossless_all_fields_quotes_prices_reviews_reserves_and_order(self):
  segments,story=fixture();story['segment_review'][1]['excluded_quotes']=[dict(quote='สองร้อยบาท',reason='ราคาเป็นตัวเลข')]
  before=copy.deepcopy(story);wire=encode(story,segments)
  self.assertEqual(compact.decode(wire,segments),before);self.assertEqual(story,before)
  self.assertLess(len(json.dumps(wire,ensure_ascii=False)),len(json.dumps(story,ensure_ascii=False))*.75)

 def test_only_long_sales_21_changes_no_other_style_model_effort_or_source(self):
  segments,_=fixture();before=copy.deepcopy(segments)
  for style in ('original','natural','natural_v2'):
   cfg=dict(model='gpt-6.1-sol',sales_style=style,effort='medium')
   self.assertEqual(sales_map.make_request(segments,cfg),sales_map.make_request(segments,dict(cfg,compact_story_map=False)))
   self.assertEqual(sales_map.make_request(segments,cfg)['max_output_tokens'],24000)
  cfg=dict(model='gpt-6.1-sol',sales_style='sales_2_1',effort='medium')
  req=sales_map.make_request(segments,cfg)
  self.assertTrue(compact.is_compact(req));self.assertEqual(req['max_output_tokens'],32768)
  self.assertEqual(req['reasoning'],dict(effort='medium'));self.assertEqual(segments,before)
  self.assertEqual(json.loads(req['input'])['segments'][0]['text'],segments[0]['text'])
  fields=req['text']['format']['schema']['properties'];self.assertIn('rt',fields)
  self.assertEqual(fields['r']['minItems'],1);self.assertEqual(fields['r']['maxItems'],231)
  self.assertIn('ss',fields['r']['items']['required']);self.assertNotIn('si',fields['r']['items']['properties'])
  self.assertEqual(fields['h']['items']['properties']['si']['type'],'integer')
  self.assertEqual(sales_map.make_request(segments[:79],cfg)['max_output_tokens'],24000)
  self.assertEqual(sales_map.make_request(segments[:80],cfg)['max_output_tokens'],28000)
  self.assertEqual(sales_map.make_request(segments,dict(cfg,map_output_tokens=30000))['max_output_tokens'],30000)

 def test_reject_unknown_refs_boolean_and_unknown_fields_without_guessing(self):
  segments,story=fixture();wire=encode(story,segments)
  for invalid in (0,232,True,'1'):
   changed=copy.deepcopy(wire);changed['h'][0]['si']=invalid
   with self.assertRaises(ValueError):compact.decode(changed,segments)
  wire['unexpected']='ignore me'
  with self.assertRaises(ValueError):compact.decode(wire,segments)

 def test_cached_completed_legacy_request_only_when_identical(self):
  segments,story=fixture();cfg=dict(model='gpt-6.1-sol',sales_style='sales_2_1',effort='medium')
  current=sales_map.make_request(segments,cfg);legacy=sales_map.make_request(segments,dict(cfg,compact_story_map=False))
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);(folder/'sales-story-map-request.json').write_text(json.dumps(legacy),'utf8')
   (folder/'sales-story-map-response.json').write_text(json.dumps(dict(request_hash=fingerprint(legacy),response=story)),'utf8')
   self.assertEqual(compact.resume_request(current,legacy,folder),legacy)
   modified=copy.deepcopy(legacy);modified['reasoning']['effort']='high'
   self.assertEqual(compact.resume_request(current,modified,folder),current)

 def test_run_decodes_once_preserves_raw_cache_and_reuses_without_paid_call(self):
  segments,story=fixture();cfg=dict(model='gpt-6.1-sol',sales_style='sales_2_1',effort='medium')
  transcript=[{k:v for k,v in row.items() if k!='id'} for row in segments]
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);source=root/'source';source.mkdir();folder=root/'job';folder.mkdir()
   (source/'transcript.json').write_text(json.dumps(transcript,ensure_ascii=False),'utf8')
   with patch('parallel_requests.plan_once',return_value=(grouped_encode(story,segments),dict(usage={}))) as send:
    result=sales_map.run(source,folder,cfg,'fake-key',lambda **e:None,lambda:False)
    raw=(folder/'sales-story-map-response.json').read_bytes()
    self.assertEqual(result,story);self.assertIn('rt',json.loads(raw)['response'])
    self.assertEqual(sales_map.run(source,folder,cfg,'',lambda **e:None,lambda:False),story)
    self.assertEqual(send.call_count,1);self.assertEqual((folder/'sales-story-map-response.json').read_bytes(),raw)
    self.assertEqual(json.loads((folder/'content-groups.json').read_text('utf8')),story)
    self.assertEqual(len(result['segment_review']),231)

 def test_grouped_reviews_expand_losslessly_and_exclusions_stay_attached(self):
  segments,story=fixture();story['segment_review'][1]['excluded_quotes']=[dict(quote=segments[1]['text'],reason='เฉพาะท่อน')]
  wire=grouped_encode(story,segments);before=copy.deepcopy(wire)
  self.assertEqual(compact.decode(wire,segments),story);self.assertEqual(wire,before)
  self.assertEqual(len(wire['r']),2)
  self.assertLess(len(json.dumps(wire,ensure_ascii=False)),len(json.dumps(encode(story,segments),ensure_ascii=False))*.6)
  row=next(r for r in wire['r'] if r['x']);self.assertEqual(row['ss'],[2])
 def test_grouped_reviews_reject_missing_duplicate_boolean_and_misattributed_quotes(self):
  segments,story=fixture();wire=grouped_encode(story,segments)
  for refs in (list(range(1,231)),[1]*231,[True]+list(range(2,232))):
   bad=copy.deepcopy(wire);bad['r'][0]['ss']=refs
   with self.assertRaises(ValueError):compact.decode(bad,segments)
  bad=copy.deepcopy(wire);bad['r'][0]['x']=[dict(q='คำเฉพาะท่อน',why='ราคา')]
  with self.assertRaises(ValueError):compact.decode(bad,segments)
 def test_verified_compact_v1_cache_reused_under_identical_editorial_contract(self):
  segments,story=fixture();cfg=dict(model='gpt-6.1-sol',sales_style='sales_2_1',effort='medium')
  current=sales_map.make_request(segments,cfg);old=sales_map.make_request(segments,dict(cfg,compact_review_groups=False))
  old.update(service_tier='default',prompt_cache_options=dict(ttl='30m'))
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);(folder/'sales-story-map-request.json').write_text(json.dumps(old),'utf8')
   (folder/'sales-story-map-response.json').write_text(json.dumps(dict(request_hash=fingerprint(old),response=encode(story,segments))),'utf8')
   legacy=sales_map.make_request(segments,dict(cfg,compact_story_map=False))
   old_format=sales_map.make_request(segments,dict(cfg,compact_review_groups=False))
   self.assertEqual(compact.resume_request(current,legacy,folder,old_format),old)
   changed=copy.deepcopy(old_format);changed['instructions']+=' different editorial rule'
   self.assertEqual(compact.resume_request(current,legacy,folder,changed),current)

 def test_completed_legacy_21_map_is_reused_without_new_api_or_raw_rewrite(self):
  segments,story=fixture();cfg=dict(model='gpt-6.1-sol',sales_style='sales_2_1',effort='medium')
  legacy=sales_map.make_request(segments,dict(cfg,compact_story_map=False))
  transcript=[{k:v for k,v in row.items() if k!='id'} for row in segments]
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);source=root/'source';source.mkdir();folder=root/'job';folder.mkdir()
   (source/'transcript.json').write_text(json.dumps(transcript,ensure_ascii=False),'utf8')
   (folder/'sales-story-map-request.json').write_text(json.dumps(legacy,ensure_ascii=False),'utf8')
   response=folder/'sales-story-map-response.json';response.write_text(json.dumps(dict(request_hash=fingerprint(legacy),response=story),ensure_ascii=False),'utf8')
   before=response.read_bytes()
   with patch('parallel_requests.plan_once',side_effect=AssertionError('completed cache must not send')):
    result=sales_map.run(source,folder,cfg,'',lambda **e:None,lambda:False)
   self.assertEqual(result,story);self.assertEqual(response.read_bytes(),before)

if __name__=='__main__':unittest.main()
