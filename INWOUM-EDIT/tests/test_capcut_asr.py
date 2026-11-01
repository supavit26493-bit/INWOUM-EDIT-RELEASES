import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from dataclasses import dataclass
import numpy as np

import capcut_asr
import engine


@dataclass
class Result:
    utterances: list


class CapcutTests(unittest.TestCase):
    def test_real_engine_routes_capcut_exports_and_resume_requires_matching_provider(self):
        from demo import create
        from audio_io import write_pcm
        from verify_outputs import check
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source,pcm,_=create(root/'media')
            config=dict(xml=str(source),mode='local',transcription='capcut')
            folder=root/'job'
            client=self.client()
            aligned=[dict(start=.1,end=.9,text='คำจริง',words=[dict(start=.1,end=.9,text='คำจริง')])]
            def extract(project,out,emit):
                write_pcm(out/'timeline.wav',pcm)
                return pcm
            with patch('bootstrap.ROOT',root), patch('engine.extract_audio',side_effect=extract), \
                 patch('engine.vad_intervals',return_value=[(.1,.9)]), \
                 patch('capcut_asr.make_client',return_value=client) as factory, \
                 patch('api_asr.Aligner',return_value=lambda *a:aligned), \
                 patch('engine.plan_once',side_effect=AssertionError('No editorial API')):
                engine.run(config,folder,'')
                engine.run(config,folder,'')
                self.assertEqual(factory.call_count,1)
                self.assertTrue(check(folder)['wav_exact_samples'])
                with self.assertRaises(ValueError):
                    engine.run(dict(config,transcription='whisper'),folder,'')
                with self.assertRaises(ValueError):
                    engine.run(dict(config,transcription='api'),folder,'')

    def test_overlap_keeps_all_text_and_raw_times_but_disables_internal_cuts(self):
        from editorial import phrase_words
        from fast_logic import timed_words
        spans=[dict(start=.1,end=1,text='คำหนึ่ง',words=[dict(start=.1,end=1,text='คำหนึ่ง')]),
               dict(start=.9,end=2,text='คำสอง',words=[dict(start=.9,end=2,text='คำสอง')])]
        result=capcut_asr.merge_overlaps(spans)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['text'],'คำหนึ่ง คำสอง')
        self.assertEqual(result[0]['words'],spans[0]['words']+spans[1]['words'])
        self.assertEqual(phrase_words(result[0]),[])
        self.assertEqual(timed_words(result[0]),[])

    def client(self):
        client = Mock()
        client.create_stt_task.return_value = dict(data=dict(tasks=[dict(id='private-id', token='private-token')]))
        client.query_stt_task.return_value = dict(data=dict(tasks=[dict(status='completed')]))
        client.extract_subtitles.return_value = Result([dict(text='คำจริง', start_time=100, end_time=900)])
        return client

    def test_save_before_gpu_and_resume_and_shared_cache_no_upload(self):
        pcm = np.zeros(16000, dtype=np.int16)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root/'job'; folder.mkdir()
            client = self.client()
            factory = Mock(return_value=client)
            with self.assertRaisesRegex(ValueError, 'GPU'):
                capcut_asr.run(pcm, folder, {}, lambda **e: None,
                    aligner=Mock(side_effect=ValueError('GPU')), client_factory=factory, cache_root=root/'cache')
            self.assertEqual(json.loads((folder/'capcut-transcription.json').read_text(encoding='utf8'))['status'], 'completed')
            aligned = [dict(start=.1, end=.9, text='คำจริง', words=[])]
            for target in (folder, root/'other'):
                target.mkdir(exist_ok=True)
                result = capcut_asr.run(pcm, target, {}, lambda **e: None,
                    aligner=lambda *a: aligned, client_factory=factory, cache_root=root/'cache')
                capcut_asr.verify_ready(pcm, target, result)
                saved = (target/'capcut-transcription.json').read_text(encoding='utf8')
                self.assertNotIn('private-token', saved)
                self.assertNotIn('private-id', saved)
                with self.assertRaises(ValueError):
                    capcut_asr.verify_ready(pcm, target, [dict(text='changed')])
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(client.create_stt_task.call_count, 1)

    def test_unknown_upload_or_submit_is_never_retried_or_leaked(self):
        for stage in ('upload_audio', 'create_stt_task', 'query_stt_task'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); folder=root/'job'; folder.mkdir()
                client = self.client()
                getattr(client, stage).side_effect = RuntimeError('private-token')
                factory = Mock(return_value=client)
                for _ in range(2):
                    with self.assertRaises((RuntimeError, ValueError)) as caught:
                        capcut_asr.run(np.zeros(16000,dtype=np.int16),folder,{},lambda **e: None,
                            aligner=Mock(),client_factory=factory,cache_root=root/'cache')
                    self.assertNotIn('private-token', str(caught.exception))
                self.assertEqual(factory.call_count, 1)
                self.assertNotIn('private-token', (folder/'capcut-transcription.json').read_text(encoding='utf8'))

    def test_reject_foreign_audio_and_invalid_block_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            engine.save(root/'capcut-transcription.json',dict(contract={},status='completed'))
            with self.assertRaises(ValueError):
                capcut_asr.run(np.zeros(16000,dtype=np.int16),root,{},lambda **e:None,cache_root=root/'cache')
        for block in (dict(text='ไทย',start_time=0,end_time=31000),
                      dict(text='ไทย',start_time=900,end_time=800),
                      dict(text='',start_time=100,end_time=800)):
            with self.assertRaises(ValueError):
                capcut_asr.validate_blocks([block],30)

    def test_no_ambient_account_credentials_and_host_redirect_guard(self):
        import os
        with patch.dict(os.environ, CAPCUT_COOKIE='private-cookie',CAPCUT_BEARER='private-bearer',
                        CAPCUT_HEADERS_JSON='{"Authorization":"private"}', CAPCUT_BASE_URL='https://example.com'):
            client=capcut_asr.make_client()
        self.assertEqual(client.extra_headers,{})
        self.assertFalse(client.session.trust_env)
        self.assertIn('capcutapi.com',client.base_url)
        with self.assertRaises(ValueError):
            client.session.get('https://example.com/file')
        client.session.close()

    def test_config_accepts_capcut_and_keeps_legacy_missing_default(self):
        self.assertEqual(engine.validate_config(dict(transcription='capcut'))['transcription'],'capcut')
        self.assertNotIn('transcription',engine.validate_config({}))


if __name__ == '__main__':
    unittest.main()
