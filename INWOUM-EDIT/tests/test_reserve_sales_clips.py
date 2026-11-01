import copy,unittest,json,tempfile
from pathlib import Path
from unittest.mock import patch
import sales_reserve as reserve,sales_map,sales_draft
from test_sales_continuity import unit,selection

class ReserveSalesClipTests(unittest.TestCase):
 def setUp(self):
  p=patch('api_budget.count_input',return_value=1000);p.start();self.addCleanup(p.stop)
 def fixture(self):
  catalog=[unit('A',['เคสนี้','กันกระแทก'],0),unit('B',['ติดง่าย','ไม่ต้องถอดเคส'],4),unit('C',['ไม่อยากให้เครื่องพัง','ลองเคสนี้'],8)]
  main=dict(name='FULL',edit_role='FULL',selections=[selection('A',0,1)])
  evidence=dict(opening_quote='ไม่อยากให้เครื่องพังลองเคสนี้',payoff_quote='กันกระแทก',recovery_quote='ติดง่าย',sales_angle='กันเครื่องพังและติดตั้งง่าย')
  alternate=dict(name='สำรอง',reserve_title='ติดง่าย กันเครื่องพัง',edit_role='reserve',hook_count=1,
   selections=[selection('C',0,1),selection('B',0,1),selection('A',1,1)],reserve_sales_evidence=evidence)
  return catalog,dict(edits=[main,alternate])

 def test_borrow_used_main_support_and_reorder_whole_passages_without_changing_main(self):
  catalog,plan=self.fixture();main=copy.deepcopy(plan['edits'][0]);reserve.validate_plan(plan,catalog)
  reserve.label_plan(plan,catalog);self.assertEqual(plan['edits'][0],main)
  self.assertEqual(plan['reserve_policy'],reserve.POLICY);self.assertTrue(plan['edits'][1]['name'].startswith('เทคสำรอง 01'))

 def test_orphan_fragment_missing_payoff_wrong_opening_or_recovery_is_rejected(self):
  catalog,plan=self.fixture()
  for field in ('payoff_quote','recovery_quote','opening_quote'):
   bad=copy.deepcopy(plan);bad['edits'][1]['reserve_sales_evidence'][field]='ไม่มีคำนี้ในคลิป'
   with self.assertRaises(ValueError):reserve.validate_plan(bad,catalog)
  bad=copy.deepcopy(plan);bad['edits'][1]['selections']=[selection('B',0,0)]
  with self.assertRaises(ValueError):reserve.validate_plan(bad,catalog)
 def test_audit_names_incomplete_reserve_without_altering_selections(self):
  catalog,plan=self.fixture();before=copy.deepcopy(plan)
  plan['edits'][1]['map_id']='R1';plan['edits'][1]['reserve_sales_evidence']['payoff_quote']='ไม่มีจริง'
  source=copy.deepcopy(plan);audit=reserve.audit_plan(plan,catalog)
  self.assertEqual(audit['invalid'][0]['map_id'],'R1');self.assertEqual(plan,source)
  self.assertEqual(audit['extra_api_calls'],0)

 def test_entirely_reused_main_and_forbidden_price_still_rejected(self):
  catalog,plan=self.fixture();bad=copy.deepcopy(plan)
  bad['edits'][1]['selections']=[selection('A',0,1)]
  with self.assertRaises(ValueError):reserve.validate_plan(bad,catalog)
  catalog[1]=unit('B',['ติดง่าย','สองร้อยบาท'],4)
  with self.assertRaises(ValueError):reserve.validate_plan(plan,catalog)

 def test_new_schema_has_exact_evidence_and_no_fragment_exception(self):
  schema=reserve.map_schema(sales_map.schema(['T001','T002']))
  fields=schema['properties']['reserve_takes']['items']
  for name in ('opening_quote','payoff_quote','recovery_quote','sales_angle'):self.assertIn(name,fields['required'])
  self.assertIn('already-selected',reserve.INSTRUCTIONS);self.assertIn('COMPLETE',reserve.INSTRUCTIONS)
  self.assertNotIn('No mandatory hook/close',reserve.INSTRUCTIONS)
  self.assertNotIn('minimum commercial strength',reserve.INSTRUCTIONS)
 def test_reserve_limit_allows_zero_to_five_and_never_forces_five(self):
  base=sales_map.schema(['T001']);schema=reserve.map_schema(base)
  self.assertEqual(schema['properties']['reserve_takes']['maxItems'],5)
  self.assertNotIn('minItems',schema['properties']['reserve_takes'])
  source=[dict(id='T001',text='เคสนี้กันกระแทกติดง่าย')]
  row=dict(title='ขาย',segment_ids=['T001'],reason='ทางเลือก',opening_quote='เคสนี้',payoff_quote='กันกระแทก',recovery_quote='ติดง่าย',sales_angle='ปกป้องเครื่อง')
  for count in (0,5):reserve.validate_map(dict(versions=[],reserve_takes=[dict(row,id=f'R{i}') for i in range(count)]),source)
  story=dict(versions=[],reserve_takes=[dict(row,id=f'R{i}') for i in range(6)]);before=copy.deepcopy(story)
  with self.assertRaisesRegex(ValueError,'เกิน 5'):reserve.validate_map(story,source)
  self.assertEqual(story,before)
 def test_plan_limit_rejects_six_without_dropping_or_reordering_clips(self):
  catalog,plan=self.fixture();plan['edits']+=copy.deepcopy(plan['edits'][1:])*5;before=copy.deepcopy(plan)
  with self.assertRaisesRegex(ValueError,'เกิน 5'):reserve.validate_plan(plan,catalog)
  self.assertEqual(plan,before)

 def test_map_quotes_must_be_real_and_have_distinct_opening_payoff(self):
  segments=[dict(id='T001',start=0,text='เคสนี้กันกระแทก'),dict(id='T002',start=4,text='ติดง่ายไม่ต้องถอดเคส')]
  row=dict(id='R1',title='ติดง่าย กันกระแทก',segment_ids=['T001','T002'],reason='กู้ประเด็นติดตั้ง',opening_quote='เคสนี้',payoff_quote='กันกระแทก',recovery_quote='ติดง่าย',sales_angle='สะดวกและป้องกันเครื่อง')
  story=dict(versions=[],reserve_takes=[row]);reserve.validate_map(story,segments)
  for change in (dict(payoff_quote='เคสนี้'),dict(recovery_quote='แต่งขึ้น'),dict(sales_angle='')):
   bad=copy.deepcopy(story);bad['reserve_takes'][0].update(change)
   with self.assertRaises(ValueError):reserve.validate_map(bad,segments)

 def test_normalized_draft_keeps_evidence_and_main_selections(self):
  catalog,plan=self.fixture();e=plan['edits'][1]['reserve_sales_evidence']
  story=dict(versions=[],reserve_takes=[dict(id='R1',title='ติดง่าย',**e)],segment_review=[])
  raw=dict(edits={'FULL':dict(name='FULL',purpose='หลัก',selections=[selection('A',0,1)]),'R1':dict(name='R1',purpose='ขายอีกมุม',selections=copy.deepcopy(plan['edits'][1]['selections']))},unused=[])
  result,_=sales_draft.normalize_draft(raw,catalog,story,[],{})
  self.assertEqual(result['edits'][1]['reserve_sales_evidence'],e)
  self.assertEqual([(s['unit_id'],s['first_word'],s['last_word']) for s in result['edits'][0]['selections']],[('A',0,1)])

 def test_new_commercial_reserve_exports_with_main_support_and_recovered_content(self):
  from test_auto_xml import AutoXMLTests
  from test_episode_continuity import fixture_events
  import runtime_contract as runtime,engine
  from verify_outputs import check
  with tempfile.TemporaryDirectory() as tmp:
   job,cfg,prepared,story,draft=AutoXMLTests().setup_job(Path(tmp))
   cfg=runtime.configure(dict(cfg,sales_style=reserve.STYLE))
   transcript=prepared[2]
   story['reserve_takes']=[dict(id='R1',title='สำรองที่ประกอบครบ',segment_ids=['T001','T003','T002'],reason='กู้ช่วงที่สาม',opening_quote=transcript[0]['text'],payoff_quote=transcript[1]['text'],recovery_quote=transcript[2]['text'],sales_angle='ประกอบบริบทกับเนื้อหาที่เหลือ')]
   picks=copy.deepcopy(draft['edits']['FULL']['selections']);draft['edits']['FULL']['selections']=picks[:2]
   draft['edits']['R1']=dict(name='สำรอง',purpose='ครบเรื่องอีกมุม',selections=[picks[0],picks[2],picks[1]])
   fixture_events(draft);draft['edits']['R1']['hook_count']=1
   expected=[(s['unit_id'],s['first_word'],s['last_word']) for s in draft['edits']['FULL']['selections']]
   def reply(req,key):
    if req['text']['format']['name']=='sales_story_map':return copy.deepcopy(story),dict(usage={})
    answer=copy.deepcopy(draft)
    if 'assembly_scope' in json.loads(req['input']):
     mids=json.loads(req['input'])['assembly_scope']['map_ids']
     answer['edits']={mid:answer['edits'][mid] for mid in mids};answer['unused']=[]
    return answer,dict(usage={})
   with patch('parallel_requests.plan_once',side_effect=reply) as send:
    result=engine.run(cfg,job,'fake-key',prepared=prepared)
   plan=json.loads((job/'plan.json').read_text('utf8'))
   self.assertEqual(send.call_count,2);self.assertEqual(check(job)['versions'],2)
   self.assertEqual(plan['reserve_policy'],reserve.POLICY)
   self.assertEqual([(s['unit_id'],s['first_word'],s['last_word']) for s in plan['edits'][0]['selections']],expected)
   self.assertEqual([s['unit_id'] for s in plan['edits'][1]['selections']],['U0001','U0003','U0002'])
   self.assertTrue((job/result['revision']/'EDITS-WITH-USAGE.xml').is_file())

if __name__=='__main__':unittest.main()
