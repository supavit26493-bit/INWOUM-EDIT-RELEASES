import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import episode_continuity as ep
from planner import fingerprint
import test_episode_continuity as fixture

class ReferenceContextTests(unittest.TestCase):
 def story(self,text):
  catalog,raw=fixture.EpisodeTests().setup_story();raw['edits'][0]['selections']=[raw['edits'][0]['selections'][0]]
  catalog[0]['words'][0]['text']=text
  return catalog,raw
 def test_self_contained_introductions_are_not_missing_context(self):
  for text in ['อันนี้เป็นกล่องอุปกรณ์ตกปลา','อันนี้คือกระเป๋ากันน้ำ','ตรงนี้คือช่องใส่แบตเตอรี่','อันนี้ เป็น ไมโครโฟนไร้สาย']:
   with self.subTest(text=text):
    catalog,raw=self.story(text)
    self.assertEqual(ep.reference_review(text),'self_contained_introduction')
    self.assertEqual(ep.findings(raw,catalog),[]);self.assertEqual(ep.reference_warnings(raw,catalog),[])
 def test_uncertain_reference_is_visible_warning_without_extra_api(self):
  for text in ['อันนี้ดีมาก','ตรงนี้ดีกว่า','อันนี้เป็นแบบนี้','อันนี้เป็นยังไง']:
   catalog,raw=self.story(text)
   self.assertEqual(ep.findings(raw,catalog),[])
   self.assertEqual(ep.reference_warnings(raw,catalog)[0]['severity'],'warning')
   with tempfile.TemporaryDirectory() as d,patch('parallel_requests.call') as send:
    fixed=ep.ensure(raw,catalog,Path(d),'',{},lambda **kw:None)
    send.assert_not_called();self.assertEqual(fixed['edits'],raw['edits'])
    report=json.loads((Path(d)/'episode-continuity.json').read_text('utf8'))
    self.assertEqual(len(report['reference_warnings']),1)
 def test_action_result_still_requires_context(self):
  for text in ['พอติดแล้วแน่นมาก','หลังติดใช้งานได้เลย','ติดแล้วเห็นชัด']:
   catalog,raw=self.story(text)
   self.assertIn('unintroduced_reference',[f['issue'] for f in ep.findings(raw,catalog)])
 def test_prior_context_in_same_event_resolves_demonstrative(self):
  catalog,raw=fixture.EpisodeTests().setup_story();raw['edits'][0]['selections']=[raw['edits'][0]['selections'][i] for i in [0,2]]
  catalog[2]['words'][0]['text']='พอติดแล้ว'
  self.assertEqual(ep.findings(raw,catalog),[]);self.assertEqual(ep.reference_warnings(raw,catalog),[])
 def checkpoint(self,folder):
  catalog,raw=self.story('อันนี้เป็นกล่องอุปกรณ์ตกปลา');raw['episode_order_policy']=ep.ORDER_POLICY
  request=dict(model='test',instructions='unchanged',input='{}')
  saved=dict(model='test',instructions='unchanged repair',input=json.dumps(dict(events=raw['events'],original_input={},affected_edits={'0':raw['edits'][0]})))
  answer={'0':copy.deepcopy(raw['edits'][0])};candidate=copy.deepcopy(raw);digest=fingerprint(saved)
  files={'episode-failed-check.json':dict(findings=[dict(edit_index=0,selection_index=0,issue='unintroduced_reference')],candidate=candidate),'episode-join-repair-v1-request.json':saved,'episode-join-repair-v1-response.json':dict(request_hash=digest,response=answer),'api-usage.json':[dict(stage='episode-join-repair-v1',status='completed',request_hash=digest)]}
  for name,data in files.items():(folder/name).write_text(json.dumps(data),'utf8')
  return catalog,raw,request
 def test_verified_checkpoint_reuses_exact_completed_answer(self):
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d);catalog,raw,request=self.checkpoint(folder)
   recovered=ep.recover_reference_checkpoint(raw,catalog,folder,request,lambda **kw:None)
   self.assertEqual(recovered,raw)
   self.assertEqual(json.loads((folder/'episode-reference-recovery.json').read_text('utf8'))['api_calls_added'],0)
 def test_changed_source_or_tampered_checkpoint_is_not_reused(self):
  for kind in ('input','hash','candidate','ledger','model','protected'):
   with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
    folder=Path(d);catalog,raw,request=self.checkpoint(folder)
    if kind=='input':request['input']='{"changed":true}'
    if kind=='model':request['model']='other'
    if kind=='hash':
     p=folder/'episode-join-repair-v1-response.json';v=json.loads(p.read_text('utf8'));v['request_hash']='bad';p.write_text(json.dumps(v),'utf8')
    if kind=='candidate':
     p=folder/'episode-failed-check.json';v=json.loads(p.read_text('utf8'));v['candidate']['edits'][0]['name']='tampered';p.write_text(json.dumps(v),'utf8')
    if kind=='ledger':(folder/'api-usage.json').write_text('[]','utf8')
    if kind=='protected':raw['edits'][0]['selections'][0]['last_word']=0
    self.assertIsNone(ep.recover_reference_checkpoint(raw,catalog,folder,request,lambda **kw:None))
 def test_invalid_bounds_and_event_dependencies_remain_blocking(self):
  catalog,raw=self.story('อันนี้เป็นกล่องอุปกรณ์ตกปลา');raw['edits'][0]['selections'][0]['last_word']=999
  self.assertEqual(ep.findings(raw,catalog)[0]['issue'],'invalid_source_bounds')
  catalog,raw=self.story('อันนี้เป็นกล่องอุปกรณ์ตกปลา');raw['events'][0]['requires']=['film']
  self.assertIn('missing_event_context',[f['issue'] for f in ep.findings(raw,catalog)])
