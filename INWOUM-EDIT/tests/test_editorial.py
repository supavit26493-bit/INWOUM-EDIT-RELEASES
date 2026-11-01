import copy
import unittest
import tempfile
import hashlib
from pathlib import Path
from unittest.mock import patch
import editorial
import planner


class EditorialTests(unittest.TestCase):
    def test_standalone_spaces_split_phrases_but_never_split_thai_vowels(self):
        segment=dict(start=0,end=3,text='โอเค ผมกำลังไปยุโรป',alignment_uncertain=True,words=[
            dict(start=0,end=.3,text='โอเค'),dict(start=.3,end=.3,text=' '),
            dict(start=.4,end=.6,text='ผม'),dict(start=.6,end=.8,text='ก'),
            dict(start=.8,end=.8,text='ำ'),dict(start=.8,end=3,text='ลังไปยุโรป')])
        original=copy.deepcopy(segment)
        words=editorial.phrase_words(segment)
        self.assertEqual([w['text'] for w in words],['โอเค',' ผมกำลังไปยุโรป'])
        self.assertEqual(words[1]['start'],.4)
        self.assertEqual(segment,original)
        with_long=copy.deepcopy(segment);with_long['words'][-1]['end']=8
        self.assertEqual(editorial.phrase_words(with_long),[])

    def test_marks_and_repetition_symbols_stay_with_phrase(self):
        segment=dict(text='แข็ง ๆ',words=[dict(start=0,end=1,text='แข็ง'),
            dict(start=1,end=1,text=' '),dict(start=1,end=1.1,text='ๆ')])
        self.assertEqual(editorial.phrase_words(segment),[dict(start=0,end=1.1,text='แข็ง ๆ')])

    def test_changed_or_unaligned_text_cannot_get_new_word_boundaries(self):
        segment=dict(text='ข้อความใหม่',words=[dict(start=0,end=1,text='คนละข้อความ')])
        self.assertEqual(editorial.phrase_words(segment),[])

    def test_contract_is_loaded_and_review_uses_concatenated_selected_text(self):
        catalog=[dict(id='U1',start=0,end=3,text='โอเค เนื้อหา',words=[
            dict(start=0,end=1,text='โอเค'),dict(start=2,end=3,text=' เนื้อหา')],protected=False)]
        plan=dict(edits=[dict(name='test',purpose='test',story_outline=['เรื่อง'],selections=[
            dict(unit_id='U1',first_word=1,last_word=1,reason='ครบ',role='hook',claim_id='หัวข้อ')])])
        with patch('editorial.rules_text',return_value='ACTUAL-RULE-CONTRACT'):
            body=planner.request_body(catalog,'gpt-6-luna','medium','',editorial=True)
            review=planner.review_body(catalog,plan,'gpt-6-luna','medium','')
        self.assertIn('ACTUAL-RULE-CONTRACT',body['instructions'])
        self.assertIn('POST-ASSEMBLY REVIEW',review['instructions'])
        scripts=editorial.assembled_scripts(plan,catalog)
        self.assertEqual(scripts[0]['assembled_text'],'เนื้อหา')
        self.assertIn('story_outline',body['text']['format']['schema']['properties']['edits']['items']['required'])

    def test_claimed_hook_use_must_be_actual_opening(self):
        catalog=[dict(id='A',words=[{},{}])]
        plan=dict(edits=[dict(selections=[dict(unit_id='A',first_word=1,last_word=1)])],
            hook_inventory=[dict(unit_id='A',first_word=0,last_word=0,decision='use')])
        with self.assertRaises(ValueError): editorial.validate_hooks(plan,catalog)
        plan['hook_inventory'][0].update(first_word=1,last_word=1)
        editorial.validate_hooks(plan,catalog)

    def test_two_bounded_calls_export_and_resume_reuses_both_plans(self):
        import engine
        from demo import create
        from project import intake, prepare_source
        from audio_io import write_pcm
        from fast_logic import make_catalog
        from verify_outputs import check
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,pcm,transcript=create(root/'media')
            project=intake(source,audio_track=1);folder=root/'job';folder.mkdir()
            prepare_source(project,folder);write_pcm(folder/'timeline.wav',pcm)
            engine.save(folder/'intake.json',engine.identity(project))
            engine.save(folder/'audio-identity.json',dict(pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest()))
            engine.save(folder/'transcript.json',transcript);engine.save(folder/'vad.json',None)
            catalog=make_catalog(transcript,project['duration'])
            plan=dict(edits=[dict(name='test',purpose='test',story_outline=['ตอบเรื่อง'],selections=[
                dict(unit_id=catalog[0]['id'],first_word=0,last_word=0,reason='ครบ',role='hook',claim_id='test')])],
                source_review=[dict(unit_id=u['id'],decision='used' if i==0 else 'incomplete',reason='เหตุผลจริง')
                               for i,u in enumerate(catalog)],hook_inventory=[],notes=[])
            cfg=dict(xml=str(source),audio_track=1,mode='ai',model='gpt-6-luna',effort='medium',editorial_policy='story-v2')
            with patch('engine.plan_once',return_value=(plan,dict(usage={}))) as send:
                engine.run(cfg,folder,'secret-test',prepared=(project,pcm,transcript,None))
                engine.run(cfg,folder,'')
                self.assertEqual(send.call_count,2)
                self.assertIn('POST-ASSEMBLY REVIEW',send.call_args.args[0]['instructions'])
            self.assertTrue(check(folder)['wav_exact_samples'])
            self.assertTrue((folder/'rules-used.txt').exists())
            self.assertTrue((folder/'assembled-scripts.json').exists())


if __name__=='__main__': unittest.main()
