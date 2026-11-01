import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

import api_asr
import engine
from planner import request_body


class ApiAsrTests(unittest.TestCase):
    def test_windows_cover_all_samples_once_and_prefer_pause(self):
        n = 91 * 16000 + 17
        spans = api_asr.windows(n, [(0, 22), (24, 50), (51, 91)])
        self.assertEqual(spans[0][1], 23 * 16000)
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], n)
        self.assertTrue(all(b == c for (_, b), (c, _) in zip(spans, spans[1:])))
        self.assertTrue(all(0 < b-a <= 28*16000 for a, b in spans))

    def test_alignment_is_new_audio_timing_not_old_word_indices(self):
        raw = [dict(word='กระเป๋า', start=.2, end=1), dict(word=' กันขโมย', start=1.1, end=2)]
        result = api_asr.aligned_segments('กระเป๋า กันขโมย', raw, 24, 3)
        self.assertEqual(result[0]['start'], 24.2)
        self.assertEqual(result[0]['end'], 26)
        with self.assertRaises(ValueError):
            api_asr.aligned_segments('พูดคนละประโยค', raw, 24, 3)
        with self.assertRaises(ValueError):
            api_asr.aligned_segments('กระเป๋า', [dict(word='กระเป๋า', start=0, end=0)], 0, 3)
        with self.assertRaises(ValueError):
            api_asr.aligned_segments('กระเป๋า', [dict(word='กระเป๋า', start=2, end=4)], 0, 3)
        from fast_logic import timed_words
        uncertain = api_asr.aligned_segments('เดี๋ยวนะ', [dict(word='เดี๋ยวนะ', start=0, end=5)], 0, 6)
        self.assertTrue(uncertain[0]['alignment_uncertain'])
        self.assertEqual(timed_words(uncertain[0]), [])

    def test_paid_response_persisted_before_alignment_and_reused_on_resume(self):
        pcm = np.zeros(16000, dtype=np.int16)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            response = dict(text='คำจริง', usage=dict(seconds=1))
            with patch('api_asr.transcribe_once', return_value=response) as send:
                with self.assertRaisesRegex(ValueError, 'alignment'):
                    api_asr.run(pcm, folder, {}, 'test-secret', lambda **kw: None, [],
                                aligner=lambda *args: (_ for _ in ()).throw(ValueError('alignment')))
                saved = (folder/'api-transcription.json').read_text(encoding='utf8')
                self.assertNotIn('test-secret', saved)
                result = api_asr.run(pcm, folder, {}, '', lambda **kw: None, [], aligner=lambda *args: ['aligned'])
                self.assertEqual(result, ['aligned'])
                self.assertEqual(send.call_count, 1)

    def test_unknown_api_result_not_automatically_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('api_asr.transcribe_once', side_effect=RuntimeError('timeout')) as send:
                for _ in range(2):
                    with self.assertRaises((RuntimeError, ValueError)):
                        api_asr.run(np.zeros(16000,dtype=np.int16), Path(tmp), {}, 'test-secret', lambda **kw: None, [], aligner=lambda *a: [])
                self.assertEqual(send.call_count, 1)

    def test_medium_model_and_config_validation(self):
        config = engine.validate_config(dict(mode='ai', model='gpt-6-luna', effort='medium', transcription='api'))
        body = request_body([], config['model'], config['effort'], '')
        self.assertEqual(body['reasoning'], dict(effort='medium'))
        self.assertEqual(body['model'], 'gpt-6-luna')
        with self.assertRaises(ValueError):
            engine.validate_config(dict(transcription='invented'))

    def test_api_pipeline_exports_usage_and_resumes_without_paid_calls(self):
        from demo import create
        from verify_outputs import check
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, pcm, _ = create(root/'media')
            folder = root/'job'
            text = 'กระเป๋ากันขโมย'
            aligned = [dict(start=.6, end=2.4, text=text,
                            words=[dict(start=.6, end=2.4, text=text)])]
            config = dict(xml=str(source), mode='ai', model='gpt-6-luna', effort='medium', transcription='api')
            plan = dict(edits=[dict(name='เรื่องจริง', purpose='ทดลอง', selections=[
                dict(unit_id='U0001', first_word=0, last_word=0, reason='เนื้อหา')])],
                source_review=[dict(unit_id='U0001', decision='used', reason='ใช้แล้ว')], notes=[])
            with patch('engine.extract_audio', return_value=pcm) as extract, patch('engine.vad_intervals', return_value=[(.6,2.4)]), \
                 patch('api_asr.Aligner', return_value=lambda *args: aligned), \
                 patch('api_asr.transcribe_once', return_value=dict(text=text)) as transcribe, \
                 patch('engine.plan_once', return_value=(plan, dict(usage={}))) as planner:
                # Mock extractor must still persist the exact WAV expected by resume/export verification.
                from audio_io import write_pcm
                def extract_fixture(project, out, progress):
                    write_pcm(out/'timeline.wav', pcm)
                    return pcm
                extract.side_effect = extract_fixture
                engine.run(config, folder, 'test-secret')
                engine.run(config, folder, '')
                self.assertEqual(transcribe.call_count, 1)
                self.assertEqual(planner.call_count, 1)
                self.assertEqual(planner.call_args.args[0]['reasoning']['effort'], 'medium')
                self.assertTrue(check(folder)['source_usage_complete'])
                self.assertTrue(check(folder)['wav_exact_samples'])
                for path in folder.rglob('*.json'):
                    self.assertNotIn('test-secret', path.read_text(encoding='utf8'))


if __name__ == '__main__':
    unittest.main()
