import copy,unittest
from sales_draft import normalize_draft

class PartialUnusedTests(unittest.TestCase):
    def fixture(self):
        catalog=[dict(id='A',start=0,end=4,text='abcd',words=[dict(text=t,start=i,end=i+1) for i,t in enumerate('abcd')]),dict(id='B',start=4,end=5,text='e',words=[dict(text='e',start=4,end=5)])]
        raw=dict(edits=dict(FULL=dict(name='full',purpose='review',selections=[dict(unit_id='A',first_word=1,last_word=2)])),unused=[dict(unit_id='A',decision='duplicate',reason='omit repeated words'),dict(unit_id='B',decision='production',reason='unusable')])
        return catalog,raw,dict(versions=[],segment_review=[])
    def test_partial_review_keeps_exact_selections_and_raw_response(self):
        cat,raw,story=self.fixture();before=copy.deepcopy(raw)
        plan,_=normalize_draft(raw,cat,story,[],{})
        self.assertEqual(raw,before)
        self.assertEqual([{k:s[k] for k in ('unit_id','first_word','last_word')} for s in plan['edits'][0]['selections']],raw['edits']['FULL']['selections'])
        self.assertEqual(plan['partial_unit_reviews'][0]['omitted_phrase_indices'],[0,3])
        reviews={r['unit_id']:r for r in plan['source_review']}
        self.assertEqual(reviews['A']['decision'],'used');self.assertEqual(reviews['B']['decision'],'production')
        self.assertEqual(reviews['A']['partial_omission_review']['reason'],'omit repeated words')
    def test_duplicates_unknown_ids_and_fully_used_conflict_still_rejected(self):
        for case in ('duplicate','unknown','fully_used','bad_bound','bad_reason'):
            cat,raw,story=self.fixture()
            if case=='duplicate':raw['unused'].append(copy.deepcopy(raw['unused'][0]))
            if case=='unknown':raw['unused'][0]['unit_id']='X'
            if case=='fully_used':raw['edits']['FULL']['selections'][0].update(first_word=0,last_word=3)
            if case=='bad_bound':raw['edits']['FULL']['selections'][0]['last_word']=99
            if case=='bad_reason':raw['unused'][0]['reason']=''
            with self.subTest(case=case),self.assertRaises(ValueError):normalize_draft(raw,cat,story,[],{})
