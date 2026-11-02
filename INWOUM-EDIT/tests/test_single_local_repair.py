import io,json,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
import single_repair as repair,sales_continuity as continuity,planner

def unit(uid,texts,start):
    words=[dict(text=t,start=start+i*.4,end=start+(i+1)*.4) for i,t in enumerate(texts)]
    return dict(id=uid,text=''.join(texts),words=words,start=start,end=words[-1]['end'],protected=False)

class SingleLocalRepairTests(unittest.TestCase):
    def setUp(self):
        self.catalog=[unit('U'+str(i),['เนื้อหา','สมบูรณ์'],i*10) for i in range(12)]
        self.catalog[5]=unit('U5',['นะ'],50)
        self.plan=dict(episode_policy='episode-continuity-v1',events=[dict(id='E1',subject='เรื่องเดิม',requires=[])],
            edits=[dict(name='คลิปเดียว',purpose='เล่าเรื่อง',selections=[dict(unit_id=u['id'],first_word=0,last_word=len(u['words'])-1,event_id='E1') for u in self.catalog])])
        self.bad=continuity.findings(self.plan,self.catalog)
        self.base=dict(instructions='single rules',text=dict(format={}),model='gpt-6.1-terra')
    def answer(self,patches):
        return {p['id']:dict(selections=[dict(s) for s in self.plan['edits'][0]['selections'][p['start']:p['end']] if s['unit_id']!='U5'],reason='ตัดคำโดด') for p in patches}
    def test_local_context_and_shared_event_enum(self):
        req,patches=repair.request_for(self.plan,self.catalog,self.bad,self.base);data=json.loads(req['input'])
        self.assertEqual(len(data['patches'][0]['units']),5)
        self.assertEqual(data['patches'][0]['before'][0]['text'],'เนื้อหาสมบูรณ์')
        self.assertEqual(req['text']['format']['schema']['$defs']['repair_event']['enum'],['E1'])
    def test_untouched_selections_and_original_remain_exact(self):
        _,patches=repair.request_for(self.plan,self.catalog,self.bad,self.base)
        fixed=repair.apply(self.plan,self.catalog,patches,self.answer(patches))
        self.assertEqual(fixed['edits'][0]['selections'],[s for s in self.plan['edits'][0]['selections'] if s['unit_id']!='U5'])
        self.assertEqual(len(self.plan['edits'][0]['selections']),12)
    def test_rejects_outside_units_invalid_bounds_or_wrong_event(self):
        _,patches=repair.request_for(self.plan,self.catalog,self.bad,self.base)
        for change in [dict(unit_id='U11'),dict(last_word=9999),dict(event_id='E99')]:
            answer=self.answer(patches);answer[patches[0]['id']]['selections'][0].update(change)
            with self.assertRaises(ValueError):repair.apply(self.plan,self.catalog,patches,answer)
    def test_preserves_protected_words(self):
        self.catalog[5]['protected']=True;_,patches=repair.request_for(self.plan,self.catalog,self.bad,self.base)
        with self.assertRaises(ValueError):repair.apply(self.plan,self.catalog,patches,self.answer(patches))
    def test_single_mode_routes_to_local_repair(self):
        _,patches=repair.request_for(self.plan,self.catalog,self.bad,self.base)
        with tempfile.TemporaryDirectory() as d,patch('parallel_requests.call',return_value=self.answer(patches)) as send:
            continuity.repair(self.plan,self.catalog,Path(d),'test',self.base,lambda **kw:None,content_mode='single')
            self.assertEqual(send.call_args.args[3],'single-local-continuity-repair-v2')

class SafeHTTPErrorTests(unittest.TestCase):
    def test_schema_error_no_retry_and_no_secret_echo(self):
        error=urllib.error.HTTPError('https://api.openai.com/v1/responses',400,'secret',{},io.BytesIO(json.dumps(dict(error=dict(code='invalid_json_schema',param='text.format.schema',message='sk-secret-private'))).encode()))
        with patch('urllib.request.urlopen',side_effect=error) as send:
            with self.assertRaises(RuntimeError) as raised:planner.plan_once(dict(model='gpt-6.1-terra'),'test-key')
            self.assertIn('รูปแบบคำตอบ',str(raised.exception));self.assertNotIn('sk-secret',str(raised.exception));self.assertEqual(send.call_count,1)
    def test_400_and_401_have_distinct_explanations(self):
        import api_errors
        for code,expected in [(400,'คำขอ'),(401,'ยืนยันตัวตน')]:
            error=urllib.error.HTTPError('url',code,'secret',{},io.BytesIO(b'invalid json'))
            self.assertIn(expected,api_errors.message(error))
