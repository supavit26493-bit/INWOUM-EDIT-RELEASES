import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fast_editorial


class FastEditorialTests(unittest.TestCase):
    def catalog(self):
        return [dict(id='A',start=0,end=3,text='โอเค เนื้อหา',protected=False,words=[
            dict(start=0,end=1,text='โอเค'),dict(start=1,end=3,text=' เนื้อหา')]),
            dict(id='B',start=4,end=5,text='พูดซ้ำ',protected=False,words=[])]

    def response(self):
        return dict(edits=[dict(name='เรื่อง',purpose='ตอบคำถาม',selections=[dict(unit_id='A',first_word=1,last_word=1)])],
            unused=[dict(unit_id='B',decision='duplicate',reason='A ให้สาระครบกว่า')])

    def test_payload_has_one_copy_of_each_phrase_and_no_audit_metadata_or_images(self):
        payload=fast_editorial.request_body(self.catalog(),'gpt-6-luna','medium','งานนี้')
        data=json.loads(payload['input'])
        self.assertNotIn('text',data['units'][0])
        self.assertNotIn('words',data['units'][0])
        self.assertEqual(data['units'][0]['phrases'],['โอเค',' เนื้อหา'])
        selection=payload['text']['format']['schema']['properties']['edits']['items']['properties']['selections']['items']
        self.assertEqual(set(selection['properties']),{'unit_id','first_word','last_word'})
        self.assertEqual(payload['max_output_tokens'],12000)
        self.assertEqual(payload['reasoning'],dict(effort='medium'))
        self.assertFalse(payload['store'])
        self.assertNotIn('input_image',json.dumps(payload))
        self.assertNotIn('tools',payload)

    def test_used_coverage_is_computed_and_unused_reasons_must_be_complete(self):
        plan=fast_editorial.normalize_plan(self.response(),self.catalog())
        rows={r['unit_id']:r for r in plan['source_review']}
        self.assertEqual(rows['A']['review_source'],'selection_map')
        self.assertEqual(rows['B']['review_source'],'ai_unused_reason')
        for mutate in [lambda r:r.update(unused=[]),
                       lambda r:r['unused'][0].update(unit_id='A'),
                       lambda r:r['edits'][0]['selections'][0].update(last_word=9),
                       lambda r:r['edits'][0]['selections'][0].update(first_word=True)]:
            raw=self.response();mutate(raw)
            with self.assertRaises(ValueError):fast_editorial.normalize_plan(raw,self.catalog())

    def test_local_text_checks_warn_without_silently_altering_plan(self):
        catalog=[dict(id='A',text='ถ้าจับดูจะรู้ว่าแม่งแข็ง',words=[],start=0,end=2,protected=False)]
        plan=dict(edits=[dict(name='test',purpose='test',selections=[dict(unit_id='A',first_word=0,last_word=0)]*2)])
        original=copy.deepcopy(plan)
        report=fast_editorial.text_checks(plan,catalog)
        self.assertIn('language_needs_review',[w['type'] for w in report['warnings']])
        self.assertIn('exact_phrase_repeat',[w['type'] for w in report['warnings']])
        self.assertEqual(report['meaning_review'],'pending')
        self.assertEqual(plan,original)

    def test_one_paid_call_pipeline_and_resume_with_complete_usage(self):
        import engine
        from demo import create
        from project import intake,prepare_source
        from audio_io import write_pcm
        from fast_logic import make_catalog
        from verify_outputs import check
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,pcm,transcript=create(root/'media');folder=root/'job';folder.mkdir()
            project=intake(source,audio_track=1);prepare_source(project,folder);write_pcm(folder/'timeline.wav',pcm)
            engine.save(folder/'intake.json',engine.identity(project))
            engine.save(folder/'audio-identity.json',dict(pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest()))
            engine.save(folder/'transcript.json',transcript);engine.save(folder/'vad.json',None)
            catalog=make_catalog(transcript,project['duration'])
            response=dict(edits=[dict(name='เรื่อง',purpose='ตอบคำถาม',selections=[dict(unit_id=catalog[0]['id'],first_word=0,last_word=0)])],
                unused=[dict(unit_id=u['id'],decision='alternative',reason='เทคสำรอง') for u in catalog[1:]])
            cfg=dict(xml=str(source),audio_track=1,mode='ai',model='gpt-6-luna',effort='medium',editorial_policy='story-v3')
            with patch('fast_editorial.plan_once',return_value=(response,dict(usage={}))) as send:
                engine.run(cfg,folder,'secret-test',prepared=(project,pcm,transcript,None))
                engine.run(cfg,folder,'')
                self.assertEqual(send.call_count,1)
            proof=check(folder)
            self.assertTrue(proof['wav_exact_samples']);self.assertTrue(proof['source_usage_complete'])
            self.assertTrue((folder/'ASSEMBLED-SCRIPTS.md').exists())
            for path in folder.rglob('*.json'):self.assertNotIn('secret-test',path.read_text(encoding='utf8'))

    def test_failed_or_unknown_request_is_not_repeated_on_resume(self):
        import engine
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);progress=engine.Progress(folder,lambda **kw:None,lambda:False)
            with patch('fast_editorial.plan_once',return_value=(None,dict(error='incomplete',usage={}))) as send:
                for _ in range(2):
                    with self.assertRaises((ValueError,RuntimeError)):
                        fast_editorial.run(self.catalog(),folder,dict(model='gpt-6-luna'), 'secret',progress,{}, {})
                self.assertEqual(send.call_count,1)


if __name__=='__main__':unittest.main()
