import copy,tempfile,unittest,json
from pathlib import Path
import sales_reserve as reserve
import test_reserve_sales_clips
from test_sales_continuity import selection

class ReserveResumeTests(unittest.TestCase):
    def fixture(self):
        catalog,plan=test_reserve_sales_clips.ReserveSalesClipTests().fixture()
        plan['edits'][0]['map_id']='FULL'
        plan['edits'][0]['selections']=[selection('A',0,1),selection('B',0,1),selection('C',0,1)]
        plan['edits'][1]['map_id']='R1'
        return catalog,plan
    def test_complete_reordered_alternative_can_reuse_all_main_footage(self):
        catalog,plan=self.fixture();before=copy.deepcopy(plan)
        reserve.validate_plan(plan,catalog);audit=reserve.audit_plan(plan,catalog)
        self.assertFalse(audit['invalid']);self.assertIn('อีกลำดับ',audit['clips'][0]['note']);self.assertEqual(plan,before)
    def test_exact_copy_cannot_be_disguised_with_different_segmentation(self):
        catalog,plan=self.fixture()
        plan['edits'][0]['selections']=copy.deepcopy(plan['edits'][1]['selections'])
        plan['edits'][1]['selections']=[selection('C',0,0),selection('C',1,1),selection('B',0,1),selection('A',1,1)]
        with self.assertRaisesRegex(ValueError,'เหมือนคลิปหลัก'):reserve.validate_plan(plan,catalog)
    def test_optional_incomplete_reserve_is_saved_without_changing_main(self):
        catalog,plan=self.fixture();main=copy.deepcopy(plan['edits'][0]);before=copy.deepcopy(plan)
        plan['edits'][1]['reserve_sales_evidence']['payoff_quote']='ไม่มีประโยชน์นี้'
        audit=reserve.audit_plan(plan,catalog)
        with tempfile.TemporaryDirectory() as directory:
            result=reserve.hold_invalid(plan,audit,Path(directory))
            self.assertEqual(result['edits'],[main]);self.assertEqual(len(plan['edits']),2)
            saved=json.loads((Path(directory)/'pending-reserves.json').read_text('utf8'))
            self.assertEqual(saved['edits'][0]['selections'],before['edits'][1]['selections'])
            self.assertIn('ขาดประโยชน์',saved['edits'][0]['pending_reason']);self.assertTrue(saved['main_unchanged'])
            reserve.validate_plan(result,catalog)
    def test_duplicate_reserves_are_audited_together_and_only_duplicate_is_held(self):
        catalog,plan=self.fixture();second=copy.deepcopy(plan['edits'][1]);second['map_id']='R2';plan['edits'].append(second)
        audit=reserve.audit_plan(plan,catalog);self.assertEqual([r['map_id'] for r in audit['invalid']],['R2'])
        with tempfile.TemporaryDirectory() as directory:
            result=reserve.hold_invalid(plan,audit,Path(directory));self.assertEqual([e['map_id'] for e in result['edits']],['FULL','R1'])
            reserve.validate_plan(result,catalog)
    def test_valid_plan_does_not_need_hold_or_another_request(self):
        catalog,plan=self.fixture();audit=reserve.audit_plan(plan,catalog)
        self.assertIs(reserve.hold_invalid(plan,audit,None),plan);self.assertEqual(audit['extra_api_calls'],0)
