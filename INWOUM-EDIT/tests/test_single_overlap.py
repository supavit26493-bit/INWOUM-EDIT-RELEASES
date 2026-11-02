import copy,unittest
import single_overlap

class SingleOverlapTests(unittest.TestCase):
    def setUp(self):
        texts=['สำหรับคนไม่คิดมาก','เหมาะ','อัน','เนี้ย','แบต','ลดเร็ว']
        words=[dict(text=t,start=i*.3,end=(i+1)*.3) for i,t in enumerate(texts)]
        self.catalog=[dict(id='U1',words=words,protected=False)]
        self.plan=dict(edits=[dict(name='คลิป',selections=[dict(unit_id='U1',first_word=2,last_word=5,event_id='battery'),dict(unit_id='U1',first_word=0,last_word=2,event_id='summary')])])
    def test_shared_intro_stays_with_following_clause(self):
        original=copy.deepcopy(self.plan);fixed,changes=single_overlap.repair(self.plan,self.catalog)
        self.assertEqual(fixed['edits'][0]['selections'][0],original['edits'][0]['selections'][0])
        self.assertEqual(fixed['edits'][0]['selections'][1]['last_word'],1)
        self.assertEqual(fixed['edits'][0]['selections'][1]['event_id'],'summary')
        self.assertEqual(self.plan,original);self.assertEqual(len(changes),1)
        self.assertEqual(changes[0]['retained_selection'],0)
    def test_reversed_timeline_order_same_source_edge_owner(self):
        self.plan['edits'][0]['selections'].reverse();fixed,changes=single_overlap.repair(self.plan,self.catalog)
        self.assertEqual(fixed['edits'][0]['selections'][0]['last_word'],1)
        self.assertEqual(fixed['edits'][0]['selections'][1]['first_word'],2)
    def test_content_word_long_token_and_protected_unit_are_untouched(self):
        for case in ['content','long','protected','no_continuation']:
            cat=copy.deepcopy(self.catalog)
            if case=='content':cat[0]['words'][2]['text']='แบตเตอรี่'
            elif case=='long':cat[0]['words'][2]['end']=2
            elif case=='protected':cat[0]['protected']=True
            else:cat[0]['words'][3]['text']='ไม่แน่ใจ'
            fixed,changes=single_overlap.repair(self.plan,cat)
            self.assertIs(fixed,self.plan);self.assertEqual(changes,[])
    def test_multiword_overlap_is_not_guessed(self):
        self.plan['edits'][0]['selections'][1]['last_word']=3
        fixed,changes=single_overlap.repair(self.plan,self.catalog)
        self.assertIs(fixed,self.plan);self.assertEqual(changes,[])
    def test_valid_plan_is_identity_and_repair_is_idempotent(self):
        fixed,_=single_overlap.repair(self.plan,self.catalog)
        again,changes=single_overlap.repair(fixed,self.catalog)
        self.assertIs(again,fixed);self.assertEqual(changes,[])
