import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import engine
import parallel_requests as api
import parallel_editorial as parallel
import test_full_contract as fixtures


class ParallelTests(unittest.TestCase):
    def test_json_key_order_reuses_saved_exact_payload_but_changed_meaning_does_not(self):
        with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.plan_once',return_value=(dict(ok=True),dict(usage={}))) as post:
            folder=Path(tmp)
            api.call(dict(model='gpt-6-luna',input='{"b":2,"a":1}'),folder,'secret','order')
            api.call(dict(model='gpt-6-luna',input='{"a":1,"b":2}'),folder,'','order')
            self.assertEqual(post.call_count,1)
            self.assertTrue((folder/'order-cache-equivalence.json').exists())
            with self.assertRaises(ValueError):api.call(dict(model='gpt-6-luna',input='{"a":3,"b":2}'),folder,'secret','order')
            self.assertEqual(post.call_count,1)

    def fixture(self):
        base=fixtures.FullContractTests();f=base.fixture()
        facts=copy.deepcopy(f.inv['full_requirements'])
        for fact in facts:fact['group_id']=f.inv['groups'][0]['id']
        raw=dict(groups=f.inv['groups'],facts=facts,protected_names=[],action_review=[],unit_audit=[])
        for u in f.catalog:
            ids=[r['id'] for r in facts if any(s['unit_id']==u['id'] for o in r['options'] for s in o['selections'])]
            raw['unit_audit'].append(dict(unit_id=u['id'],classification='usable' if ids else 'uncertain',fact_ids=ids,reason='มีสาระ' if ids else 'วลีที่ยังไม่จบ'))
        return base,f,raw

    def test_parallel_reservations_preserve_both_results_and_never_resend_cached_or_unknown(self):
        barrier=threading.Barrier(2)
        def send(payload,key):
            barrier.wait(3)
            return dict(value=payload['input']),dict(usage={})
        with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.plan_once',side_effect=send) as post:
            folder=Path(tmp)
            with ThreadPoolExecutor(2) as pool:
                a=pool.submit(api.call,dict(model='gpt-6-luna',input='A'),folder,'secret','a')
                b=pool.submit(api.call,dict(model='gpt-6-luna',input='B'),folder,'secret','b')
                self.assertEqual(a.result(),dict(value='A'));self.assertEqual(b.result(),dict(value='B'))
            rows=json.loads((folder/'api-usage.json').read_text('utf8'))
            self.assertEqual(len(rows),2);self.assertTrue(all(r['status']=='completed' for r in rows))
            api.call(dict(model='gpt-6-luna',input='A'),folder,'','a');self.assertEqual(post.call_count,2)
            from planner import fingerprint
            rows.append(dict(stage='unknown',request_hash=fingerprint(dict(model='gpt-6-luna',input='C')),status='sent'))
            engine.save(folder/'api-usage.json',rows)
            with self.assertRaisesRegex(ValueError,'ไม่ทราบผล'):api.call(dict(model='gpt-6-luna',input='C'),folder,'secret','unknown')
            self.assertEqual(post.call_count,2)
            for f in folder.glob('*.json'):self.assertNotIn('secret',f.read_text('utf8'))

    def test_fact_links_require_real_source_and_every_usable_unit_is_accounted(self):
        base,f,raw=self.fixture();inv=parallel.make_inventory(raw,f.catalog)
        self.assertEqual(len(inv['units']),len(f.catalog))
        bad=copy.deepcopy(raw);bad['unit_audit'][0]['fact_ids']=[]
        with self.assertRaises(ValueError):parallel.make_inventory(bad,f.catalog)
        bad=copy.deepcopy(raw);bad['facts'][0]['options']=[]
        with self.assertRaises(ValueError):parallel.make_inventory(bad,f.catalog)
        linked=copy.deepcopy(raw);linked['unit_audit'][0]['fact_ids']=['detail']
        report=parallel.make_inventory(linked,f.catalog)['units'][0]['fact_links'][0]
        self.assertFalse(report['literal_option_from_this_unit'])
        self.assertEqual(report['basis'],'model_semantic_reference_not_literal_evidence')

    def test_disjoint_merge_cannot_replace_full_or_drop_and_replace_same_story(self):
        base,f,raw=self.fixture();p=f.expanded['edits'];before=copy.deepcopy(p)
        focused=dict(replacements=[],drops=[],additions=[])
        self.assertEqual(parallel.merge_focused(p,focused),p[1:]);self.assertEqual(p,before)
        focused['replacements']=[dict(edit_index=0,edit=p[0])]
        with self.assertRaises(ValueError):parallel.merge_focused(p,focused)
        focused.update(replacements=[dict(edit_index=1,edit=p[1])],drops=[dict(edit_index=1,reason='ซ้ำ')])
        with self.assertRaises(ValueError):parallel.merge_focused(p,focused)

    def test_three_requests_real_coverage_parallel_start_and_cache_resume(self):
        base,f,raw=self.fixture();barrier=threading.Barrier(2)
        def send(payload,key):
            schema=payload['text']['format']['schema']['properties']
            if 'facts' in schema:answer=raw
            elif 'equivalent_facts' in schema:answer=dict(equivalent_facts=[],remove_selection_indices=[],selection_replacements=[],review_note='บทครบ ไม่ซ้ำ')
            elif 'fact_review' in schema:
                rows=[];scripts=json.loads(payload['input'])['actual_selected_scripts']
                for fact in raw['facts']:
                    span=fact['options'][0]['selections'][0]
                    i,j=next((s['clip_index'],r['selection_index']) for s in scripts for r in s['selections'] if r['unit_id']==span['unit_id'] and r['first_word']<=span['first_word']<=span['last_word']<=r['last_word'])
                    rows.append(dict(fact_id=fact['id'],status='covered',clip_index=i,selection_indices=[j],reason='วลีนี้กล่าวสาระครบ'))
                answer=dict(fact_review=rows)
            else:
                barrier.wait(3)
                if 'edit' in schema:answer=dict(edit=f.expanded['edits'][0],review_note='ครบFULL')
                else:answer=dict(replacements=[],drops=[],additions=[] if json.loads(payload['input'])['existing_edits'] else f.expanded['edits'][1:],review_note='เรื่องย่อยครบ')
            return answer,dict(usage={})
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'first';folder.mkdir();progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            cfg=dict(model='test',effort='medium')
            with patch('parallel_requests.plan_once',side_effect=send) as post,patch('parallel_editorial.resolve_plan',side_effect=base.cuts):
                first=parallel.run(f.catalog,folder,cfg,'secret',progress,{}, {})
                again=parallel.run(f.catalog,folder,cfg,'',progress,{}, {})
                self.assertEqual(first,again);self.assertEqual(post.call_count,5)
                engine.save(folder/'plan.json',first);engine.save(folder/'catalog.json',f.catalog)
                for name in ('source-sha256.json','audio-identity.json'):engine.save(folder/name,{})
                for child,notes,expected in [('same','',9),('changed','เงื่อนไขงานใหม่',14)]:
                    second=Path(tmp)/child;second.mkdir()
                    for name in ('source-sha256.json','audio-identity.json'):engine.save(second/name,{})
                    cfg2=dict(cfg,editorial_seed_job='first',notes=notes)
                    p2=engine.Progress(second,lambda **kw:None,lambda:False)
                    parallel.run(f.catalog,second,cfg2,'secret',p2,{}, {})
                    self.assertEqual(post.call_count,expected)
                    self.assertEqual((second/'fact-ledger-reused.json').exists(),child=='same')
            report=json.loads((folder/'fact-coverage.json').read_text('utf8'))['facts']
            self.assertTrue(all(r['edit_indices'] for r in report))
            self.assertTrue((folder/'FULL-COVERAGE.md').exists())

    def test_full_polish_preserves_fact_ids_and_rejects_unsafe_aliases_and_overlaps(self):
        base,f,raw=self.fixture();inv=parallel.make_inventory(raw,f.catalog);edit=f.expanded['edits'][0]
        answer=dict(equivalent_facts=[],remove_selection_indices=[],selection_replacements=[],review_note='ครบ')
        out,edited=parallel.apply_full_polish(answer,inv,edit,f.catalog)
        self.assertEqual(out,inv);self.assertEqual(edited,edit)
        bad=dict(answer,equivalent_facts=[dict(fact_ids=['identity','detail'],reason='ไม่ใช่สาระเดียวกัน')])
        with self.assertRaises(ValueError):parallel.apply_full_polish(bad,inv,edit,f.catalog)
        bad=dict(answer,remove_selection_indices=[0],selection_replacements=[dict(selection_index=0,ranges=[dict(first_word=0,last_word=0)])])
        with self.assertRaises(ValueError):parallel.apply_full_polish(bad,inv,edit,f.catalog)

    def test_meaning_proof_cannot_cite_nonexistent_or_wrong_clip_or_omit_fact(self):
        base,f,raw=self.fixture();inv=parallel.make_inventory(raw,f.catalog);plan=dict(edits=f.expanded['edits'])
        good=dict(fact_review=[dict(fact_id='identity',status='covered',clip_index=0,selection_indices=[0],reason='ระบุสินค้าครบ'),dict(fact_id='detail',status='covered',clip_index=1,selection_indices=[0],reason='รายละเอียดครบ')])
        self.assertEqual(len(parallel.validate_fact_proof(good,inv,plan,f.catalog)),2)
        for mutate in [lambda r:r['fact_review'].pop(),lambda r:r['fact_review'][0].update(clip_index=1),lambda r:r['fact_review'][0].update(selection_indices=[999]),lambda r:r['fact_review'][1].update(status='missing',clip_index=-1,selection_indices=[])]:
            bad=copy.deepcopy(good);mutate(bad)
            with self.assertRaises(ValueError):parallel.validate_fact_proof(bad,inv,plan,f.catalog)

    def test_missing_semantic_fact_is_inserted_without_rewriting_existing_body_and_rechecked(self):
        base,f,raw=self.fixture()
        incomplete=copy.deepcopy(f.expanded['edits'][0]);incomplete['selections']=[dict(unit_id='A',first_word=1,last_word=1)]
        def send(payload,key):
            fields=payload['text']['format']['schema']['properties'];data=json.loads(payload['input'])
            if 'facts' in fields:answer=raw
            elif 'edit' in fields:answer=dict(edit=incomplete,review_note='โครงFULL')
            elif 'replacements' in fields:answer=dict(replacements=[],drops=[],additions=f.expanded['edits'][1:],review_note='คลิปย่อย')
            elif 'equivalent_facts' in fields:answer=dict(equivalent_facts=[],remove_selection_indices=[],selection_replacements=[],review_note='ยังไม่เกลาสาระ')
            elif 'insertions' in fields:answer=dict(insertions=[dict(edit_index=0,before_selection_index=0,selections=[dict(unit_id='A',first_word=0,last_word=0)])],reason='เติมชื่อสินค้าโดยคงbody')
            else:
                scripts=data['actual_selected_scripts'];rows=[]
                for fact in raw['facts']:
                    span=fact['options'][0]['selections'][0]
                    found=[(s['clip_index'],r['selection_index']) for s in scripts for r in s['selections'] if r['unit_id']==span['unit_id'] and r['first_word']<=span['first_word']<=span['last_word']<=r['last_word']]
                    i,j=found[0] if found else (-1,-1)
                    rows.append(dict(fact_id=fact['id'],status='covered' if found else 'missing',clip_index=i,selection_indices=[j] if found else [],reason='พบวลีจริง' if found else 'ชื่อสินค้ายังหาย'))
                answer=dict(fact_review=rows)
            return answer,dict(usage={})
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            with patch('parallel_requests.plan_once',side_effect=send) as post,patch('parallel_editorial.resolve_plan',side_effect=base.cuts):
                plan=parallel.run(f.catalog,folder,dict(model='test',effort='medium'),'secret',progress,{}, {})
            self.assertEqual(post.call_count,8)
            self.assertEqual([(s['first_word'],s['last_word']) for s in plan['edits'][0]['selections']],[(0,0),(1,1)])
            self.assertTrue((folder/'meaning-proof-after-insert-response.json').exists())

