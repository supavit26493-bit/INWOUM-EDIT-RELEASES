import copy,hashlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import model_catalog,speech_cleanup

class ModelTests(unittest.TestCase):
    def test_saved_request_hash_matches_adapted_wire_parameters(self):
        import parallel_requests,planner
        with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.plan_once',return_value=(dict(ok=True),dict(usage={}))) as send:
            folder=Path(tmp);original=dict(model='gpt-4.1',reasoning=dict(effort='medium'),max_output_tokens=24000,input='text')
            parallel_requests.call(original,folder,'secret','compat')
            saved=json.loads((folder/'compat-request.json').read_text('utf8'))
            self.assertEqual(saved,send.call_args.args[0]);self.assertNotIn('reasoning',saved)
            row=json.loads((folder/'api-usage.json').read_text('utf8'))[0]
            self.assertEqual(row['request_hash'],planner.fingerprint(saved))
    def test_legacy_nonreasoning_parameters_and_output_caps_without_model_substitution(self):
        for model,limit in [('gpt-4.1',24000),('gpt-4o-mini',16384),('gpt-4o-2024-08-06',16384)]:
            request=dict(model=model,reasoning=dict(effort='medium'),max_output_tokens=24000,input='text')
            before=copy.deepcopy(request);wire=model_catalog.prepare_request(request)
            self.assertNotIn('reasoning',wire);self.assertEqual(wire['model'],model)
            self.assertEqual(wire['max_output_tokens'],limit);self.assertEqual(request,before)
    def test_reasoning_default_and_pro_high(self):
        p=dict(model='gpt-6.1-sol',reasoning=dict(effort='medium'))
        self.assertEqual(model_catalog.prepare_request(p),p)
        self.assertNotIn('reasoning',model_catalog.prepare_request(dict(model='gpt-6-luna',reasoning=dict(effort='default'))))
        self.assertEqual(model_catalog.prepare_request(dict(model='gpt-5-pro',reasoning=dict(effort='low')))['reasoning']['effort'],'high')
    def test_known_incompatible_models_rejected_before_network(self):
        for model in ('gpt-4','gpt-3.5-turbo','gpt-4o-transcribe','gpt-image-1','gpt-realtime','gpt-4o-2024-05-13'):
            with self.assertRaises(ValueError):model_catalog.prepare_request(dict(model=model))
    def test_account_listing_keeps_all_gpt_including_disabled_specialized_models_and_snapshots(self):
        response=io.BytesIO(json.dumps(dict(data=[dict(id=m) for m in ('gpt-6.1-sol','gpt-4.1-2025-04-14','gpt-image-1','whisper-1','gpt-6.1-sol')])).encode())
        with patch('model_catalog.urllib.request.urlopen',return_value=response) as send:
            rows=model_catalog.list_account_models('secret')
        self.assertEqual([r['id'] for r in rows],['gpt-4.1-2025-04-14','gpt-6.1-sol','gpt-image-1'])
        self.assertFalse(rows[-1]['supported']);self.assertNotIn('secret',json.dumps(rows))
        self.assertEqual(send.call_args.args[0].get_method(),'GET')
        self.assertEqual(send.call_args.args[0].full_url,'https://api.openai.com/v1/models')

class CleanupTests(unittest.TestCase):
    def test_program_assembly_applies_cleanup_once_without_extra_api(self):
        import engine,sales_draft,runtime_contract,test_sales_v8
        source,story=test_sales_v8.V8Tests().example()
        cat=[dict(id='A',start=0,end=4,text='ดีนะดีนะซื้อได้',protected=False,words=[dict(text=t,start=i*.5,end=(i+1)*.5) for i,t in enumerate(['ดี','นะ','ดี','นะ','ซื้อ','ได้'])])]
        raw=dict(edits={mid:dict(name=mid,purpose='รีวิว',selections=[dict(unit_id='A',first_word=0,last_word=5)]) for mid in ('FULL','V1')},unused=[],review_note='รอตรวจ')
        before=copy.deepcopy(raw)
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)/'map';parent.mkdir();folder=Path(tmp)/'draft';folder.mkdir()
            transcript=json.dumps(source).encode();(folder/'transcript.json').write_bytes(transcript)
            (parent/'grouping-source.json').write_text(json.dumps(dict(transcript_sha256=hashlib.sha256(transcript).hexdigest(),segments=source)),'utf8')
            (parent/'content-groups.json').write_text(json.dumps(story),'utf8')
            (parent/'rules-used.txt').write_text(runtime_contract.rules(),'utf8')
            with patch('sales_draft.call',return_value=raw) as send,patch('sales_draft.resolve_plan',return_value=[dict(ranges=[dict(start=0,end=3)])]*2):
                plan=sales_draft.run(cat,folder,dict(approved_story_map_job='map',model='gpt-6-luna'),'secret',engine.Progress(folder,lambda **e:None,lambda:False),dict(duration=200),{})
            self.assertEqual(send.call_count,1);self.assertEqual(raw,before)
            self.assertEqual(self.text(plan,cat),'ดีนะซื้อได้')
            self.assertTrue((folder/'api-plan-before-cleanup.json').exists())
            log=json.loads((folder/'speech-cleanup.json').read_text('utf8'))
            self.assertFalse(log['manual_edits']);self.assertEqual(log['extra_api_calls'],0)
    def fixture(self,lists):
        catalog=[dict(id=str(i),words=[dict(text=t,start=j,end=j+1) for j,t in enumerate(words)],protected=False) for i,words in enumerate(lists)]
        plan=dict(edits=[dict(map_id='FULL',name='full',selections=[dict(unit_id=u['id'],first_word=0,last_word=len(u['words'])-1) for u in catalog])],source_review=[dict(unit_id=u['id'],decision='used') for u in catalog])
        return plan,catalog
    def text(self,p,c):
        lookup={u['id']:u for u in c}
        return ''.join(w['text'] for s in p['edits'][0]['selections'] for w in lookup[s['unit_id']]['words'][s['first_word']:s['last_word']+1])
    def test_repeated_phrase_across_asr_units_and_raw_api_immutable(self):
        plan,cat=self.fixture([['ดี','นะ'],['ดี','นะ','ซื้อ','ได้']]);original=copy.deepcopy(plan)
        clean,changes=speech_cleanup.clean(plan,cat)
        self.assertEqual(self.text(clean,cat),'ดีนะซื้อได้');self.assertEqual(plan,original)
        self.assertEqual(changes[0]['text'],'ดีนะ')
    def test_single_pronoun_stutter_and_multiple_repeats(self):
        plan,cat=self.fixture([['เรา','เรา','เรา','มี','ของ']])
        clean,changes=speech_cleanup.clean(plan,cat)
        self.assertEqual(self.text(clean,cat),'เรามีของ');self.assertEqual(len(changes),2)
    def test_meaningful_thai_reduplication_and_same_subject_repeated_later_are_retained(self):
        plan,cat=self.fixture([['จริง','จริง','สาว','สาว','คล้าย','คล้าย','ดี','นะ','ซื้อ','ดี','นะ']])
        clean,changes=speech_cleanup.clean(plan,cat)
        self.assertEqual(clean,plan);self.assertEqual(changes,[])
    def test_alternate_versions_independent_and_unused_review_consistent(self):
        plan,cat=self.fixture([['เหนื่อยนะ'],['เหนื่อยนะ']]);plan['edits'].append(copy.deepcopy(plan['edits'][0]))
        clean,changes=speech_cleanup.clean(plan,cat)
        self.assertEqual(len(clean['edits']),2)
        self.assertTrue(all(len(e['selections'])==1 for e in clean['edits']))
        self.assertEqual(clean['source_review'][1]['decision'],'duplicate')
    def test_cleanup_is_idempotent(self):
        plan,cat=self.fixture([['ดี','นะ','ดี','นะ','ดี','นะ','เรา','เรา']])
        clean,changes=speech_cleanup.clean(plan,cat);again,more=speech_cleanup.clean(clean,cat)
        self.assertEqual(again,clean);self.assertEqual(more,[])

if __name__=='__main__':unittest.main()
