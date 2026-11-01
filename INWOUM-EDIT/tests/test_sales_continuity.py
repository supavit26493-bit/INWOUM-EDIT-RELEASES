import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sales_continuity as flow,runtime_contract
from fast_logic import resolve_plan

def unit(uid,texts,start):
    words=[dict(text=t,start=start+i*.3,end=start+(i+1)*.3) for i,t in enumerate(texts)]
    return dict(id=uid,text=''.join(texts),words=words,start=start,end=words[-1]['end'],protected=False)
def selection(uid,a,b):return dict(unit_id=uid,first_word=a,last_word=b)
def plan(rows):return dict(edits=[dict(name='เต็ม',purpose='รีวิว',selections=rows)])

class ContinuityTests(unittest.TestCase):
    def test_new_sales_default_and_explicit_original(self):
        self.assertEqual(runtime_contract.configure({})['sales_style'],'sales_2_1')
        self.assertEqual(runtime_contract.configure(dict(sales_style='original'))['sales_style'],'original')
        with self.assertRaises(ValueError):runtime_contract.configure(dict(sales_style='unknown'))
        self.assertFalse(flow.enabled(dict(content_mode='single',sales_style='natural')))
        self.assertFalse(flow.enabled({})) # legacy saved jobs
    def test_green_tea_example_keeps_right_continuation(self):
        catalog=[unit('A',['ชาเขียว','นี่','มัน','อร่อย','ดี'],0),unit('B',['ดี','นะ','ผม','ชอบ','ซื้อ','กิน','ประจำ'],4)]
        before=plan([selection('A',0,4),selection('B',0,6)])
        result,changes=flow.clean(before,catalog)
        self.assertEqual(result['edits'][0]['selections'],[selection('A',0,4),selection('B',1,6)])
        self.assertEqual(before['edits'][0]['selections'][1]['first_word'],0)
        self.assertEqual(changes[0]['text'],'ดี')
    def test_in_source_reduplication_is_not_cut(self):
        catalog=[unit('A',['จริง','จริง','ดี','ดี'],0)]
        before=plan([selection('A',0,1),selection('A',2,3)])
        result,changes=flow.clean(before,catalog)
        self.assertEqual(result,before);self.assertFalse(changes)
    def test_word_island_flagged_but_complete_reaction_allowed(self):
        catalog=[unit('A',['นะ','ประโยค','เต็ม'],10),unit('B',['พูด','ต่อ'],30)]
        before=plan([selection('A',0,0),selection('B',0,1)])
        self.assertEqual(flow.findings(before,catalog)[0]['text'],'นะ')
        catalog[0]['words'][0]['text']='จริงเหรอ';self.assertFalse(flow.findings(before,catalog))
    def test_default_does_not_restore_long_pause_to_hide_short_fragments(self):
        catalog=[unit('A',['นี่คือ','สินค้า','ที่ชอบ','มาก'],0)]
        # One selection spanning .5 s internal removed interval; both remaining edges <.6 s.
        catalog[0]['words'][-1]['end']=1.2
        p=plan([selection('A',0,3)])
        silence=dict(profile='compact',removed_frames=[(14,39)],policy='vad-negative-noise-v4')
        project=dict(duration=100,primary=[dict(start=0,end=100)],videos=[],audios=[])
        with patch('project.validate_edits',side_effect=lambda edits,project:edits):
            original=resolve_plan(p,catalog,silence,project)
            natural=resolve_plan(p,catalog,dict(silence,sales_continuity=flow.POLICY),project)
        self.assertEqual(original[0]['ranges'],natural[0]['ranges'])
        self.assertFalse(any(r['start']<.5<r['end'] for r in natural[0]['ranges']))
        self.assertEqual(silence['removed_frames'],[(14,39)])
    def test_valid_passages_do_not_call_extra_api(self):
        catalog=[unit('A',['ชาเขียว','อร่อย'],0)]
        p=plan([selection('A',0,1)])
        with patch('parallel_requests.call') as send:
            self.assertEqual(flow.repair(p,catalog,None,'',{},None),(p,[]));send.assert_not_called()
    def test_targeted_repair_keeps_other_version_and_reconciles_review(self):
        catalog=[unit('A',['นะ'],10),unit('B',['ประโยค','สมบูรณ์'],30)]
        p=plan([selection('A',0,0),selection('B',0,1)])
        p['edits'].append(dict(name='อีกคลิป',purpose='รีวิว',selections=[selection('B',0,1)]))
        p['source_review']=[dict(unit_id='A',decision='used',reason='เดิม')]
        answer={'e0':dict(selections=[selection('B',0,1)],reason='เลือกวลีสมบูรณ์')}
        base=dict(text=dict(format={}))
        with tempfile.TemporaryDirectory() as d,patch('parallel_requests.call',return_value=answer) as send:
            result,bad=flow.repair(p,catalog,Path(d),'test',base,lambda **kw:None)
            self.assertTrue(bad)
            self.assertEqual(result['edits'][1],p['edits'][1])
            self.assertEqual(result['source_review'][0]['decision'],'incomplete')
            self.assertTrue((Path(d)/'sales-continuity-repair.json').exists())
            self.assertEqual(send.call_count,1)
            self.assertIn(flow.INSTRUCTIONS,send.call_args.args[0]['instructions'])
            self.assertEqual(p['source_review'][0]['decision'],'used')
    def test_repair_cannot_restore_forbidden_price(self):
        catalog=[unit('A',['นะ'],10),unit('B',['ดี','ราคา','สองร้อยบาท'],30)]
        p=plan([selection('A',0,0),selection('B',0,0)])
        answer={'e0':dict(selections=[selection('B',0,2)],reason='วลีเต็ม')}
        with tempfile.TemporaryDirectory() as d,patch('parallel_requests.call',return_value=answer):
            with self.assertRaisesRegex(ValueError,'ราคา'):
                flow.repair(p,catalog,Path(d),'test',dict(text=dict(format={})),lambda **kw:None)
