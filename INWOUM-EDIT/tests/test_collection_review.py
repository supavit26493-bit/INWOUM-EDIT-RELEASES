import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import collection_review as review
import self_edit_workflow as workflow
import engine
import test_self_edit_workflow as fixtures


class CollectionReviewTests(unittest.TestCase):
    def fixture(self):
        f=fixtures.SelfEditWorkflowTests();f.setUp();return f

    def resolved(self,plan,*args):
        return [dict(ranges=[dict(start=0,end=8)]) for e in plan['edits']]

    def test_source_overlap_and_short_duration_are_evidence_not_deletion(self):
        f=self.fixture();plan=workflow.normalize(f.expanded,f.catalog,f.inv)
        data=review.context(plan,f.catalog,self.resolved(plan))
        self.assertEqual(len(data['candidates']),2)
        self.assertEqual(data['candidates'][0]['seconds'],8)
        self.assertEqual(data['source_overlap'],[])
        plan['edits'].append(copy.deepcopy(plan['edits'][0]))
        data=review.context(plan,f.catalog,self.resolved(plan))
        self.assertEqual(data['source_overlap'][0]['a_source_overlap'],1)

    def test_inventory_recovers_only_existing_model_reason_not_fabricated_exclusion(self):
        f=self.fixture();raw=copy.deepcopy(f.inv);raw['units'][0]['reason']=''
        fixed=workflow.validate_inventory(raw,f.catalog,True)
        self.assertEqual(fixed['units'][0]['reason'],'เรื่อง')
        self.assertEqual(fixed['units'][0]['reason_source'],'model_opportunity_purpose_recovered')
        self.assertEqual(raw['units'][0]['reason'],'')
        raw['units'][0]['classification']='production';raw['units'][0]['opportunities']=[]
        with self.assertRaises(ValueError):workflow.validate_inventory(raw,f.catalog,True)

    def test_used_status_is_computed_without_changing_edits_or_discarding_evidence(self):
        f=self.fixture();raw=copy.deepcopy(f.expanded)
        raw['unused']=[dict(unit_id='A',decision='alternative',reason='เก็บเหตุผลเดิมไว้')]
        fixed,displaced=workflow.reconcile_used_rows(raw)
        self.assertEqual(fixed['edits'],raw['edits'])
        self.assertEqual(fixed['unused'],[])
        self.assertEqual(displaced,raw['unused'])
        self.assertEqual(len(raw['unused']),1)

    def test_short_rationale_reuses_both_existing_model_judgments_and_preserves_raw(self):
        raw=dict(release_review=[dict(edit_index=0,short_clip_reason='',standalone_reason='ตอบคำถามครบ',distinct_reason='มุมใช้งานต่าง')])
        fixed,changes=review.recover_short_reasons(raw,[dict(ranges=[dict(start=0,end=7)])])
        self.assertEqual(raw['release_review'][0]['short_clip_reason'],'')
        self.assertEqual(fixed['release_review'][0]['short_clip_reason'],'ตอบคำถามครบ; มุมใช้งานต่าง')
        self.assertEqual(changes[0]['source'],'model_standalone_and_distinct_recovered')
        raw['release_review'][0]['distinct_reason']=''
        self.assertEqual(review.recover_short_reasons(raw,[dict(ranges=[dict(start=0,end=7)])])[1],[])

    def test_four_calls_publish_only_selected_collection_and_archive_alternative(self):
        f=self.fixture();final=copy.deepcopy(f.expanded)
        final['edits']=final['edits'][:1]
        final['unused']=[dict(unit_id='B',decision='alternative',reason='เทคสำรอง')]
        final['span_audit']=[]
        final['group_audit'][1].update(decision='hold',reason='พักเป็นทางเลือก')
        final['candidate_review']=[dict(candidate_id='C001',decision='keep',target_edit_index=0,reason='เรื่องหลัก'),dict(candidate_id='C002',decision='alternate',target_edit_index=0,reason='เทคสำรอง')]
        final['release_review']=[dict(edit_index=0,standalone_reason='เรื่องครบ',distinct_reason='ตอบโจทย์หลัก',short_clip_reason='สั้นแต่จบความ',boundary_review='ขอบประโยคครบ',claims_review='คงเงื่อนไข')]
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            with patch('story_editorial.plan_once',side_effect=[(x,dict(usage={})) for x in [f.inv,f.draft,f.expanded,final]]) as send,patch('self_edit_workflow.resolve_plan',side_effect=self.resolved):
                plan=workflow.run(f.catalog,folder,dict(model='test',editorial_workflow='self-edit-v2'),'secret',progress,{}, {})
                self.assertEqual(send.call_count,4)
                self.assertEqual(len(plan['edits']),1)
                payload=send.call_args_list[-1].args[0]
                self.assertIn('FINAL COLLECTION SELECTION',payload['instructions'])
                self.assertEqual(len(json.loads(payload['input'])['collection_evidence']['candidates']),2)
            self.assertEqual(len(json.loads((folder/'alternate-takes.json').read_text(encoding='utf8'))),1)
            self.assertTrue((folder/'COLLECTION-REVIEW.md').exists())
            bad=copy.deepcopy(final);bad['release_review'][0]['short_clip_reason']=''
            with self.assertRaises(ValueError):review.validate(bad,plan,dict(candidates=[dict(candidate_id='C001'),dict(candidate_id='C002')]),self.resolved(plan))
            bad=copy.deepcopy(final);bad['candidate_review'].pop()
            with self.assertRaises(ValueError):review.validate(bad,plan,dict(candidates=[dict(candidate_id='C001'),dict(candidate_id='C002')]),self.resolved(plan))
