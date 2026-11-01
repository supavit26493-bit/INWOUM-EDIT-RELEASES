import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import engine,thai_phrases,story_editorial,silence_v3


class StoryV4Tests(unittest.TestCase):
    def test_lexical_review_keeps_original_words_and_exact_audio_endpoints(self):
        text='ตัวนี้มีที่ห้อยกุญแจด้วย'
        raw=[dict(start=i*.1,end=(i+1)*.1,text=c) for i,c in enumerate(text)]
        segment=dict(start=0,end=len(text)*.1,text=text,words=raw)
        coarse=thai_phrases.phrases(segment)
        fine=thai_phrases.phrases(segment,lexical_review=True)
        self.assertGreater(len(fine),len(coarse))
        self.assertEqual(''.join(w['text'] for w in fine),text)
        self.assertTrue(all(w['end']>w['start'] for w in fine))
        self.assertTrue(all(w['start'] in [r['start'] for r in raw] and w['end'] in [r['end'] for r in raw] for w in fine))

    def test_new_policy_padding_does_not_restore_omitted_countdown_or_profanity(self):
        from demo import create
        from project import intake
        from fast_logic import resolve_plan
        with tempfile.TemporaryDirectory() as tmp:
            source,_,_=create(Path(tmp)/'media');project=intake(source,audio_track=1)
            catalog=[dict(id='A',start=0,end=3,text='นับเทค ข้อความ หยาบ',protected=False,words=[
                dict(start=0,end=1,text='นับเทค'),dict(start=1,end=2,text='ข้อความ'),dict(start=2,end=3,text='หยาบ')])]
            plan=dict(edits=[dict(name='เนื้อหา',selections=[dict(unit_id='A',first_word=1,last_word=1)])])
            silence=dict(profile='compact',removed_frames=[],policy='vad-negative-adaptive-v3')
            ranges=resolve_plan(plan,catalog,silence,project)[0]['ranges']
            self.assertEqual([(r['start'],r['end']) for r in ranges],[(1,2)])

    def test_thai_lexical_boundaries_preserve_text_times_and_zero_vowels(self):
        text='อะสามสองหนึ่งใครกำลังจะไปยุโรปต้องแยกเก็บของสำคัญครับ'
        raw=[dict(start=i*.1,end=(i+1)*.1,text=c) for i,c in enumerate(text)]
        segment=dict(start=0,end=len(text)*.1,text=text,words=raw)
        result=thai_phrases.phrases(segment)
        self.assertGreater(len(result),2)
        self.assertEqual(''.join(w['text'] for w in result),text)
        self.assertTrue(any(w['text'].endswith('สามสองหนึ่ง') for w in result))
        self.assertTrue(all(w['start'] in [r['start'] for r in raw] and w['end'] in [r['end'] for r in raw] for w in result))
        # A combining vowel with no duration must never become a standalone cut.
        raw[2]['end']=raw[2]['start']
        result=thai_phrases.phrases(segment)
        self.assertEqual(''.join(w['text'] for w in result),text)
        self.assertTrue(all(w['end']>w['start'] for w in result))
        self.assertEqual(thai_phrases.phrases(dict(segment,text='ข้อความอื่น')),[])

    def test_vad_positive_soft_speech_and_protected_action_are_preserved(self):
        pcm=np.full(4*16000,120,dtype=np.int16)
        pcm[:16000]=5000;pcm[48000:]=5000
        transcript=[dict(start=0,end=4,text='คำพูด',words=[dict(start=0,end=4,text='คำพูด')])]
        result=silence_v3.detect(pcm,transcript,'compact',[(2,2.5)],[(0,1),(1.4,1.8),(3,4)])
        for a,b in result['removed_frames']:
            self.assertFalse(max(a,70)<min(b,90))
            self.assertFalse(max(a,100)<min(b,125))
        self.assertGreater(result['removed_seconds'],0)
        soft=np.full(2*16000,50,dtype=np.int16)
        t=[dict(start=0,end=2,text='เสียงเบา',words=[])]
        self.assertEqual(silence_v3.detect(soft,t,vad_speech=[])['removed_seconds'],0)

    def test_two_calls_cached_and_final_export_uses_review_not_draft(self):
        from demo import create
        from project import intake,prepare_source
        from audio_io import write_pcm
        from verify_outputs import check
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,pcm,transcript=create(root/'media');folder=root/'job';folder.mkdir()
            project=intake(source,audio_track=1);prepare_source(project,folder);write_pcm(folder/'timeline.wav',pcm)
            for name,value in [('intake.json',engine.identity(project)),('audio-identity.json',dict(pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest())),('transcript.json',transcript),('vad.json',None)]:engine.save(folder/name,value)
            cfg=dict(xml=str(source),mode='ai',model='gpt-6-luna',effort='medium',editorial_policy='story-v4',silence_policy='speech-v3')
            _,catalog=engine.cut_map(cfg,folder,project,pcm,transcript,None)
            raw=dict(edits=[dict(name='draft',purpose='เรื่อง',selections=[dict(unit_id=catalog[0]['id'],first_word=0,last_word=0)])],unused=[dict(unit_id=u['id'],decision='alternative',reason='เทคสำรอง') for u in catalog[1:]])
            final=copy.deepcopy(raw);final['edits'][0]['name']='reviewed'
            with patch('story_editorial.plan_once',side_effect=[(raw,dict(usage={})),(final,dict(usage={}))]) as send:
                engine.run(cfg,folder,'test-secret',prepared=(project,pcm,transcript,None))
                engine.run(cfg,folder,'')
                self.assertEqual(send.call_count,2)
                self.assertIn('assembled_draft',json.loads(send.call_args_list[1].args[0]['input']))
                self.assertIn('ACCEPTED USER WORKFLOW',send.call_args_list[0].args[0]['instructions'])
                self.assertIn('Technical sizes/weights/capacities',send.call_args_list[1].args[0]['instructions'])
                self.assertIn('CONTENT GROUPS BEFORE HOOK VARIANTS',send.call_args_list[0].args[0]['instructions'])
                self.assertIn('Do not cap the collection',send.call_args_list[0].args[0]['instructions'])
                schema=send.call_args_list[0].args[0]['text']['format']['schema']['properties']['edits']['items']
                self.assertIn('content_group',schema['required'])
                self.assertIn('distinct_body_summary',schema['required'])
            self.assertTrue(check(folder)['wav_exact_samples'])
            self.assertEqual(json.loads((folder/'plan.json').read_text(encoding='utf8'))['edits'][0]['name'],'reviewed')
            self.assertEqual(len(json.loads((folder/'api-usage.json').read_text(encoding='utf8'))),2)
            for path in folder.glob('*.json'):self.assertNotIn('test-secret',path.read_text(encoding='utf8'))

    def test_unknown_request_cannot_resubmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('story_editorial.plan_once',return_value=(None,dict(error='timeout'))) as send:
                for _ in range(2):
                    with self.assertRaises((RuntimeError,ValueError)):
                        story_editorial.call(dict(test=True),Path(tmp),'key','draft')
                self.assertEqual(send.call_count,1)


if __name__=='__main__':unittest.main()
