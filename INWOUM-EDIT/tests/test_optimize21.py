import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import optimize21,transport21,episode_continuity as ep,sales_draft,runtime_contract as runtime
from test_sales_continuity import unit,selection

class Optimize21Tests(unittest.TestCase):
 def fixture(self):
  catalog=[unit('A',['สเปรย์หอมล็อกผม','ใช้แล้วอยู่ทรง'],0),unit('B',['ครีมเซตผมจากเกาหลี','คงวอลลุ่มธรรมชาติ','ใช้ง่ายใช้ได้ทุกวัน'],4)]
  edits={'FULL':dict(name='FULL',purpose='หลัก',hook_count=1,selections=[dict(selection('A',0,1),event_id='E0')]),'R1':dict(name='สำรอง',purpose='อีกมุม',hook_count=1,selections=[dict(selection('B',1,2),event_id='E1')])}
  raw=dict(edits=edits,unused=[],review_note='ครบ',events=[dict(id='E0',subject='สเปรย์',unit_ids=['A'],keep_order=True,requires=[]),dict(id='E1',subject='ครีม',unit_ids=['B'],keep_order=True,requires=[])])
  reserve=dict(id='R1',title='ครีม',segment_ids=['T2'],reason='อีกเทค',opening_quote='ครีมเซตผมจากเกาหลี',recovery_quote='คงวอลลุ่มธรรมชาติ',payoff_quote='ใช้ง่ายใช้ได้ทุกวัน',sales_angle='ธรรมชาติ')
  story=dict(groups=[dict(id='G',segment_ids=['T1','T2'])],hooks=[],full_hook_id='',full_outline=[],versions=[],continuity_links=[],reserve_takes=[reserve],segment_review=[dict(segment_id=s,disposition='usable',reason='ประโยชน์') for s in ['T1','T2']])
  schema=ep.schema(sales_draft.draft_schema(story,{}),catalog)
  req=dict(model='gpt-6.1-sol',reasoning=dict(effort='medium'),instructions='original source contract',input=json.dumps(dict(units=[dict(id=u['id'],phrases={str(i):w['text'] for i,w in enumerate(u['words'])}) for u in catalog],approved_story_map=story)),text=dict(format=dict(type='json_schema',name='test',strict=True,schema=schema)))
  return catalog,raw,req,story
 def folder(self,tmp):
  folder=Path(tmp);(folder/'config.json').write_text(json.dumps(dict(sales_style='sales_2_1')))
  return folder
 def test_one_targeted_call_repairs_evidence_without_changing_main(self):
  cat,raw,req,story=self.fixture();fixed=copy.deepcopy(raw['edits']['R1']);fixed['selections'][0]['first_word']=0
  before=copy.deepcopy(raw)
  with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.call',return_value={'1':fixed}) as paid:
   result=ep.ensure(raw,cat,self.folder(tmp),'fake',req,lambda **kw:None)
   self.assertEqual(paid.call_count,1);self.assertEqual(raw,before)
   self.assertEqual(result['edits']['FULL'],before['edits']['FULL']);self.assertFalse(optimize21.findings(result,cat,req))
   data=json.loads(paid.call_args.args[0]['input'])
   self.assertEqual(data['required_reserve_evidence']['1']['opening_quote'],story['reserve_takes'][0]['opening_quote'])
   self.assertEqual([u['id'] for u in data['original_input']['units']],['B'])
 def test_unfixed_reserve_is_preserved_as_pending_with_main_intact(self):
  cat,raw,req,story=self.fixture()
  with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.call',return_value={'1':raw['edits']['R1']}) as paid:
   folder=self.folder(tmp);result=ep.ensure(raw,cat,folder,'fake',req,lambda **kw:None)
   self.assertEqual(result['pending_reserve_keys'],['R1']);self.assertEqual(paid.call_count,1)
   plan,_=sales_draft.normalize_draft(result,cat,story,[dict(id='T1',start=0,end=1),dict(id='T2',start=4,end=5)],{})
   main=copy.deepcopy(plan['edits'][0]);out=optimize21.hold_pending(plan,result,folder)
   self.assertEqual(out['edits'],[main]);self.assertEqual(out['pending_reserve_edits'][0]['map_id'],'R1')
   self.assertTrue((folder/'pending-reserves.json').exists());self.assertIn('ยังไม่รวมใน XML',out['pending_reserve_warning'])
 def test_budget_stop_does_not_block_valid_main_or_retry(self):
  cat,raw,req,_=self.fixture()
  with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.call',side_effect=ValueError('budget stopped')) as paid:
   result=ep.ensure(raw,cat,self.folder(tmp),'fake',req,lambda **kw:None)
   self.assertEqual(paid.call_count,1);self.assertEqual(result['edits']['FULL'],raw['edits']['FULL']);self.assertEqual(result['pending_reserve_keys'],['R1'])
 def test_main_context_failure_still_blocks_export(self):
  cat,raw,req,_=self.fixture();raw['events'][0]['requires']=['E1']
  with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.call',return_value={'0':raw['edits']['FULL'],'1':raw['edits']['R1']}) as paid:
   with self.assertRaisesRegex(ValueError,'คลิปหลัก'):ep.ensure(raw,cat,self.folder(tmp),'fake',req,lambda **kw:None)
   self.assertEqual(paid.call_count,1)
 def test_cycles_stop_before_paid_repair(self):
  cat,raw,req,_=self.fixture();raw['events'][0]['requires']=['E1'];raw['events'][1]['requires']=['E0']
  with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.call') as paid:
   with self.assertRaisesRegex(ValueError,'วงจร'):ep.ensure(raw,cat,self.folder(tmp),'fake',req,lambda **kw:None)
   paid.assert_not_called()
 def test_source_bounds_compaction_is_lossless(self):
  cat,_,req,_=self.fixture();schema=req['text']['format']['schema'];before=copy.deepcopy(schema)
  compact=transport21.compact_bounds(schema)
  def bounds(s):return {uid:c['properties']['first_word']['maximum'] for c in s['$defs']['source_selection']['anyOf'] for uid in c['properties']['unit_id']['enum']}
  self.assertEqual(bounds(schema),bounds(compact));self.assertEqual(schema,before)
 def test_addendum_scoped_to_21_only(self):
  self.assertIn(optimize21.rules(),runtime.rules_for(dict(sales_style='sales_2_1')))
  for style in ('original','natural','natural_v2'):
   self.assertNotIn(optimize21.rules(),runtime.rules_for(dict(sales_style=style)))
   self.assertFalse(optimize21.enabled(dict(sales_style=style)))

if __name__=='__main__':unittest.main()
