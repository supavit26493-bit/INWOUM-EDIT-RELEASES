import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import engine
import full_contract as contract
import self_edit_workflow as workflow
import test_self_edit_workflow as fixtures


class FullContractTests(unittest.TestCase):
    def fixture(self):
        f=fixtures.SelfEditWorkflowTests();f.setUp()
        f.inv['full_requirements']=[dict(id='identity',topic='ระบุสินค้า',function='identity',scope='full',reason='ให้รู้ว่าคืออะไร',options=[dict(selections=[dict(unit_id='A',first_word=0,last_word=0)])]),dict(id='detail',topic='รายละเอียดเพิ่ม',function='condition',scope='collection',reason='ข้อมูลใหม่ไม่ควรหาย',options=[dict(selections=[dict(unit_id='B',first_word=0,last_word=0)])])]
        for raw in (f.draft,f.expanded):
            for i,e in enumerate(raw['edits']):e['edit_role']='FULL' if i==0 else 'focused'
        return f

    def cuts(self,plan,*args):
        return [dict(ranges=[dict(start=0,end=20)]) for e in plan['edits']]

    def test_full_required_first_real_coverage_and_180_second_limit(self):
        f=self.fixture();plan=workflow.normalize(f.expanded,f.catalog,f.inv)
        self.assertEqual(contract.validate(plan,f.inv,f.catalog,self.cuts(plan),True)['seconds'],20)
        for mutate in [lambda p:p['edits'][0].update(edit_role='focused'),lambda p:p['edits'][0]['selections'][0].update(first_word=1,last_word=1)]:
            bad=copy.deepcopy(plan);mutate(bad)
            with self.assertRaises(ValueError):contract.validate(bad,f.inv,f.catalog,self.cuts(bad),True)
        cuts=self.cuts(plan);cuts[0]['ranges'][0]['end']=180.02
        with self.assertRaises(ValueError):contract.validate(plan,f.inv,f.catalog,cuts,True)
        cuts[0]['ranges'][0]['end']=180
        contract.validate(plan,f.inv,f.catalog,cuts,True)

    def test_reviewed_unassigned_fragment_can_be_kept_with_reason_but_unknown_group_cannot(self):
        f=self.fixture();raw=copy.deepcopy(f.inv)
        raw['units'][0]['opportunities'][0]['group_ids']=[]
        workflow.validate_inventory(raw,f.catalog,True,True)
        with self.assertRaises(ValueError):workflow.validate_inventory(raw,f.catalog,True,False)
        raw['units'][0]['opportunities'][0]['group_ids']=['not-real']
        with self.assertRaises(ValueError):workflow.validate_inventory(raw,f.catalog,True,True)

    def test_anchor_does_not_require_hesitation_or_next_clause_head_but_keeps_negation(self):
        units={'A':dict(words=[dict(text=x) for x in ['อืม','ไม่','เป่า','มัน']])}
        option=dict(selections=[dict(unit_id='A',first_word=0,last_word=3)])
        self.assertEqual(contract.option_words(option,units),{('A',1),('A',2)})
        self.assertFalse(contract.option_words(option,units)<={('A',2)})
        units['A']['words']=[dict(text=x) for x in ['ไม่','เป่า','นะครับ','เพราะว่ามัน']]
        self.assertEqual(contract.option_words(option,units),{('A',0),('A',1)})

    def test_dangling_modal_for_following_location_clause_is_not_part_of_previous_fact(self):
        words=['ใบ','เล็ก','ไม่','เยอะ','ก็','อาจจะ','แบบ','อยู่','นี่','คาดเอว']
        units={'A':dict(words=[dict(text=w) for w in words])}
        option=dict(selections=[dict(unit_id='A',first_word=0,last_word=5)])
        self.assertEqual(contract.option_words(option,units),{('A',i) for i in range(4)})
        # A real uncertainty about a property must still be present, including
        # when the modal is truncated by the inventory's mistaken anchor.
        units['A']['words'][6]['text']='กันน้ำ'
        self.assertIn(('A',5),contract.option_words(option,units))
        units['A']['words']=[dict(text=w) for w in ['อาจจะ','กันน้ำ','ไม่','แน่ใจ']]
        option['selections'][0]['last_word']=3
        self.assertEqual(len(contract.option_words(option,units)),4)

    def test_collection_detail_cannot_vanish_or_be_split_from_condition(self):
        f=self.fixture();plan=workflow.normalize(f.draft,f.catalog,f.inv)
        contract.validate(plan,f.inv,f.catalog,self.cuts(plan),False)
        with self.assertRaises(ValueError):contract.validate(plan,f.inv,f.catalog,self.cuts(plan),True)
        plan=workflow.normalize(f.expanded,f.catalog,f.inv)
        f.inv['full_requirements'][1]['options'][0]['selections'].append(dict(unit_id='A',first_word=0,last_word=0))
        with self.assertRaises(ValueError):contract.validate(plan,f.inv,f.catalog,self.cuts(plan),True)

    def test_full_must_keep_protected_demonstration(self):
        f=self.fixture();plan=workflow.normalize(f.expanded,f.catalog,f.inv)
        f.catalog.append(dict(id='P',protected=True,words=[]))
        with self.assertRaises(ValueError):contract.validate(plan,f.inv,f.catalog,self.cuts(plan),True)
        plan['edits'][0]['selections'].append(dict(unit_id='P',first_word=0,last_word=0))
        contract.validate(plan,f.inv,f.catalog,self.cuts(plan),True)

    def test_merge_must_retain_distinct_source_not_only_common_body(self):
        previous=dict(edits=[dict(edit_role='FULL',selections=[dict(unit_id='A',first_word=0,last_word=1)]),dict(edit_role='focused',selections=[dict(unit_id='A',first_word=0,last_word=1),dict(unit_id='B',first_word=0,last_word=2)])])
        plan=dict(edits=[copy.deepcopy(previous['edits'][0])])
        raw=dict(candidate_review=[dict(candidate_id='C002',decision='merge',target_edit_index=0)])
        with self.assertRaises(ValueError):contract.validate_merges(raw,previous,plan)
        plan['edits'][0]['selections'].append(dict(unit_id='B',first_word=0,last_word=2))
        contract.validate_merges(raw,previous,plan)

    def test_v3_four_stage_program_keeps_full_and_reports_source_contract(self):
        f=self.fixture();final=copy.deepcopy(f.expanded);final['span_audit']=[]
        final['candidate_review']=[dict(candidate_id=f'C{i+1:03d}',decision='keep',target_edit_index=i,reason='หน้าที่ต่างกัน') for i in range(2)]
        final['release_review']=[dict(edit_index=i,standalone_reason='เรื่องครบ',distinct_reason='หน้าที่ต่างกัน',short_clip_reason='',boundary_review='จบความ',claims_review='คงเงื่อนไข') for i in range(2)]
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            bad=copy.deepcopy(f.draft);bad['edits'][0]['selections'][0].update(first_word=1,last_word=1)
            with patch('story_editorial.plan_once',side_effect=[(x,dict(usage={})) for x in [f.inv,bad,f.draft,f.expanded,final,final]]) as send,patch('self_edit_workflow.resolve_plan',side_effect=self.cuts):
                plan=workflow.run(f.catalog,folder,dict(model='test',editorial_workflow='self-edit-v3'),'secret',progress,{}, {})
                self.assertEqual(send.call_count,5)
                self.assertEqual(plan['edits'][0]['edit_role'],'FULL')
                self.assertIn('full_requirements',json.loads(send.call_args_list[-1].args[0]['input'])['inventory'])
                saved_report=json.loads((folder/'full-coverage.json').read_text(encoding='utf8'))
                with patch('full_contract.validate',return_value=saved_report):
                    again=workflow.run(f.catalog,folder,dict(model='test',editorial_workflow='self-edit-v3'),'',progress,{}, {})
                self.assertEqual(plan,again)
                self.assertEqual(send.call_count,5)
                polished=workflow.run(f.catalog,folder,dict(model='test',editorial_workflow='self-edit-v4'),'secret',progress,{}, {})
                self.assertEqual(send.call_count,6)
                self.assertEqual(polished['edits'],plan['edits'])
                self.assertTrue((folder/'polish-plan.json').exists())
                self.assertIn('FINAL POLISH',send.call_args_list[-1].args[0]['instructions'])
            self.assertTrue((folder/'FULL-COVERAGE.md').exists())
            self.assertTrue(all(r['in_collection'] for r in json.loads((folder/'full-coverage.json').read_text(encoding='utf8'))['requirements']))
