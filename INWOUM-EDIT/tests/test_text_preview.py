import copy,unittest
from text_preview import apply_removals,count_fillers

class TextPreviewTests(unittest.TestCase):
    def setUp(self):
        self.catalog=[dict(id='A',words=[dict(text=t) for t in ['แบบ','ไม่','ออกแบบ','แบบ','นี้','กันน้ำ']])]
        self.plan=dict(edits=[dict(selections=[dict(unit_id='A',first_word=0,last_word=5)])])
    def test_remove_filler_only_preserves_original_and_all_other_words(self):
        before=copy.deepcopy(self.plan)
        revised=apply_removals(self.plan,self.catalog,dict(removals=[dict(edit_index=0,selection_index=0,word_index=0,reason='ฟิลเลอร์')]))
        self.assertEqual(self.plan,before)
        self.assertEqual(revised['edits'][0]['selections'],[dict(unit_id='A',first_word=1,last_word=5)])
        self.assertEqual(count_fillers(revised,self.catalog),1)
    def test_cannot_remove_negation_or_compound_or_duplicate_position(self):
        for k in [1,2,9]:
            with self.assertRaises(ValueError):apply_removals(self.plan,self.catalog,dict(removals=[dict(edit_index=0,selection_index=0,word_index=k,reason='ผิด')]))
        row=dict(edit_index=0,selection_index=0,word_index=0,reason='ซ้ำ')
        with self.assertRaises(ValueError):apply_removals(self.plan,self.catalog,dict(removals=[row,row]))
    def test_no_removals_is_identical_and_whole_clip_cannot_be_dropped(self):
        self.assertEqual(apply_removals(self.plan,self.catalog,dict(removals=[])),self.plan)
        self.plan['edits'][0]['selections'][0]['last_word']=0
        with self.assertRaises(ValueError):apply_removals(self.plan,self.catalog,dict(removals=[dict(edit_index=0,selection_index=0,word_index=0,reason='หมด')]))
