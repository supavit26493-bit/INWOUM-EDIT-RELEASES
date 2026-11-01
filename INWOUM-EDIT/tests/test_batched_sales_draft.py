import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import batched_sales_draft as batch,sales_draft,episode_continuity,selection_bounds
from test_sales_continuity import unit

class BatchedSalesDraftTests(unittest.TestCase):
 def request(self):
  story=dict(groups=[dict(id='G1',segment_ids=['T1'])],hooks=[dict(id='H1',segment_id='T1')],full_hook_id='H1',full_outline=[dict(segment_ids=['T1'])],continuity_links=[],versions=[dict(id='V1',group_ids=['G1'],hook_id='H1'),dict(id='V2',group_ids=['G1'],hook_id='H1')],reserve_takes=[dict(id='R1',segment_ids=['T1'])])
  schema=sales_draft.draft_schema(story,{})
  schema=episode_continuity.schema(schema,[unit('U1',['ขายดี'],0)])
  return dict(model='gpt-6.1-sol',store=False,max_output_tokens=24000,instructions='original contract',
   input=json.dumps(dict(approved_story_map=story,units=[dict(id='U1',phrases={'0':'ขายดี'})],source_bindings=[dict(id='T1',unit_ids=['U1'])])),
   text=dict(format=dict(type='json_schema',name='approved_map_draft',strict=True,schema=schema)))
 def answer(self,req):
  mids=list(req['text']['format']['schema']['properties']['edits']['properties'])
  return dict(edits={mid:dict(name=mid,purpose='ขาย',hook_count=0,selections=['U1,0,0,E1']) for mid in mids},
   unused=[],review_note='ครบเรื่อง',events=[dict(id='E1',subject='สินค้า',unit_ids=['U1'],keep_order=True,requires=[])])
 def test_scoped_request_keeps_source_and_one_complete_edit(self):
  req=self.request();before=copy.deepcopy(req);small=batch.clip_request(req,'R1')
  self.assertEqual(req,before);self.assertEqual(small['max_output_tokens'],32768)
  self.assertEqual(small['text']['format']['schema']['properties']['edits']['required'],['R1'])
  data=json.loads(small['input']);self.assertEqual(data['units'],json.loads(req['input'])['units'])
  self.assertEqual(data['approved_story_map']['versions'],[])
  self.assertEqual(data['approved_story_map']['reserve_takes'],[dict(id='R1',segment_ids=['T1'])])
 def test_every_clip_retained_in_map_order_and_event_names_do_not_collide(self):
  with tempfile.TemporaryDirectory() as tmp,patch('batched_sales_draft.call',side_effect=lambda req,*a,**k:self.answer(req)) as send:
   raw=batch.run(self.request(),Path(tmp),'fake',lambda **k:None)
   self.assertEqual(list(raw['edits']),['FULL','V1','V2','R1']);self.assertEqual(send.call_count,1)
   self.assertEqual([e['id'] for e in raw['events']],['g1:E1'])
   for mid,e in raw['edits'].items():self.assertEqual(e['selections'][0]['event_id'],'g1:E1')
   self.assertTrue(json.loads((Path(tmp)/'draft-batches.json').read_text('utf8'))['complete'])
 def test_interrupted_run_reuses_completed_responses_without_new_generation(self):
  import parallel_requests
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);calls=[]
   req=self.request();data=json.loads(req['input'])
   data['approved_story_map']['versions']=[dict(id=f'V{i}',group_ids=['G1'],hook_id='H1') for i in range(1,25)]
   req['input']=json.dumps(data)
   req['text']['format']['schema']=episode_continuity.schema(sales_draft.draft_schema(data['approved_story_map'],{}),[unit('U1',['ขายดี'],0)])
   def generate(req,key):
    calls.append(req)
    if len(calls)==2:raise RuntimeError('connection interrupted')
    return self.answer(req),dict(usage={})
   with patch('parallel_requests.plan_once',side_effect=generate):
    with self.assertRaises(RuntimeError):batch.run(req,folder,'fake',lambda **k:None)
   checkpoint=json.loads((folder/'draft-batches.json').read_text('utf8'))
   self.assertEqual(checkpoint['completed_ids'],['FULL']+[f'V{i}' for i in range(1,22)])
   # Unknown in-flight request must stay blocked, not silently be billed again.
   with patch('parallel_requests.plan_once') as send:
    with self.assertRaises(ValueError):batch.run(req,folder,'fake',lambda **k:None)
    send.assert_not_called()
 def test_old_modes_disabled(self):
  for style in ('original','natural','natural_v2'):
   self.assertFalse(batch.enabled(dict(content_mode='sales',sales_style=style)))
  self.assertFalse(batch.enabled(dict(content_mode='single',sales_style='sales_2_1')))
 def test_scope_removes_unrelated_units_and_schema_but_preserves_dependency_neighbors(self):
  req=self.request();data=json.loads(req['input'])
  data['approved_story_map']['continuity_links']=[dict(segment_ids=['T1','T3'])]
  data['units']=[dict(id=f'U{i}',phrases={'0':'คำจริง'}) for i in range(1,7)]
  data['source_bindings']=[dict(id=f'T{i}',unit_ids=[f'U{i}']) for i in range(1,7)]
  req['input']=json.dumps(data)
  schema=sales_draft.draft_schema(data['approved_story_map'],{})
  req['text']['format']['schema']=episode_continuity.schema(schema,[unit(f'U{i}',['คำจริง'],i) for i in range(1,7)])
  small=batch.batch_request(req,['V1','R1']);value=json.loads(small['input'])
  self.assertEqual([u['id'] for u in value['units']],['U1','U2','U3','U4'])
  pattern=small['text']['format']['schema']['$defs']['source_selection']['pattern']
  import re
  for i in range(1,5):self.assertTrue(re.fullmatch(pattern,f'U{i},0,0,E1'))
  self.assertFalse(re.fullmatch(pattern,'U5,0,0,E1'));self.assertFalse(re.fullmatch(pattern,'U1,1,1,E1'))
  self.assertNotIn('full_outline',value['approved_story_map'])
  self.assertLess(small['max_output_tokens'],32768)
 def test_fewer_long_clips_keep_reasoning_and_response_headroom(self):
  req=self.request()
  for mids in (['FULL'],['FULL','V1','V2','R1']):
   wire=batch.batch_request(req,mids)
   self.assertGreaterEqual(wire['max_output_tokens'],12000)
   self.assertEqual(wire['reasoning'] if 'reasoning' in wire else None,req.get('reasoning'))
 def test_repair_sends_only_requested_edit_and_dependency_event_sources(self):
  data=dict(events=[dict(id='E1',unit_ids=['U1'],requires=['E0']),dict(id='E0',unit_ids=['U0'],requires=[]),dict(id='E9',unit_ids=['U9'],requires=[])],affected_edits={'0':dict(selections=[dict(unit_id='U1',event_id='E1')]),'9':dict(selections=[dict(unit_id='U9',event_id='E9')])},original_input=dict(units=[dict(id='U0'),dict(id='U1'),dict(id='U9')]),issues=[dict(edit_index=0),dict(edit_index=9)])
  req=self.request();req['input']=json.dumps(data);small=batch.scope_correction(req,'0');out=json.loads(small['input'])
  self.assertEqual(list(out['affected_edits']),['0']);self.assertEqual([e['id'] for e in out['events']],['E1','E0'])
  self.assertEqual([u['id'] for u in out['original_input']['units']],['U0','U1'])
 def test_repairs_split_all_keys_without_changing_events_or_source(self):
  req=self.request();req['text']['format']['schema']=dict(type='object',properties={'0':dict(type='string'),'3':dict(type='string')},required=['0','3'],additionalProperties=False)
  before=copy.deepcopy(req)
  def reply(r,*a,**k):
   fields=r['text']['format']['schema']['properties'];self.assertEqual(len(fields),1)
   self.assertEqual(json.loads(r['input']),json.loads(before['input']))
   return {next(iter(fields)):'model output'}
  with tempfile.TemporaryDirectory() as tmp,patch('batched_sales_draft.call',side_effect=reply) as send:
   self.assertEqual(batch.correction(req,Path(tmp),'fake','repair'),{'0':'model output','3':'model output'})
   self.assertEqual(send.call_count,2);self.assertEqual(req,before)
 def test_malformed_response_does_not_advance_checkpoint(self):
  with tempfile.TemporaryDirectory() as tmp,patch('batched_sales_draft.call',return_value=dict(edits={'wrong':{}},unused=[])):
   with self.assertRaises(ValueError):batch.run(self.request(),Path(tmp),'fake',lambda **k:None)
   self.assertEqual(json.loads((Path(tmp)/'draft-batches.json').read_text('utf8'))['completed_ids'],[])

if __name__=='__main__':unittest.main()
