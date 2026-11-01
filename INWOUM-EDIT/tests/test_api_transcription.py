import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.error
import wave
import hashlib

import numpy as np
from api_transcription import multipart, wav_bytes, transcribe_once
from asr_compare import text_differences
import asr_compare
from audio_io import write_pcm


class APITranscriptionTests(unittest.TestCase):
    def test_comparison_uses_identical_samples_and_keeps_key_out_of_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, target, shared = base / 'source', base / 'test', base / 'shared'
            source.mkdir(); target.mkdir()
            (shared / 'models/large-v3-turbo').mkdir(parents=True)
            (shared / 'models/large-v3-turbo/model.bin').write_bytes(b'fixture')
            pcm = np.arange(16000, dtype=np.int16)
            write_pcm(source / 'timeline.wav', pcm)
            (source / 'audio-identity.json').write_text(json.dumps(dict(pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest())))
            (source / 'config.json').write_text(json.dumps(dict(shared_home=str(shared))))
            class Segment:
                text = 'เสียงตัวอย่าง'
            class Whisper:
                def transcribe(self, data, **kwargs):
                    np.testing.assert_array_equal(data, pcm.astype(np.float32) / 32768)
                    self.kwargs = kwargs
                    return iter([Segment()]), None
            whisper = Whisper()
            response = dict(text='เสียงตัวอย่าง API', usage=None, languages=[dict(code='th')])
            with patch('faster_whisper.WhisperModel', return_value=whisper), patch('asr_compare.cuda_handles', return_value=[]), patch('asr_compare.transcribe_once', return_value=response) as api:
                result = asr_compare.run(source, target, 'secret-test-key', {}, lambda **e: None, lambda: False)
            np.testing.assert_array_equal(api.call_args.args[0], pcm)
            self.assertEqual(whisper.kwargs['temperature'], 0)
            self.assertEqual(result['api_calls'], 1)
            self.assertFalse(result['cuts_changed'])
            for file in target.glob('*.json'):
                self.assertNotIn('secret-test-key', file.read_text(encoding='utf8'))

    def test_multipart_contains_audio_and_hints_but_never_key(self):
        wav = wav_bytes(np.zeros(16000, dtype=np.int16))
        body, content_type = multipart(wav, 'gpt-transcribe', 'ภาษาไทย', ['RFID', 'ลวดสลิง'])
        self.assertIn('multipart/form-data', content_type)
        self.assertIn(b'RIFF', body)
        self.assertIn(b'name="file"; filename="sample.wav"', body)
        self.assertIn(b'name="languages[]"', body)
        self.assertIn('ลวดสลิง'.encode(), body)
        self.assertNotIn(b'Authorization', body)
        with wave.open(io.BytesIO(wav)) as audio:
            self.assertEqual(audio.getnframes(), 16000)
            self.assertEqual(audio.getframerate(), 16000)

    def test_legacy_model_uses_singular_language_without_unsupported_keywords(self):
        body, _ = multipart(b'RIFF', 'gpt-4o-transcribe', 'คำศัพท์ RFID', ['RFID'])
        self.assertIn(b'name="language"', body)
        self.assertNotIn(b'name="languages[]"', body)
        self.assertNotIn(b'name="keywords[]"', body)

    def test_bad_model_and_keyword_rejected(self):
        for model, words in [('text-model', []), ('gpt-transcribe', ['bad\nword'])]:
            with self.assertRaises(ValueError):
                multipart(b'RIFF', model, keywords=words)

    def test_parse_api_transcript_without_inventing_timestamps(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        result = dict(text='กระเป๋ากันขโมย', usage=dict(seconds=24))
        with patch('urllib.request.urlopen', return_value=Response(json.dumps(result).encode())) as send:
            parsed = transcribe_once(np.zeros(16000), 'secret-example')
            self.assertEqual(send.call_count, 1)
            self.assertEqual(parsed['text'], result['text'])
            self.assertFalse(parsed['word_timestamps_available'])

    def test_failure_does_not_retry_or_echo_remote_secrets(self):
        error = urllib.error.HTTPError('https://api.openai.com/v1/audio/transcriptions', 401, 'secret-example', {}, None)
        with patch('urllib.request.urlopen', side_effect=error) as send:
            with self.assertRaises(RuntimeError) as raised:
                transcribe_once(np.zeros(16000), 'secret-example')
            self.assertNotIn('secret-example', str(raised.exception))
            self.assertEqual(send.call_count, 1)

    def test_text_disagreement_is_not_claimed_as_accuracy(self):
        result = text_differences('กระเป๋า กันขโมย', 'กระเป๋ากันขามวย')
        self.assertTrue(result['differences'])
        self.assertIn('not accuracy', result['interpretation'])


if __name__ == '__main__':
    unittest.main()
