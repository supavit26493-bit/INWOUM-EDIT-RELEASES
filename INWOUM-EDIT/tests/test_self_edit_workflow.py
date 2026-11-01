import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import engine
import self_edit_workflow as workflow


class SelfEditWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.catalog=[dict(id='A',start=0,end=2,text='เรื่องแรกเงื่อนไข',protected=False,words=[dict(start=0,end=1,text='เรื่องแรก'),dict(start=1,end=2,text='เงื่อนไข')]),dict(id='B',start=3,end=4,text='อีกเรื่อง',protected=False,words=[])]
        self.inv=dict(groups=[dict(id=g,title=g,question='คำถาม'+g,kind='focused',unit_ids=[u]) for g,u in [('g1','A'),('g2','B')]],units=[dict(unit_id=u,classification='usable',reason='สาระ'+u,opportunities=[dict(first_word=0,last_word=b,group_ids=[g],purpose='เรื่อง')]) for u,b,g in [('A',1,'g1'),('B',0,'g2')]],action_review=[])
        self.draft=dict(edits=[dict(name='เรื่องA',purpose='คำถาม',content_group='g1',distinct_body_summary='เนื้อหาA',selections=[dict(unit_id='A',first_word=0,last_word=0)])],unused=[dict(unit_id='B',decision='alternative',reason='รอใช้')])
        self.expanded=copy.deepcopy(self.draft);self.expanded['edits'][0]['selections'][0]['last_word']=1
        self.expanded['edits'].append(dict(name='เรื่องB',purpose='อีกคำถาม',content_group='g2',distinct_body_summary='เนื้อหาB',selections=[dict(unit_id='B',first_word=0,last_word=0)]));self.expanded['unused']=[]
        self.expanded['group_audit']=[dict(group_id=g,decision='included',reason='มีเรื่อง'+g) for g in ['g1','g2']]
        self.expanded['span_audit']=[dict(span_id=x,decision='included',reason='เลือกจริง') for x in ['A:1-1','B:0-0']]

    def test_omissions_include_inside_used_units(self):
        plan=workflow.normalize(self.draft,self.catalog,self.inv)
        self.assertEqual([r['span_id'] for r in workflow.omissions(plan,self.catalog)],['A:1-1','B:0-0'])

    def test_inventory_requires_every_unit_and_real_groups_and_bounds(self):
        self.assertEqual(workflow.validate_inventory(self.inv,self.catalog),self.inv)
        for mutate in [lambda r:r['units'].pop(),lambda r:r['units'][0]['opportunities'][0].update(last_word=8),lambda r:r['units'][0]['opportunities'][0].update(group_ids=['fake']),lambda r:r['groups'].append(r['groups'][0])]:
            raw=copy.deepcopy(self.inv);mutate(raw)
            with self.assertRaises(ValueError):workflow.validate_inventory(raw,self.catalog)

    def test_audit_cannot_claim_unselected_group_or_phrase_included(self):
        plan=workflow.normalize(self.draft,self.catalog,self.inv);spans=workflow.omissions(plan,self.catalog)
        with self.assertRaises(ValueError):workflow.validate_audit(self.expanded,plan,self.inv,spans)
        complete=workflow.normalize(self.expanded,self.catalog,self.inv)
        workflow.validate_audit(self.expanded,complete,self.inv,spans)
        raw=copy.deepcopy(self.expanded);raw['span_audit'].pop()
        with self.assertRaises(ValueError):workflow.validate_audit(raw,complete,self.inv,spans)

    def test_four_actual_stages_recovery_uses_local_omissions_and_resume_is_free(self):
        final=copy.deepcopy(self.expanded);final['span_audit']=[]
        answers=[(r,dict(usage={})) for r in [self.inv,self.draft,self.expanded,final]]
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            config=dict(model='test',effort='medium',notes='')
            with patch('story_editorial.plan_once',side_effect=answers) as send,patch('self_edit_workflow.resolve_plan',return_value=[]):
                plan=workflow.run(self.catalog,folder,config,'test-secret',progress,{}, {})
                again=workflow.run(self.catalog,folder,config,'',progress,{}, {})
                self.assertEqual(send.call_count,4)
                recovery=json.loads(send.call_args_list[2].args[0]['input'])
                self.assertEqual([r['span_id'] for r in recovery['omitted_spans']],['A:1-1','B:0-0'])
                self.assertEqual(len(plan['edits']),2);self.assertEqual(plan,again)
            self.assertTrue((folder/'final-audit.json').exists())
            for p in folder.glob('*.json'):self.assertNotIn('test-secret',p.read_text(encoding='utf8'))

    def test_reconcile_partial_inclusion_preserves_model_reason_and_edit_choices(self):
        raw=copy.deepcopy(self.expanded)
        raw['span_audit'][0].update(decision='unusable',reason='model judged the remainder redundant')
        spans=workflow.omissions(workflow.normalize(self.draft,self.catalog,self.inv),self.catalog)
        fixed,changes=workflow.reconcile_span_audit(raw,spans)
        self.assertEqual(fixed['edits'],raw['edits'])
        self.assertEqual(fixed['span_audit'][0]['decision'],'included')
        self.assertEqual(fixed['span_audit'][0]['model_reason'],'model judged the remainder redundant')
        self.assertEqual(changes[0]['selected_word_indices'],[1])
        workflow.validate_audit(fixed,workflow.normalize(fixed,self.catalog,self.inv),self.inv,spans)
