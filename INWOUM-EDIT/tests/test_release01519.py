import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sales_map as sm,sales_reserve as sr,runtime_contract as rt,token_usage,fx_rates
from planner import fingerprint

class Release01519Tests(unittest.TestCase):
 def fixture(self):
  seg=[dict(id='T001',start=0,end=3,text='เปิดสินค้านี้ดี ใช้สะดวก ปิดท้ายคุ้มค่า')]
  story=dict(groups=[dict(id='G1',title='เรื่อง',segment_ids=['T001'])],hooks=[dict(id='H1',segment_id='T001',opening_quote='เปิดสินค้านี้ดี',trim_note='',sales_angle='ขาย')],full_hook_id='H1',full_outline=[dict(role=role,segment_ids=['T001'],budget_seconds=20,purpose='ครบ') for role in ('hook','features','close')],versions=[],segment_review=[dict(segment_id='T001',roles=['feature'],disposition='usable',reason='เนื้อหา',excluded_quotes=[])],continuity_links=[],review_note='ครบ',reserve_takes=[dict(id='R1',title='อีกเทค',segment_ids=['T001','T001'],reason='ต่างอารมณ์',opening_quote='เปิดสินค้านี้ดี',recovery_quote='ใช้สะดวก',payoff_quote='ปิดท้ายคุ้มค่า',sales_angle='คุ้ม')])
  return seg,story,rt.configure(dict(model='gpt-6.1-terra',effort='high'))
 def test_duplicate_catalog_fix_keeps_all_main_and_reserve_content(self):
  seg,story,cfg=self.fixture();before=copy.deepcopy(story)
  fixed,changes=sm.canonicalize_reference_catalogs(story,seg,cfg);sm.validate(fixed,seg)
  self.assertEqual(story,before);self.assertEqual(fixed['full_outline'],before['full_outline']);self.assertEqual(fixed['versions'],before['versions'])
  self.assertEqual(len(fixed['reserve_takes']),1);self.assertEqual(fixed['reserve_takes'][0],dict(before['reserve_takes'][0],segment_ids=['T001']));self.assertEqual(len(changes),1)
 def test_unknown_reference_is_never_removed_to_force_pass(self):
  seg,story,cfg=self.fixture();story['reserve_takes'][0]['segment_ids']=['missing','T001','T001']
  fixed,changes=sm.canonicalize_reference_catalogs(story,seg,cfg);self.assertEqual(changes,[])
  with self.assertRaisesRegex(ValueError,'R1.*missing'):sm.validate(fixed,seg)
 def test_reserve_patch_has_source_and_does_not_change_main(self):
  seg,story,cfg=self.fixture();req,scope=sm.refinement_request(seg,dict(cfg,blocking_only=True),story)
  data=json.loads(req['input']);self.assertEqual(data['previous_map']['reserve_takes'],story['reserve_takes'])
  self.assertIn('reserve_updates',req['text']['format']['schema']['required'])
  fields=sm.patch_schema(['T001'],True)['properties'];p={k:[] for k,v in fields.items() if v['type']=='array'};p.update(full_hook_id='',review_note='ซ่อม')
  new=dict(story['reserve_takes'][0],segment_ids=['T001']);p['reserve_updates']=[new]
  fixed=sm.apply_map_patch(story,p);self.assertEqual(fixed['full_outline'],story['full_outline']);self.assertEqual(fixed['reserve_takes'],[new]);sm.validate(fixed,seg)
 def recovery_folder(self,folder):
  seg,story,cfg=self.fixture();raw=json.dumps([dict(start=0,end=3,text=seg[0]['text'])]).encode()
  request=sm.make_request(seg,cfg);digest=fingerprint(request)
  files={'map-candidate.json':story,'grouping-source.json':dict(segments=seg,transcript_sha256=hashlib.sha256(raw).hexdigest()),'sales-story-map-request.json':request,'sales-story-map-response.json':dict(request_hash=digest,response=story),'api-usage.json':[dict(stage='sales-story-map',status='completed',request_hash=digest)]}
  for name,value in files.items():(folder/name).write_text(json.dumps(value,ensure_ascii=False),'utf8')
  (folder/'transcript.json').write_bytes(raw);(folder/'rules-used.txt').write_text(rt.rules_for(cfg),'utf8')
  return seg,story,cfg,raw
 def test_resume_completed_duplicate_map_uses_zero_api_and_preserves_response(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);seg,story,cfg,raw=self.recovery_folder(folder);reply=(folder/'sales-story-map-response.json').read_bytes()
   with patch('sales_map.call',side_effect=AssertionError('No paid API allowed')) as api:
    answer=sm.run(folder,folder,cfg,'',lambda **kw:None,lambda:False)
   api.assert_not_called();self.assertEqual((folder/'sales-story-map-response.json').read_bytes(),reply);self.assertEqual((folder/'transcript.json').read_bytes(),raw)
   self.assertEqual(answer['full_outline'],story['full_outline']);self.assertEqual(answer['reserve_takes'][0]['segment_ids'],['T001']);self.assertTrue((folder/'map-local-recovery.json').exists())
 def test_recovery_requires_matching_source_effort_and_completed_request(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);seg,story,cfg,raw=self.recovery_folder(folder)
   self.assertIsNotNone(sm.completed_local_recovery(folder,seg,cfg,raw))
   self.assertIsNone(sm.completed_local_recovery(folder,seg,cfg,raw+b' '))
   self.assertIsNone(sm.completed_local_recovery(folder,seg,dict(cfg,effort='medium'),raw))
   (folder/'api-usage.json').write_text('[]');self.assertIsNone(sm.completed_local_recovery(folder,seg,cfg,raw))
 def test_defaults_and_explicit_old_choices(self):
  cfg=rt.configure({});self.assertEqual((cfg['sales_style'],cfg['model'],cfg['effort']),('sales_2_1','gpt-6.1-terra','high'))
  old=rt.configure(dict(sales_style='natural',model='gpt-6.1-sol',effort='medium'));self.assertEqual((old['sales_style'],old['model'],old['effort']),('natural','gpt-6.1-sol','medium'))
  with self.assertRaises(ValueError):rt.configure(dict(sales_style='sales_2_2'))
 def test_usage_reports_actual_mixed_effort_not_current_config(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);rows=[]
   for stage,effort in [('map','high'),('fix','low')]:
    req=dict(model='gpt-6.1-terra',reasoning=dict(effort=effort));(folder/(stage+'-request.json')).write_text(json.dumps(req))
    rows.append(dict(stage=stage,request_hash=fingerprint(req),usage=dict(input_tokens=10,output_tokens=5)))
   (folder/'api-usage.json').write_text(json.dumps(rows));(folder/'config.json').write_text(json.dumps(dict(model='gpt-6.1-sol',effort='medium')))
   self.assertEqual(token_usage.read(folder)['models_used'],[dict(model='gpt-6.1-terra',effort='high'),dict(model='gpt-6.1-terra',effort='low')])
 def test_fx_cross_rate_frozen_per_job_no_change_to_usd(self):
  rate=fx_rates.normalize(dict(date='2026-10-30',usd_per_eur='2',thb_per_eur='70'));self.assertEqual(rate['thb_per_usd'],'35')
  with tempfile.TemporaryDirectory() as tmp,patch('fx_rates.quote',return_value=rate) as lookup:
   folder=Path(tmp);data=dict(total_calls=1,cost=dict(usd=.2));self.assertEqual(fx_rates.attach(data,folder)['cost']['thb'],7)
   rate['thb_per_usd']='99';self.assertEqual(fx_rates.attach(dict(total_calls=1,cost=dict(usd=.3)),folder)['cost']['thb'],10.5);self.assertEqual(lookup.call_count,1);self.assertEqual(data['cost']['usd'],.2)
 def test_fx_no_call_no_network_unknown_usd_not_zero(self):
  with tempfile.TemporaryDirectory() as tmp,patch('fx_rates.quote',side_effect=AssertionError('no lookup')):
   folder=Path(tmp);self.assertEqual(fx_rates.attach(dict(total_calls=0,cost=dict(usd=0)),folder)['cost']['thb'],0)
   self.assertNotIn('thb',fx_rates.attach(dict(total_calls=1,cost=dict(usd=None)),folder)['cost'])
 def test_fx_offline_fallback_and_invalid_rate(self):
  with patch('fx_rates.fetch',side_effect=OSError()),patch.object(fx_rates,'_quote',None):
   self.assertTrue(fx_rates.quote()['cached_reference'])
  with self.assertRaises(ValueError):fx_rates.normalize(dict(date='2026-10-30',usd_per_eur='1',thb_per_eur='999'))

if __name__=='__main__':unittest.main()
