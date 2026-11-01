import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import engine
import selection_bounds as bounds
import self_edit_workflow as workflow
import test_self_edit_workflow as fixtures
from planner import fingerprint


class BoundsTests(unittest.TestCase):
    def test_specific_bounds_and_no_silent_clamping(self):
        f=fixtures.SelfEditWorkflowTests();f.setUp()
        raw=copy.deepcopy(f.expanded);raw['edits'][0]['selections'][0]['last_word']=39
        bad=bounds.issues(raw,f.catalog)
        self.assertEqual(bad[0]['valid_last'],1)
        request=bounds.correction_request(dict(text=dict(format={})),raw,f.catalog,bad)
        self.assertNotIn('current_plan',json.loads(request['input']))
        answer={'e0_s0':dict(first_word=0,last_word=1,reason='เลือกวลีครบตามต้นฉบับ')}
        fixed=bounds.apply_corrections(raw,f.catalog,bad,answer)
        self.assertEqual(raw['edits'][0]['selections'][0]['last_word'],39)
        self.assertEqual(fixed['edits'][1],raw['edits'][1])
        answer['e0_s0']['last_word']=2
        with self.assertRaises(ValueError):bounds.apply_corrections(raw,f.catalog,bad,answer)

    def test_schema_has_per_unit_limits_and_compaction_preserves_source(self):
        f=fixtures.SelfEditWorkflowTests();f.setUp()
        schema=bounds.bounded_schema(workflow.edit_schema(),f.catalog)
        self.assertEqual(schema['$defs']['source_selection']['anyOf'][0]['properties']['last_word']['maximum'],1)
        self.assertEqual(schema['$defs']['source_selection']['anyOf'][1]['properties']['last_word']['maximum'],0)
        data=dict(units=[dict(phrases=['ไม่','ทิ้ง'])],current_plan=dict(edits=f.expanded['edits'],source_review=[dict(reason='repeated')]),assembled_scripts=[dict(assembled_text='ไม่ทิ้ง',selections=[dict(reason='repeated')])])
        compact=bounds.compact_data(data)
        self.assertEqual(compact['units'][0]['phrases'],['ไม่','ทิ้ง'])
        self.assertEqual(compact['assembled_scripts'][0]['assembled_text'],'ไม่ทิ้ง')
        self.assertNotIn('source_review',compact['current_plan'])
        self.assertIn('source_review',data['current_plan'])

    def test_bad_bounds_only_corrects_bad_selection_and_does_not_resend(self):
        f=fixtures.SelfEditWorkflowTests();f.setUp()
        broken=copy.deepcopy(f.expanded);broken['edits'][0]['selections'][0]['last_word']=39
        final=copy.deepcopy(f.expanded);final['span_audit']=[]
        # Simulate the reported completed full repair which still has an invalid index.
        answers=[(r,dict(usage={})) for r in [f.inv,f.draft,broken,{'e0_s0':dict(first_word=0,last_word=1,reason='ตรงกับวลีจริง')},final]]
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            with patch('story_editorial.plan_once',side_effect=answers) as send,patch('self_edit_workflow.resolve_plan',return_value=[]):
                cfg=dict(model='test',effort='medium',notes='')
                plan=workflow.run(f.catalog,folder,cfg,'secret',progress,{}, {})
                again=workflow.run(f.catalog,folder,cfg,'',progress,{}, {})
                self.assertEqual(plan,again);self.assertEqual(send.call_count,5)
                ledger=json.loads((folder/'api-usage.json').read_text('utf8'))
                self.assertEqual(ledger[3]['stage'],'self-recovery-bounds')
                self.assertLess(len(send.call_args_list[3].args[0]['input']),len(send.call_args_list[2].args[0]['input']))
                self.assertEqual(json.loads((folder/'self-recovery-response.json').read_text('utf8'))['response'],broken)

    def test_legacy_completed_repair_with_bad_bounds_resumes_without_rewriting_previous_stages(self):
        f=fixtures.SelfEditWorkflowTests();f.setUp()
        final=copy.deepcopy(f.expanded);final['span_audit']=[]
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);cfg=dict(model='test',effort='medium',notes='')
            progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            with patch('story_editorial.plan_once',side_effect=[(r,dict(usage={})) for r in [f.inv,f.draft,f.expanded,final]]) as send,patch('self_edit_workflow.resolve_plan',return_value=[]):
                expected=workflow.run(f.catalog,folder,cfg,'secret',progress,{}, {})
                request=copy.deepcopy(send.call_args_list[2].args[0])
            original=copy.deepcopy(f.expanded);original['edits'][0]['selections'][0]['last_word']=39
            repair=copy.deepcopy(original);repair['edits'][0]['selections'][0]['last_word']=37
            cached=json.loads((folder/'self-recovery-response.json').read_text('utf8'));cached['response']=original
            engine.save(folder/'self-recovery-response.json',cached)
            error='รหัสท่อนหรือขอบ phrase ไม่ถูกต้อง'
            data=json.loads(request['input']);data.update(candidate_response=original,validation_error=error)
            request['input']=json.dumps(data,ensure_ascii=False,separators=(',',':'))
            request['instructions']+='\nREPAIR KNOWN SAVED RESPONSE: correct the supplied validation error. Use the exact group/span IDs and computed selected/included facts. Preserve valid edits and give truthful specific reasons. Return the complete schema. Do not claim a missing phrase was selected.'
            engine.save(folder/'self-recovery-validation-error.json',dict(error=error,known_response_saved=True))
            engine.save(folder/'self-recovery-repair-response.json',dict(request_hash=fingerprint(request),response=repair))
            ledger=json.loads((folder/'api-usage.json').read_text('utf8'));ledger.append(dict(stage='self-recovery-repair',request_hash=fingerprint(request),status='completed',usage={}))
            engine.save(folder/'api-usage.json',ledger)
            with patch('story_editorial.plan_once',return_value=({'e0_s0':dict(first_word=0,last_word=1,reason='ตรงกับคำจริง')},dict(usage={}))) as send,patch('self_edit_workflow.resolve_plan',return_value=[]):
                actual=workflow.run(f.catalog,folder,cfg,'secret',progress,{}, {})
                again=workflow.run(f.catalog,folder,cfg,'',progress,{}, {})
                self.assertEqual(actual,expected);self.assertEqual(again,expected)
                self.assertEqual(send.call_count,1)
                self.assertEqual(len(json.loads((folder/'api-usage.json').read_text('utf8'))),6)
