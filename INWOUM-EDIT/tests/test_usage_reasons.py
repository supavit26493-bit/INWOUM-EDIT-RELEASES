import unittest
from usage_reasons import explain


class UsageReasonsTests(unittest.TestCase):
    def setUp(self):
        self.unit=dict(id='A',start=0,end=3,text='เนื้อหา ราคา',words=[dict(start=0,end=1,text='เนื้อหา'),dict(start=2,end=3,text='ราคา')])

    def test_unused_preserves_specific_supplied_reason(self):
        cov=dict(units=[dict(unit_id='A',start=0,end=3,status='unused',review=dict(reason='เทคสำรอง ยังใช้ได้'))])
        self.assertIn('เทคสำรอง ยังใช้ได้',explain(cov,[self.unit],0,1))

    def test_partial_does_not_invent_rejection_reason(self):
        cov=dict(units=[dict(unit_id='A',start=0,end=3,status='partial',unselected_word_spans=[(1,2)])])
        self.assertIn('ราคา',explain(cov,[self.unit],2,3))
        self.assertIn('ไม่ได้ให้เหตุผลเฉพาะ',explain(cov,[self.unit],2,3))
        self.assertNotIn('ไม่เลือกวลี',explain(cov,[self.unit],0,1))

    def test_gap_does_not_claim_confirmed_silence(self):
        self.assertIn('ไม่ยืนยันว่าเงียบ',explain(dict(units=[]),[],0,1))
