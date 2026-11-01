import copy
import unittest
import fast_editorial
import story_editorial


class ReviewCoverageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.catalog=[dict(id=u,start=i,end=i+1,text=u,words=[],protected=False) for i,u in enumerate(['A','B','C'])]
        self.draft=dict(edits=[dict(name='draft',purpose='เรื่อง',selections=[dict(unit_id='A',first_word=0,last_word=0)])],unused=[dict(unit_id=u,decision='alternative',reason='ทางเลือก '+u) for u in ['B','C']])
        self.final=copy.deepcopy(self.draft);self.final['edits'][0]['name']='reviewed';self.final['unused']=self.final['unused'][:1]

    def test_recovers_only_missing_still_unused_reason_and_preserves_responses(self):
        original=copy.deepcopy(self.final)
        plan,recovered=story_editorial.normalize_review(self.final,self.catalog,self.draft)
        self.assertEqual(self.final,original)
        expected=copy.deepcopy(original);expected['unused'].append(self.draft['unused'][1])
        self.assertEqual(plan['edits'],fast_editorial.normalize_plan(expected,self.catalog)['edits'])
        self.assertEqual(plan['edits'][0]['name'],'reviewed')
        self.assertEqual([r['unit_id'] for r in recovered],['C'])
        row=next(r for r in plan['source_review'] if r['unit_id']=='C')
        self.assertEqual(row['reason'],'ทางเลือก C')
        self.assertEqual(row['review_source'],'ai_draft_unused_reason_recovered')

    def test_complete_review_uses_its_own_reason(self):
        plan,recovered=story_editorial.normalize_review(self.draft,self.catalog,self.draft)
        self.assertEqual(recovered,[])
        self.assertTrue(all(r['review_source']=='ai_unused_reason' for r in plan['source_review'] if r['decision']!='used'))

    def test_no_recovery_from_previously_used_unit_or_incomplete_draft(self):
        draft=copy.deepcopy(self.draft)
        draft['edits'][0]['selections'].append(dict(unit_id='C',first_word=0,last_word=0))
        draft['unused']=draft['unused'][:1]
        with self.assertRaisesRegex(ValueError,'C'):
            story_editorial.normalize_review(self.final,self.catalog,draft)
        draft['unused']=[]
        with self.assertRaises(ValueError):story_editorial.normalize_review(self.final,self.catalog,draft)

    def test_invalid_bounds_duplicate_unknown_and_used_rows_still_fail(self):
        for mutate in [lambda r:r['edits'][0]['selections'][0].update(last_word=5),lambda r:r['unused'].append(r['unused'][0]),lambda r:r['unused'][0].update(unit_id='Z'),lambda r:r['unused'][0].update(unit_id='A')]:
            raw=copy.deepcopy(self.final);mutate(raw)
            with self.assertRaises(ValueError):story_editorial.normalize_review(raw,self.catalog,self.draft)


if __name__=='__main__':unittest.main()
