import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sales_draft

class DraftBoundsRepairTests(unittest.TestCase):
    def setUp(self):
        self.catalog=[dict(id='A',words=[dict(text='ไม่'),dict(text='แพง')])]
        self.raw=dict(edits={'FULL':dict(name='เต็ม',purpose='รีวิว',selections=[dict(unit_id='A',first_word=0,last_word=2)]),
                             'V1':dict(name='สั้น',purpose='ราคา',selections=[dict(unit_id='A',first_word=0,last_word=1)])},unused=[],review_note='เดิม')
        self.request=dict(model='test',text=dict(format={}),instructions='original',input='source')
        self.answer={'e0_s0':dict(first_word=0,last_word=1,reason='คำสุดท้ายคือแพง หมายเลข 1')}
    def test_only_bad_bounds_corrected_once_and_original_kept(self):
        before=copy.deepcopy(self.raw)
        with tempfile.TemporaryDirectory() as tmp,patch('sales_draft.call',return_value=self.answer) as send:
            result=sales_draft.repair_draft_bounds(self.raw,self.catalog,Path(tmp),'test-key',self.request,lambda **e:None)
            self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.kwargs['max_calls'],3)
            self.assertNotIn('current_plan',send.call_args.args[0]['input'])
            self.assertEqual(result['edits']['FULL']['selections'][0]['last_word'],1)
            self.assertEqual(result['edits']['V1'],before['edits']['V1'])
            self.assertEqual(self.raw,before)
            self.assertTrue((Path(tmp)/'draft-bounds-repair.json').exists())
    def test_valid_plan_sends_no_extra_api(self):
        self.raw['edits']['FULL']['selections'][0]['last_word']=1
        with patch('sales_draft.call') as send:
            self.assertEqual(sales_draft.repair_draft_bounds(self.raw,self.catalog,None,'',self.request,None),self.raw)
            send.assert_not_called()
    def test_resume_reuses_completed_repair_no_second_api(self):
        with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.plan_once',return_value=(self.answer,dict(usage={}))) as send:
            folder=Path(tmp)
            first=sales_draft.repair_draft_bounds(self.raw,self.catalog,folder,'test-key',self.request,lambda **e:None)
            second=sales_draft.repair_draft_bounds(self.raw,self.catalog,folder,'',self.request,lambda **e:None)
            self.assertEqual(first,second)
            self.assertEqual(send.call_count,1)
    def test_unknown_unit_stops_before_api_instead_of_guessing(self):
        self.raw['edits']['FULL']['selections'][0]['unit_id']='MISSING'
        with patch('sales_draft.call') as send:
            with self.assertRaisesRegex(ValueError,'ไม่พบท่อน'):sales_draft.repair_draft_bounds(self.raw,self.catalog,None,'',self.request,None)
            send.assert_not_called()
    def test_invalid_repair_still_stops_no_clamping(self):
        self.answer['e0_s0']['last_word']=2
        with patch('sales_draft.call',return_value=self.answer):
            with self.assertRaises(ValueError):sales_draft.repair_draft_bounds(self.raw,self.catalog,None,'test-key',self.request,lambda **e:None)
