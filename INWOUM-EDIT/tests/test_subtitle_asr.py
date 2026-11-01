import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import engine,subtitle_asr as asr,subtitle_mode as sub
from audio_io import write_pcm

class LocalASRTests(unittest.TestCase):
    def fixture(self,root):
        source=root/'input.wav';pcm=np.zeros(4*16000,dtype=np.int16);write_pcm(source,pcm)
        folder=root/'job';folder.mkdir();write_pcm(folder/'timeline.wav',pcm)
        progress=engine.Progress(folder,lambda **e:None,lambda:False)
        transcript=[dict(start=.1,end=1,text='ทดสอบครับ',words=[dict(start=.1,end=1,text='ทดสอบครับ')])]
        return source,pcm,folder,progress,transcript

    def test_all_local_providers_dispatch_and_never_use_capcut_or_paid_api(self):
        for provider in asr.MODELS:
            with self.subTest(provider=provider),tempfile.TemporaryDirectory() as tmp:
                source,pcm,folder,p,transcript=self.fixture(Path(tmp))
                cfg=dict(subtitle_source=str(source),subtitle_provider=provider)
                with patch('subtitle_asr.ensure_cpp',return_value=(Path('cli'),Path('model'))),patch('subtitle_asr.cpp_run',return_value=transcript) as cpp,patch('subtitle_asr.transcribe_gpu',return_value=transcript) as gpu,patch('capcut_asr.run',side_effect=AssertionError('wrong provider')),patch('planner.plan_once',side_effect=AssertionError('No paid API')):
                    result=sub.run(cfg,folder)
                    self.assertEqual(result['provider'],provider)
                    self.assertEqual(result['openai_calls'],0)
                    if provider=='whisper-cpp':cpp.assert_called_once();gpu.assert_not_called()
                    else:
                        self.assertEqual(gpu.call_args.args[1],asr.MODELS[provider]);cpp.assert_not_called()
                        if provider=='whisper-large':self.assertEqual(gpu.call_args.kwargs['compute_type'],'int8_float16')
                        else:self.assertNotIn('compute_type',gpu.call_args.kwargs)
                    sub.run(cfg,folder)
                    self.assertEqual(cpp.call_count+gpu.call_count,1)

    def test_changed_audio_or_model_never_uses_wrong_cached_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,pcm,folder,p,transcript=self.fixture(Path(tmp))
            with patch('subtitle_asr.transcribe_gpu',return_value=transcript) as gpu:
                asr.run(pcm,folder,dict(subtitle_provider='whisper-turbo'),p)
                asr.run(pcm,folder,dict(subtitle_provider='whisper-large'),p)
                pcm[0]=1;asr.run(pcm,folder,dict(subtitle_provider='whisper-large'),p)
                self.assertEqual(gpu.call_count,3)

    def test_tampered_transcript_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,pcm,folder,p,transcript=self.fixture(Path(tmp))
            with patch('subtitle_asr.transcribe_gpu',return_value=transcript) as gpu:
                cfg=dict(subtitle_provider='whisper-turbo');asr.run(pcm,folder,cfg,p)
                engine.save(folder/'transcript.json',[dict(start=0,end=1,text='modified',words=[])])
                asr.run(pcm,folder,cfg,p);self.assertEqual(gpu.call_count,2)

    def test_cpp_parser_reads_real_millisecond_token_times_and_filters_special_tokens(self):
        raw=dict(transcription=[dict(text='ทดสอบครับ',offsets={'from':100,'to':1000},tokens=[
            dict(id=50364,text='[_BEG_]'),dict(id=123,text='ทดสอบ',offsets={'from':100,'to':600}),
            dict(id=124,text='ครับ',offsets={'from':600,'to':1000})])])
        result=asr.cpp_transcript(raw)
        self.assertEqual(result[0]['words'][0]['start'],.1)
        self.assertEqual(result[0]['words'][1]['end'],1)
        self.assertEqual(''.join(w['text'] for w in result[0]['words']),'ทดสอบครับ')

    def test_parser_never_fabricates_missing_token_times(self):
        raw=dict(transcription=[dict(text='ทดสอบครับ',offsets={'from':100,'to':1000},tokens=[dict(id=123,text='ทดสอบครับ')])])
        self.assertEqual(asr.cpp_transcript(raw)[0]['words'],[])

    def test_unknown_provider_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,_,_,_,_=self.fixture(Path(tmp))
            with self.assertRaises(ValueError):sub.validate_config(dict(subtitle_source=str(source),subtitle_provider='paid'))

    def test_shared_gpu_loader_keeps_default_and_accepts_explicit_int8(self):
        from audio_io import transcribe_gpu
        for explicit,expected in [(False,'float16'),(True,'int8_float16')]:
            with patch('audio_io.cuda_handles',return_value=[]),patch('ctranslate2.get_cuda_device_count',return_value=1),patch('faster_whisper.WhisperModel') as constructor:
                constructor.return_value.transcribe.return_value=([],None)
                kwargs={'compute_type':'int8_float16'} if explicit else {}
                transcribe_gpu(np.zeros(16000,dtype=np.int16),'fixture',lambda **e:None,**kwargs)
                self.assertEqual(constructor.call_args.kwargs['compute_type'],expected)

    def test_full_int8_contract_does_not_reuse_old_fp16_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,pcm,folder,p,transcript=self.fixture(Path(tmp))
            cfg=dict(subtitle_provider='whisper-large')
            with patch('subtitle_asr.transcribe_gpu',return_value=transcript) as gpu:
                asr.run(pcm,folder,cfg,p)
                record=json.loads((folder/'subtitle-asr.json').read_text('utf8'))
                self.assertEqual(record['contract']['compute_type'],'int8_float16')
                record['contract'].pop('compute_type');engine.save(folder/'subtitle-asr.json',record)
                asr.run(pcm,folder,cfg,p)
                self.assertEqual(gpu.call_count,2)
