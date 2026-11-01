import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import episode_continuity as ep,single_clip,runtime_contract
from planner import fingerprint
from model_catalog import prepare_request
from test_sales_continuity import unit,selection

def case():
    catalog=[unit('result',['ช่องนี้','ไม่มีเนื้อหา'],10),unit('setup',['เดี๋ยวลองกด','ดูช่อง'],100)]
    raw=dict(events=[dict(id='E',subject='เปิดดูช่องและอธิบายผล',unit_ids=['setup','result'],keep_order=True,requires=[])],
        edits=[dict(name='เรื่อง',purpose='ตรวจช่อง',hook_count=0,selections=[dict(selection(uid,0,1),event_id='E') for uid in ['setup','result']])],unused=[])
    cfg=runtime_contract.configure(dict(content_mode='single',single_method='rearrange',model='gpt-6.1-sol',effort='medium'))
    return catalog,raw,single_clip.request_body(catalog,cfg)

def cache(folder):
    catalog,raw,request=case();old=prepare_request(request);old['instructions']='Old source timestamp rule'
    repair=copy.deepcopy(old);repair['instructions']='Old repair';repair['input']='{}'
    ledger=[]
    for stage,payload,response in [('single-story',old,raw),('episode-join-repair-v1',repair,{'0':raw['edits'][0],'unused':[]})]:
        digest=fingerprint(payload)
        (folder/(stage+'-request.json')).write_text(json.dumps(payload,ensure_ascii=False),'utf8')
        (folder/(stage+'-response.json')).write_text(json.dumps(dict(request_hash=digest,response=response),ensure_ascii=False),'utf8')
        ledger.append(dict(stage=stage,request_hash=digest,status='completed'))
    (folder/'api-usage.json').write_text(json.dumps(ledger),'utf8')
    (folder/'episode-failed-check.json').write_text(json.dumps(dict(findings=ep.findings(raw,catalog,single=True),candidate=raw),ensure_ascii=False),'utf8')
    return catalog,raw,request

class EventOrderRecoveryTests(unittest.TestCase):
    def test_alternate_take_may_go_back_in_source_time_when_story_order_is_correct(self):
        catalog,raw,_=case()
        self.assertEqual(ep.findings(raw,catalog,single=True)[0]['issue'],'event_action_order')
        raw['episode_order_policy']=ep.ORDER_POLICY
        self.assertEqual(ep.findings(raw,catalog,single=True),[])

    def test_reversed_narrative_stage_is_still_blocked(self):
        catalog,raw,_=case();raw['episode_order_policy']=ep.ORDER_POLICY
        raw['edits'][0]['selections'].reverse()
        self.assertIn('event_action_order',[r['issue'] for r in ep.findings(raw,catalog,single=True)])

    def test_completed_checkpoint_recovers_without_api_or_selection_changes(self):
        with tempfile.TemporaryDirectory() as d,patch('parallel_requests.call',side_effect=AssertionError('No API')):
            folder=Path(d);catalog,raw,request=cache(folder)
            fixed=ep.recover_single_checkpoint(folder,request,catalog,lambda **kw:None)
            self.assertEqual(fixed['edits'],raw['edits']);self.assertEqual(fixed['events'],raw['events'])
            self.assertEqual(ep.findings(fixed,catalog,single=True),[])
            self.assertTrue((folder/'episode-cache-recovery.json').exists())

    def test_unknown_request_changed_input_or_modified_candidate_is_never_recovered(self):
        for defect in ('unknown','input','candidate'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as d,patch('parallel_requests.call',side_effect=AssertionError('No API')):
                folder=Path(d);catalog,raw,request=cache(folder)
                if defect=='unknown':
                    ledger=json.loads((folder/'api-usage.json').read_text('utf8'));ledger[0]['status']='sent';(folder/'api-usage.json').write_text(json.dumps(ledger),'utf8')
                elif defect=='input':request['input']=json.dumps(dict(topic='changed',units=[]))
                else:
                    failure=json.loads((folder/'episode-failed-check.json').read_text('utf8'));failure['candidate']['edits'][0]['selections'].reverse();(folder/'episode-failed-check.json').write_text(json.dumps(failure),'utf8')
                with self.assertRaises(ValueError):ep.recover_single_checkpoint(folder,request,catalog,lambda **kw:None)
