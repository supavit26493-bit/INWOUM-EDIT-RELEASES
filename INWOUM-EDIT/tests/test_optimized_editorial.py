import copy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import engine
import optimized_editorial as quick
import test_full_contract as fixtures
from planner import fingerprint


class OptimizedTests(unittest.TestCase):
    def test_openers_and_full_repetition_across_cuts_are_checked_without_mutation(self):
        catalog=[dict(id='X',words=[dict(text=t) for t in ['แต่','กระเป๋า','ซ่อน','ลวด','ซ่อน','แบบจำลอง']])]
        plan=dict(edits=[dict(edit_role='FULL',selections=[dict(unit_id='X',first_word=0,last_word=2),dict(unit_id='X',first_word=4,last_word=4)])])
        before=copy.deepcopy(plan)
        findings=quick.speech_findings(plan,catalog)
        self.assertEqual([r['issue'] for r in findings],['forbidden_opener','consecutive_repeat'])
        self.assertEqual(plan,before)
        with self.assertRaises(ValueError):quick.validate_speech(plan,catalog)
        plan['edits'][0]['selections']=[dict(unit_id='X',first_word=5,last_word=5)]
        quick.validate_speech(plan,catalog) # No naive prefix rejection of แบบจำลอง.
        for text in ['แต่','แต่ว่า','ก็','แบบ']:
            catalog[0]['words'][5]['text']=text
            with self.assertRaises(ValueError):quick.validate_speech(plan,catalog)

    def test_join_checks_find_abandoned_verb_and_composite_clause_tail(self):
        catalog=[dict(id='X',words=[dict(text=t) for t in ['พาสปอร์ต','ใส่','เอามา','ใส่','มือถือ','เนี้ยแต่ว่าราคาก็','เข้าถึงได้']])]
        plan=dict(edits=[dict(edit_role='focused',selections=[dict(unit_id='X',first_word=0,last_word=1),dict(unit_id='X',first_word=2,last_word=4)]),
            dict(edit_role='focused',selections=[dict(unit_id='X',first_word=5,last_word=6)])])
        self.assertEqual([r['issue'] for r in quick.speech_findings(plan,catalog)],['abandoned_verb_at_join','carried_clause_tail'])
        catalog[0]['words'][1]['text']='นะใส่'
        self.assertEqual([r['issue'] for r in quick.speech_findings(plan,catalog)],['abandoned_verb_at_join','carried_clause_tail'])

    def test_speech_patch_is_model_authored_bounded_and_preserves_other_cuts(self):
        catalog=[dict(id='X',words=[dict(text=t) for t in ['ใบ','เล็ก','เล็ก','ครับ']])]
        plan=dict(edits=[dict(name='FULL',edit_role='FULL',selections=[dict(unit_id='X',first_word=0,last_word=3)])])
        targets,schema,data=quick.speech_patch_request(plan,catalog,quick.speech_findings(plan,catalog))
        fixed=quick.apply_speech_patch(plan,targets,dict(e0_s0=dict(ranges=[dict(first_word=0,last_word=1),dict(first_word=3,last_word=3)],reason='เอาคำเล็กซ้ำออก')))
        quick.validate_speech(fixed,catalog)
        self.assertEqual(plan['edits'][0]['selections'][0]['last_word'],3)
        for ranges in [[dict(first_word=0,last_word=4)],[dict(first_word=2,last_word=3),dict(first_word=0,last_word=1)]]:
            with self.assertRaises(ValueError):quick.apply_speech_patch(plan,targets,dict(e0_s0=dict(ranges=ranges,reason='ผิดขอบ')))

    def fixture(self):
        base=fixtures.FullContractTests();f=base.fixture();f.inv['protected_names']=[]
        review=dict(replacements=[],additions=[],output_order=['C001','C002'],unused_updates=[],
            candidate_review=[dict(candidate_id=f'C{i+1:03d}',decision='keep',target_edit_index=i,reason='เรื่องครบต่างหน้าที่') for i in range(2)],
            release_review=[dict(edit_index=i,standalone_reason='เรื่องครบ',distinct_reason='ต่างหน้าที่',short_clip_reason='',boundary_review='ขอบคำครบ',claims_review='คงเงื่อนไข') for i in range(2)])
        return base,f,review

    def test_three_calls_full_contract_and_unchanged_edits_do_not_need_reprinting(self):
        base,f,review=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            cfg=dict(model='test',effort='medium',editorial_workflow='self-edit-v5',editorial_request_version='sparse-review-v2')
            with patch('story_editorial.plan_once',side_effect=[(x,dict(usage={})) for x in [f.inv,f.expanded,review]]) as send,patch('optimized_editorial.resolve_plan',side_effect=base.cuts):
                result=quick.run(f.catalog,folder,cfg,'secret',progress,{}, {})
                again=quick.run(f.catalog,folder,cfg,'',progress,{}, {})
                self.assertEqual(result,again);self.assertEqual(send.call_count,3)
                self.assertEqual(send.call_args_list[0].args[0]['reasoning']['effort'],'medium')
                self.assertEqual(send.call_args_list[1].args[0]['reasoning']['effort'],'medium')
                self.assertEqual(result['edits'][0]['edit_role'],'FULL')
                self.assertEqual(len(result['edits']),2)
                self.assertNotIn('edits',send.call_args_list[2].args[0]['text']['format']['schema']['properties'])
                schema=send.call_args_list[1].args[0]['text']['format']['schema']
                self.assertEqual(schema['properties']['edits']['items']['properties']['content_group']['enum'],['g1','g2'])
                names=send.call_args_list[0].args[0]['text']['format']['schema']['properties']['protected_names']['items']
                self.assertEqual(names['required'],['text'])
                # A malformed historical repair must not force a new paid call
                # when the original saved inventory now passes source binding.
                fix_request={'cached':'known-repair'}
                broken=copy.deepcopy(f.inv);broken['units']=[]
                engine.save(folder/'quick-inventory-validation-error.json',dict(error='old name binding',repair_request=fix_request))
                engine.save(folder/'quick-inventory-repair-response.json',dict(request_hash=fingerprint(fix_request),response=broken))
                recovered=quick.run(f.catalog,folder,cfg,'',progress,{}, {})
                self.assertEqual(recovered,result);self.assertEqual(send.call_count,3)
                self.assertTrue((folder/'quick-inventory-accepted-original.json').exists())
            self.assertTrue((folder/'FULL-COVERAGE.md').exists())
            for p in folder.glob('*.json'):self.assertNotIn('secret',p.read_text('utf8'))

    def test_v4_loads_user_rules_and_exports_speech_gate_report(self):
        base,f,review=self.fixture()
        anchors=dict(anchors=[dict(id=r['id'],reason='เก็บสาระเดิม ขอบคำครบ',options=r['options']) for r in f.inv['full_requirements'] if r['scope']!='hold'])
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            cfg=dict(model='test',effort='medium',editorial_workflow='self-edit-v5',editorial_request_version='sparse-review-v4')
            with patch('story_editorial.plan_once',side_effect=[(x,dict(usage={})) for x in [f.inv,anchors,f.expanded,review]]) as send,patch('optimized_editorial.resolve_plan',side_effect=base.cuts):
                quick.run(f.catalog,folder,cfg,'secret',progress,{}, {})
                quick.run(f.catalog,folder,cfg,'',progress,{}, {})
                self.assertEqual(send.call_count,4)
                self.assertEqual(json.loads((folder/'fact-anchor-order.json').read_text('utf8'))['order'],'before-draft')
                self.assertIn('NEVER start with แต่',send.call_args_list[0].args[0]['instructions'])
                self.assertIn('PRIMARY inventory group',send.call_args_list[3].args[0]['instructions'])
                self.assertEqual(json.loads((folder/'speech-quality.json').read_text('utf8'))['findings'],[])

    def test_fact_anchor_patch_cannot_delete_or_demote_facts(self):
        base,f,_=self.fixture();before=copy.deepcopy(f.inv)
        rows=[dict(id=r['id'],reason='เก็บความหมายเดิม',options=r['options']) for r in f.inv['full_requirements'] if r['scope']!='hold']
        refined=quick.apply_fact_anchors(dict(anchors=rows),f.inv,f.catalog)
        self.assertEqual(refined,f.inv);self.assertEqual(f.inv,before)
        with self.assertRaises(ValueError):quick.apply_fact_anchors(dict(anchors=rows[:-1]),f.inv,f.catalog)
        with self.assertRaises(ValueError):quick.apply_fact_anchors(dict(anchors=rows+[rows[0]]),f.inv,f.catalog)

    def test_sparse_order_cannot_lose_or_falsely_keep_candidates(self):
        base,f,raw=self.fixture()
        previous=quick.legacy.normalize(f.expanded,f.catalog,f.inv);before=copy.deepcopy(previous)
        result=quick.apply_review(raw,previous,f.inv)
        self.assertEqual(result['edits'],previous['edits']);self.assertEqual(previous,before)
        for mutate in [lambda r:r['output_order'].append('C001'),lambda r:r['output_order'].pop(),lambda r:r['replacements'].append(dict(candidate_id='C099',edit=f.expanded['edits'][0]))]:
            bad=copy.deepcopy(raw);mutate(bad)
            with self.assertRaises(ValueError):quick.apply_review(bad,previous,f.inv)

    def test_inventory_range_error_uses_tiny_patch_and_resumes_without_new_calls(self):
        base,f,review=self.fixture();bad=copy.deepcopy(f.inv)
        bad['units'][0]['opportunities'][0]['last_word']=2
        fix={'e0_s0':dict(first_word=0,last_word=1,reason='จบที่วลีจริง')}
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            cfg=dict(model='test',effort='medium',editorial_workflow='self-edit-v5',editorial_request_version='sparse-review-v3')
            with patch('story_editorial.plan_once',side_effect=[(x,dict(usage={})) for x in [bad,fix,f.expanded,review]]) as send,patch('optimized_editorial.resolve_plan',side_effect=base.cuts):
                result=quick.run(f.catalog,folder,cfg,'secret',progress,{}, {})
                again=quick.run(f.catalog,folder,cfg,'',progress,{}, {})
                self.assertEqual(result,again);self.assertEqual(send.call_count,4)
                self.assertEqual(send.call_args_list[0].args[0]['reasoning']['effort'],'low')
                self.assertNotIn('units',json.loads(send.call_args_list[1].args[0]['input']))
                self.assertTrue((folder/'quick-inventory-bounds-applied.json').exists())

    def test_name_boundaries_and_unknown_or_invented_names_fail_closed(self):
        catalog=[dict(id='X',words=[dict(text=t) for t in ['จาก','แพ็ค','เซฟ','ครับ']])]
        inv=dict(protected_names=[dict(text='แพ็คเซฟ',occurrences=[dict(unit_id='X',first_word=1,last_word=2)])])
        good=dict(edits=[dict(selections=[dict(unit_id='X',first_word=0,last_word=2)])])
        quick.validate_names(inv,catalog,good)
        bad=copy.deepcopy(good);bad['edits'][0]['selections'][0]['last_word']=1
        with self.assertRaisesRegex(ValueError,'ตัดชื่อเฉพาะ'):quick.validate_names(inv,catalog,bad)
        inv['protected_names'][0]['text']='ชื่อที่ไม่ได้พูด'
        with self.assertRaises(ValueError):quick.validate_names(inv,catalog,good)

    def test_name_occurrences_come_from_source_not_model_confusion_with_backpacker(self):
        catalog=[dict(id='X',words=[dict(text=t) for t in ['แพ็ค','เซฟ','แบ็ค','แพ็ค','เกอร์','แพ็ค','เซฟ']])]
        raw=dict(protected_names=[dict(text='แพ็ค เซฟ',occurrences=[dict(unit_id='X',first_word=3,last_word=4)])])
        result=quick.bind_names(raw,catalog)
        self.assertEqual(result['protected_names'][0]['occurrences'],[dict(unit_id='X',first_word=0,last_word=1),dict(unit_id='X',first_word=5,last_word=6)])
        self.assertEqual(raw['protected_names'][0]['occurrences'][0]['first_word'],3)

    def test_profanity_source_mask_rejects_leftover_half_word_and_schema_cannot_select_it(self):
        catalog=[dict(id='X',words=[dict(text=t) for t in ['โรงแรม','ไอ้','เหี้ย','กระเป๋า','โล่ง']])]
        bad=dict(edits=[dict(selections=[dict(unit_id='X',first_word=0,last_word=1),dict(unit_id='X',first_word=4,last_word=4)])])
        with self.assertRaisesRegex(ValueError,'เศษคำสบถ'):quick.validate_forbidden_fragments(bad,catalog)
        good=copy.deepcopy(bad);good['edits'][0]['selections'][0]['last_word']=0;good['edits'][0]['selections'][1]['first_word']=3
        quick.validate_forbidden_fragments(good,catalog)
        req=dict(text=dict(format=dict(schema={'$defs':{}})))
        schema=quick.exclude_forbidden_schema(req,catalog)['text']['format']['schema']['$defs']['source_selection']['anyOf']
        limits=[(s['properties']['first_word']['minimum'],s['properties']['last_word']['maximum']) for s in schema]
        self.assertEqual(limits,[(0,0),(3,4)])
        self.assertEqual(quick.forbidden_words([dict(id='whole',words=[],text='ไอ้เหี้ย')]),{'whole':{0}})
