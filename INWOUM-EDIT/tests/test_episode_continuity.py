import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import episode_continuity as ep,runtime_contract,single_clip,sales_map
from test_sales_continuity import unit,selection

def fixture_events(raw):
    """One independent explanation fixture, not a causal demonstration."""
    edits=ep.edit_rows(raw);ids=sorted({s['unit_id'] for e in edits for s in e['selections']})
    raw['events']=[dict(id='E1',subject='เรื่องตัวอย่าง',unit_ids=ids,keep_order=False,requires=[])]
    for edit in edits:
        edit['hook_count']=0
        for s in edit['selections']:s['event_id']='E1'
    return raw

class EpisodeTests(unittest.TestCase):
    def setup_story(self):
        catalog=[unit('A',['นี่คือเคส','ใส่เคส'],0),unit('B',['นี่คือฟิล์ม','ติดฟิล์ม'],10),unit('C',['เคสใส่แล้ว','เห็นสีเครื่อง'],20)]
        def pick(uid,event):return dict(selection(uid,0,1),event_id=event)
        events=[dict(id='case',subject='รีวิวเคส',unit_ids=['A','C'],keep_order=True,requires=[]),dict(id='film',subject='ติดฟิล์ม',unit_ids=['B'],keep_order=True,requires=[])]
        raw=dict(events=events,edits=[dict(name='เรื่อง',purpose='ครบเรื่อง',hook_count=0,selections=[pick('A','case'),pick('B','film'),pick('C','case')])],unused=[])
        return catalog,raw

    def test_case_film_case_is_blocked_but_complete_combined_events_are_allowed(self):
        catalog,raw=self.setup_story()
        self.assertIn('interleaved_event',[r['issue'] for r in ep.findings(raw,catalog)])
        raw['edits'][0]['selections']=[raw['edits'][0]['selections'][i] for i in [0,2,1]]
        self.assertEqual(ep.findings(raw,catalog),[])

    def test_demo_result_cannot_precede_setup_and_reference_needs_context(self):
        catalog,raw=self.setup_story();picks=raw['edits'][0]['selections']
        raw['edits'][0]['selections']=[picks[2],picks[0],picks[1]]
        self.assertIn('event_action_order',[r['issue'] for r in ep.findings(raw,catalog)])
        raw['edits'][0]['selections']=[picks[1]];raw['events'][1]['requires']=['case']
        self.assertIn('missing_event_context',[r['issue'] for r in ep.findings(raw,catalog)])
        raw['events'][1]['requires']=[];catalog[1]['words'][0]['text']='พอติดแล้ว'
        self.assertIn('unintroduced_reference',[r['issue'] for r in ep.findings(raw,catalog)])

    def test_source_membership_and_single_hook_validation(self):
        catalog,raw=self.setup_story();raw['edits'][0]['selections'][1]['event_id']='case'
        self.assertIn('event_source_mismatch',[r['issue'] for r in ep.findings(raw,catalog)])
        raw['edits'][0]['hook_count']=1
        self.assertIn('invalid_hook_count',[r['issue'] for r in ep.findings(raw,catalog,single=True)])

    def test_original_and_legacy_do_not_get_event_policy(self):
        self.assertFalse(ep.enabled({}))
        for cfg in [dict(sales_style='original'),dict(content_mode='single',single_style='original',single_method='rearrange')]:
            self.assertNotIn('episode_policy',runtime_contract.configure(dict(cfg,episode_policy=ep.POLICY)))
        cfg=runtime_contract.configure(dict(content_mode='single',single_method='chronological'))
        self.assertNotIn('episode_policy',cfg)

    def test_valid_event_plan_needs_no_extra_api(self):
        catalog,raw=self.setup_story();raw['edits'][0]['selections']=[raw['edits'][0]['selections'][i] for i in [0,2,1]]
        with tempfile.TemporaryDirectory() as d,patch('parallel_requests.call') as send:
            expected=copy.deepcopy(raw);expected['episode_order_policy']=ep.ORDER_POLICY
            self.assertEqual(ep.ensure(raw,catalog,Path(d),'',{},lambda **kw:None),expected)
            send.assert_not_called();self.assertTrue((Path(d)/'episode-continuity.json').exists())

    def test_export_cannot_bypass_event_checks_after_a_local_plan_edit(self):
        import engine
        catalog,raw=self.setup_story();raw['episode_policy']=ep.POLICY
        with self.assertRaisesRegex(ValueError,'สลับเหตุการณ์'):
            engine.export(None,dict(default_audio='default-audio-v2'),None,raw,catalog,None,None)

    def test_targeted_repair_keeps_other_version_and_events_immutable(self):
        catalog,raw=self.setup_story();valid=copy.deepcopy(raw['edits'][0]);valid['selections']=[valid['selections'][i] for i in [0,2,1]]
        raw['edits'].append(copy.deepcopy(valid))
        cfg=runtime_contract.configure(dict(content_mode='single',single_method='rearrange',model='gpt-6.1-sol'))
        request=single_clip.request_body(catalog,cfg)
        with tempfile.TemporaryDirectory() as d,patch('parallel_requests.call',return_value={'0':valid,'unused':[]}) as send:
            fixed=ep.ensure(raw,catalog,Path(d),'test',request,lambda **kw:None,single=True)
            self.assertEqual(fixed['edits'][1],raw['edits'][1]);self.assertEqual(fixed['events'],raw['events'])
            self.assertEqual(ep.findings(fixed,catalog,single=True),[]);self.assertEqual(send.call_count,1)
